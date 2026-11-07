import type * as vscode from "vscode";
import {
  GatewayClient,
  type OpenAIChatMessage,
  type OpenAITool,
  type MCPTool,
  type MCPToolCallResponse,
  type StreamUsage,
  GatewayError,
} from "./gateway-client";
import { AgentToolBridge, type AgentToolRequest } from "./agent-tools";
import { ThinkTagFilter } from "./think-filter";
import {
  createThinkingSequenceId,
  reportChatThinkingProgress,
  ThinkingStreamGate,
} from "./thinking";
import { isReasoningFirstModel } from "./lm-convert";
import { KNOWN_R4_TOOLS, buildR4SystemPrompt } from "./shared-constants";

/**
 * Maximum token budget for conversation history (rough estimate).
 * When exceeded, older tool results are trimmed to keep context window from overflowing.
 */
const MAX_CONTEXT_TOKENS = 128_000;

/**
 * Maximum number of tool call results to retain in history.
 * Older results beyond this count are summarised (not dropped entirely).
 */
const MAX_TOOL_RESULTS = 20;

export interface AgentTurnConfig {
  messages: { role: string; content: string }[];
  model: string;
  enableToolCalls: boolean;
  mode: string;
  autoApprove: boolean;
}

export interface AgentTurnResult {
  content: string;
  toolCalls: AgentToolCallInfo[];
  hasThinking: boolean;
  /** Token usage from the last streamed turn (populated when B13 is enabled). */
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  /** Follow-up suggestion messages (B14). */
  followUp?: string;
}

export interface AgentToolCallInfo {
  toolName: string;
  success: boolean;
  error?: string;
}

interface ToolCallAccum {
  id: string;
  name: string;
  argsJson: string;
}

/** Dedup key for tool calls: (name, argsJson) → result text. */
interface ToolDedupKey {
  name: string;
  argsJson: string;
}

function toolDedupKey(tc: ToolCallAccum): ToolDedupKey {
  return { name: tc.name, argsJson: tc.argsJson || "{}" };
}

function dedupKeyHash(key: ToolDedupKey): string {
  // Simple deterministic hash: encode(name||argsJson)
  const raw = `${key.name}||${key.argsJson}`;
  let hash = 0;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw.charCodeAt(i);
    hash = ((hash << 5) - hash + ch) | 0;
  }
  return `h_${Math.abs(hash).toString(36)}`;
}

/** #214: parse tool-call argument JSON. A parse failure returns argsError so
 *  the engine can reject the call instead of executing with silent "{}" args. */
function parseToolArgs(tc: ToolCallAccum): { args: Record<string, unknown>; argsError?: string } {
  try {
    const parsed = JSON.parse(tc.argsJson || "{}");
    return { args: parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {} };
  } catch (err) {
    return { args: {}, argsError: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Convert KNOWN_R4_TOOLS to OpenAI tool schemas.
 * These act as fallback tools when MCP tools list is empty.
 */
export function knownToolsToSchemas(): OpenAITool[] {
  return KNOWN_R4_TOOLS.map((t) => ({
    type: "function",
    function: {
      name: t.name,
      description: t.description,
      parameters: { type: "object", properties: {} },
    },
  }));
}

export class AgentEngine {
  private stream: vscode.ChatResponseStream | null = null;
  private thinkingId: string;
  private thinkingText = "";
  private thinkingHasEmitted = false;
  private lastToolCallName: string | undefined;

  constructor(
    private readonly client: GatewayClient,
    private readonly toolBridge: AgentToolBridge | undefined,
    private readonly mcpTools: MCPTool[],
    private readonly workspaceTools: { name: string; description: string }[],
    private readonly autoApprove: boolean,
    private readonly onToolCall: (toolName: string) => void,
    private readonly onToolResult: (
      toolName: string,
      success: boolean,
      error?: string,
    ) => void,
  ) {
    this.thinkingId = createThinkingSequenceId();
  }

  async runTurn(
    config: AgentTurnConfig,
    token: vscode.CancellationToken,
    stream: vscode.ChatResponseStream,
  ): Promise<AgentTurnResult> {
    this.stream = stream;
    this.lastToolCallName = undefined;
    let capturedUsage: StreamUsage | undefined;

    const openaiMessages: OpenAIChatMessage[] = config.messages.map((m) => ({
      role: m.role as OpenAIChatMessage["role"],
      content: m.content,
    }));

    // Add system prompt with tool list (grounding prevents hallucination)
    if (!config.messages.some((m) => m.role === "system")) {
      const systemPrompt = buildR4SystemPrompt(this.mcpTools, this.workspaceTools);
      openaiMessages.unshift({ role: "system", content: systemPrompt });
    }

    // In plan mode, add plan override system prompt and disable tools
    if (config.mode === "plan") {
      openaiMessages.push({
        role: "system" as const,
        content:
          "Plan mode is active. This turn is planning only — no tools, files, or side effects.\n" +
          "Return a structured plan (steps, assumptions, risks, open questions).\n" +
          "Do not present the work as completed and do not imply anything was executed.\n" +
          "For open-ended or brainstorming requests, frame items as a proposed roadmap, not a final deliverable.",
      });
    }

    // Build tool schemas — MUST include KNOWN_R4_TOOLS fallback
    let toolsEnabled = config.enableToolCalls;
    let toolSchemas: OpenAITool[] | undefined;
    if (toolsEnabled) {
      const tools: OpenAITool[] = [];
      for (const t of this.mcpTools) {
        tools.push({
          type: "function",
          function: {
            name: t.name,
            description: t.description,
            parameters:
              (t.inputSchema as Record<string, unknown>) ?? {
                type: "object",
                properties: {},
              },
          },
        });
      }
      for (const t of this.workspaceTools) {
        tools.push({
          type: "function",
          function: {
            name: t.name,
            description: t.description,
            parameters: { type: "object", properties: {} },
          },
        });
      }
      // ALWAYS include KNOWN_R4_TOOLS when tool calls are enabled
      tools.push(...knownToolsToSchemas());
      toolSchemas = tools;
    }

    let assistantMessages: OpenAIChatMessage[] = [];
    let toolCallRound = 0;
    const maxToolRounds = 10;
    const toolCallInfos: AgentToolCallInfo[] = [];

    // B9: Tool-call dedup — hash(name, argsJson) → result text.
    const seenToolCalls = new Map<string, string>();

    // B8: Context window budget tracking (rough token estimate).
    let contextTokenBudget = estimateTokens(openaiMessages);
    const toolResultsStore: string[] = [];  // Separate store for trimming

    // B10: Track whether we've already retried without tools.
    let toolsRetryAttempted = false;

    // Buffer ALL output and render once at end to avoid double/chunked output
    const outputBuffer: string[] = [];

    // B14: follow-up suggestion (declared before loop so return can access it)
    let followUp: string | undefined;

    while (toolCallRound < maxToolRounds) {
      if (token.isCancellationRequested) break;

      // B8: Trim context if budget exceeded — keep system prompt + recent messages only.
      if (contextTokenBudget > MAX_CONTEXT_TOKENS) {
        assistantMessages = trimAssistantMessages(assistantMessages);
        contextTokenBudget = estimateTokens(openaiMessages.concat(assistantMessages));
        if (toolCallRound > 0) {
          this.stream?.progress("📝 Trimming conversation history to fit context window…");
        }
      }

      const requestMessages: OpenAIChatMessage[] = [
        ...openaiMessages,
        ...assistantMessages,
      ];

      const controller = new AbortController();
      const cancelListener = token.onCancellationRequested(() => controller.abort());

      const reasoningFirst = isReasoningFirstModel(config.model);
      let sawSeparateReasoning = false;
      let firstContent = true;

      const accumulatedToolCalls: ToolCallAccum[] = [];
      let tcIndex = 0;
      const roundContent: string[] = [];

      const thinkingGate = new ThinkingStreamGate();
      const thinkFilter = new ThinkTagFilter((text) => {
        this.thinkingText += text;
        this.thinkingHasEmitted = true;
        reportChatThinkingProgress(this.stream!, text, this.thinkingId);
      });

      // B10: Track stream errors for retry.
      let streamError: Error | undefined;

      const chatReq: Parameters<GatewayClient["chatCompletionStream"]>[0] = {
        model: config.model || "",
        messages: requestMessages,
      };
      // B13: request usage metadata at end of stream.
      if (toolsEnabled && toolSchemas?.length) {
        chatReq.stream_options = { include_usage: true };
        chatReq.tools = toolSchemas;
        chatReq.tool_choice = "auto";
      }

      try {
        for await (const delta of this.client.chatCompletionStream(
            chatReq,
            controller.signal,
          )) {
            if (token.isCancellationRequested) break;

            // Capture usage from final delta (set when include_usage is true).
            if (delta.usage) {
              capturedUsage = delta.usage;
            }

            // Tool call delta — buffer, don't render yet
            if (delta.tool_calls && delta.tool_calls.length > 0) {
              for (const tc of delta.tool_calls) {
                if (tc.function?.name) {
                  const acc = accumulatedToolCalls[tcIndex] ?? {
                    id: tc.id ?? `tc_${toolCallRound}_${tcIndex}`,
                    name: tc.function.name,
                    argsJson: "",
                  };
                  if (tc.id) acc.id = tc.id;
                  if (tc.function.name) acc.name = tc.function.name;
                  if (!accumulatedToolCalls[tcIndex]) accumulatedToolCalls[tcIndex] = acc;
                  this.onToolCall(acc.name);
                }
                if (tc.function?.arguments) {
                  if (!accumulatedToolCalls[tcIndex]) {
                    accumulatedToolCalls[tcIndex] = {
                      id: tc.id ?? `tc_${toolCallRound}_${tcIndex}`,
                      name: "",
                      argsJson: tc.function.arguments,
                    };
                  } else {
                    accumulatedToolCalls[tcIndex].argsJson += tc.function.arguments;
                  }
                }
                if (tc.index !== undefined && tc.index >= 0) {
                  tcIndex = Math.max(tcIndex, tc.index + 1);
                }
              }
              continue;
            }

            // Content delta — buffer
            if (delta.content) {
              if (firstContent && reasoningFirst && !sawSeparateReasoning) {
                thinkFilter.beginInThinking();
              }
              firstContent = false;
              const visible = thinkFilter.push(delta.content);
              if (visible) {
                thinkingGate.markAnswerStarted();
                roundContent.push(visible);
              }
            }

            // Reasoning delta
            if (delta.reasoning) {
              sawSeparateReasoning = true;
              thinkingGate.reportThinking(
                (t) => reportChatThinkingProgress(this.stream!, t, this.thinkingId),
                delta.reasoning,
              );
            }
          }
        } catch (err) {
          // B10: Only retry on 400 errors that are truly tool-rejection errors.
          if (err instanceof GatewayError && err.status === 400) {
            const body = err.body || err.message || "";
            const isToolRejection =
              body.includes("tool") ||
              body.includes("function") ||
              body.includes("tool_choice") ||
              body.includes("tools");
            if (isToolRejection) {
              streamError = err;
            } else {
              // Not a tool-rejection — surface the actual error and stop.
              const errMsg = err.message || String(err);
              roundContent.push(`\n> ❌ *Request error: ${errMsg}*\n`);
              // Force break — no retry possible for this error type.
              streamError = err;
              // Don't attempt tool-rejection fallback for non-tool errors.
              throw new NonToolError(errMsg);
            }
          } else {
            // Other errors — format and continue.
            const errMsg = err instanceof Error ? err.message : String(err);
            roundContent.push(`\n> ❌ *Streaming error: ${errMsg}*\n`);
          }
        } finally {
          cancelListener.dispose();
        }

        // Flush any remaining thinking text.
        const tail = thinkFilter.flush();
        if (tail) {
          thinkingGate.markAnswerStarted();
          roundContent.push(tail);
        }

      // B10: If stream errored (400 tool rejection), retry without tools.
      if (streamError && !(streamError instanceof NonToolError)) {
        if (toolsEnabled && !toolsRetryAttempted) {
          toolsRetryAttempted = true;
          const retryResult = await retryWithoutTools(
            this.client,
            config,
            requestMessages,
            this.stream!,
            token,
          );
          if (retryResult) {
            outputBuffer.push(retryResult);
            followUp = "Retry succeeded without tools. Would you like me to do anything else?";
            break;
          }
        }
        // No more retries — break and return what we have.
        break;
      }

if (accumulatedToolCalls.length > 0) {
        const toolCallMsg: OpenAIChatMessage = {
          role: "assistant",
          content: null,
          tool_calls: accumulatedToolCalls.map((tc) => ({
            id: tc.id,
            type: "function",
            function: { name: tc.name, arguments: tc.argsJson || "{}" },
          })),
        };
        assistantMessages.push(toolCallMsg);

        const toolResults: string[] = [];
        for (const tc of accumulatedToolCalls) {
          if (token.isCancellationRequested) break;

          // B9: Dedup check — skip if identical call already executed.
          const key = toolDedupKey(tc);
          const hash = dedupKeyHash(key);
          const priorResult = seenToolCalls.get(hash);
          if (priorResult !== undefined) {
            this.stream?.progress(`Reusing prior result for \`${tc.name}\`…`);
            toolResults.push(
              `> 🔄 Tool \`${tc.name}\` (repeated call — using prior result)\n` +
              `> \`\`\`\n${priorResult}\n> \`\`\``,
            );
            toolCallInfos.push({ toolName: tc.name, success: true, error: undefined });
            assistantMessages.push({
              role: "tool",
              tool_call_id: tc.id,
              content: priorResult,
            });
            continue;
          }

          // B12: Emit progress so the user sees what's happening.
          this.stream?.progress(`Executing \`${tc.name}\`...`);

          let resultText = "";
          let success = false;
          let error: string | undefined;

          // #214: malformed tool-args JSON must NOT execute with "{}".
          const { args, argsError } = parseToolArgs(tc);
          if (argsError) {
            error = `malformed tool-call arguments JSON: ${argsError}`;
            resultText =
              `❌ Tool \`${tc.name}\` rejected: unreadable arguments JSON (${argsError})\n\n` +
              `*The model emitted malformed tool arguments and the call was NOT executed.*\n` +
              `Re-emit the same tool call with valid JSON arguments.`;
            success = false;
          } else try {
            const wsTool = this.workspaceTools.find((t) => t.name === tc.name);
            if (wsTool && this.toolBridge) {
              try {
                const request: AgentToolRequest = {
                  sessionId: "r4-extension-session",
                  requestId: `workspace-${tc.name}-${Date.now()}`,
                  toolCallId: `ws-${tc.name}`,
                  name: tc.name,
                  arguments: args,
                  autoApprove: config.autoApprove,
                };
                const result = await this.toolBridge.call(request);
                resultText =
                  typeof result === "string"
                    ? result
                    : JSON.stringify(result, null, 2);
                success = true;
              } catch (err) {
                error = err instanceof Error ? err.message : String(err);
                resultText = `Error executing ${tc.name}: ${error}`;
                success = false;
              }
            } else {
              try {
                const mcpResult = await this.client.callMCPTool({
                  tool: tc.name,
                  arguments: args,
                });
                resultText = this.normalizeMcpResult(mcpResult);
                success = !mcpResult.isError;
              } catch (err) {
                error = err instanceof Error ? err.message : String(err);
                // B11: Structured failure message — keep loop alive.
                resultText =
                  `❌ Tool \`${tc.name}\` failed: ${error}\n\n` +
                  `*The tool execution errored, but the agent loop continues.*\n` +
                  `The model can adjust arguments or try a different approach.`;
                success = false;
              }
            }
          } catch (err) {
            error = err instanceof Error ? err.message : String(err);
            // B11: Structured failure message — keep loop alive.
            resultText =
              `❌ Tool \`${tc.name}\` failed: ${error}\n\n` +
              `*The tool execution errored, but the agent loop continues.*\n` +
              `The model can adjust arguments or try a different approach.`;
            success = false;
          }

          // B9: Store result in dedup map (always, even on failure).
          if (resultText.length < 5000) {
            seenToolCalls.set(hash, resultText);
          }

          // B8: Store in separate context store for trimming.
          toolResultsStore.push(resultText);

          this.onToolResult(tc.name, success, error);
          toolCallInfos.push({ toolName: tc.name, success, error });

          // B12: Mark progress as complete.
          this.stream?.progress(success ? `Done: \`${tc.name}\`` : `Error: \`${tc.name}\` — ${error}`);

          if (success) {
            const truncated =
              resultText.length > 2000
                ? resultText.slice(0, 2000) + "…"
                : resultText;
            toolResults.push(
              `> ✅ Tool \`${tc.name}\` executed\n> \`\`\`\n${truncated}\n> \`\`\``,
            );
          } else {
            // B11: Show structured failure with recovery guidance.
            toolResults.push(
              `> ❌ Tool \`${tc.name}\` failed\n` +
              `> \`\`\`\n${resultText}\n> \`\`\``,
            );
          }

          assistantMessages.push({
            role: "tool",
            tool_call_id: tc.id,
            content: resultText,
          });

          this.lastToolCallName = tc.name;
        }

        toolCallRound++;

        // Buffer tool results
        outputBuffer.push(...toolResults);
        continue;
      }

      // Final text response — add to buffer
      if (this.lastToolCallName && !roundContent.length) {
        outputBuffer.push(
          `\n> Tool execution complete. Review results above.`,
        );
      }

      if (!roundContent.length && this.lastToolCallName === undefined) break;

      // Add accumulated content to buffer
      outputBuffer.push(...roundContent);

      // B14: Generate follow-up suggestion based on turn outcome.
      if (toolCallInfos.length > 0) {
        const failures = toolCallInfos.filter((t) => !t.success);
        if (failures.length > 0) {
          const failedNames = failures.map((f) => f.toolName);
          followUp = `Some tools failed (${failedNames.join(", ")}). Would you like me to try again with adjusted arguments or a different approach?`;
        } else {
          followUp = "Task complete. Would you like me to do anything else?";
        }
      } else if (roundContent.length) {
        followUp = "Anything else you'd like me to help with?";
      }
      break;
    }

    // Buffer all output — chat-participant renders once to avoid VSCode duplication.
    const fullOutput = outputBuffer.join("\n\n");
    return {
      content: fullOutput || "",
      toolCalls: toolCallInfos,
      hasThinking: this.thinkingHasEmitted,
      usage: capturedUsage,
      followUp,
    };
  }

  /** Normalize MCP tool call response to a text string */
  private normalizeMcpResult(result: MCPToolCallResponse): string {
    if (result.isError) {
      if (Array.isArray(result.content)) {
        for (const block of result.content) {
          if (
            typeof block === "object" &&
            block !== null &&
            "text" in block
          ) {
            return `Error: ${String((block as Record<string, unknown>).text)}`;
          }
        }
      }
      return `Error: ${String(result.content)}`;
    }

    if (Array.isArray(result.content)) {
      for (const block of result.content) {
        if (
          typeof block === "object" &&
          block !== null &&
          "type" in block &&
          "text" in block
        ) {
          return (block as Record<string, unknown>).text as string;
        }
      }
      return JSON.stringify(result.content, null, 2);
    }

    if (typeof result.content === "string") return result.content;
    return JSON.stringify(result.content);
  }
}

// >-- Context management helpers (B8) ----------------------------------------

/**
 * Marker error: 400 received but NOT a tool-rejection error.
 * Prevents the B10 fallback from triggering.
 */
class NonToolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NonToolError";
  }
}

/**
 * Rough token estimator. Not exact — enough for budget tracking.
 * Rule of thumb: 1 token ≈ 4 chars for English text.
 */
function estimateTokens(messages: OpenAIChatMessage[]): number {
  let charCount = 0;
  for (const m of messages) {
    charCount += m.content?.length ?? 0;
    if (m.tool_calls) {
      for (const tc of m.tool_calls) {
        charCount += (tc.function?.arguments?.length ?? 0);
      }
    }
  }
  return Math.max(0, Math.floor(charCount / 4));
}

/**
 * Trim assistant messages when context budget is exceeded.
 * Keeps system prompt + user messages + recent assistant/tool pairs.
 * Drops/summarises older tool results (B8).
 */
function trimAssistantMessages(
  assistantMessages: OpenAIChatMessage[],
): OpenAIChatMessage[] {
  // Group into (assistant + tool_results) pairs
  const pairs: OpenAIChatMessage[][] = [];
  let current: OpenAIChatMessage[] = [];

  for (const msg of assistantMessages) {
    if (msg.role === "assistant" && msg.tool_calls) {
      current = [msg];
    } else if (msg.role === "tool" && current.length > 0) {
      current.push(msg);
      pairs.push(current);
      current = [];
    } else if (msg.role === "assistant") {
      // Text-only assistant message (shouldn't happen in agent loop, but keep it)
      current = [msg];
      pairs.push(current);
      current = [];
    }
  }

  // Keep last N pairs (B8: MAX_TOOL_RESULTS)
  const keep = pairs.slice(-MAX_TOOL_RESULTS);

  // Summarise dropped pairs into a single message
  if (pairs.length > MAX_TOOL_RESULTS) {
    const droppedCount = pairs.length - MAX_TOOL_RESULTS;
    const summaryContent =
      `*Context trimmed: ${droppedCount} prior tool execution(s) summarised for brevity.*\n\n` +
      `*(Earlier tool results were dropped to fit the context window. ` +
      `The agent has already received and processed those results.)*`;
    const summaryMsg: OpenAIChatMessage = {
      role: "assistant" as const,
      content: summaryContent,
    };
    return [summaryMsg, ...keep.flat()];
  }

  return keep.flat();
}

// >-- Tool rejection fallback (B10) ------------------------------------------

/**
 * Retry the LLM call without tools after a 400 error.
 * Returns true if a retry was performed.
 */
async function retryWithoutTools(
  client: GatewayClient,
  config: AgentTurnConfig,
  openaiMessages: OpenAIChatMessage[],
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<string | null> {
  stream.markdown(
    "> ⚠️ *Model rejected tool schemas — retrying without tools…*\n",
  );

  try {
    const controller = new AbortController();
    const cancelListener = token.onCancellationRequested(() => controller.abort());
    const results: string[] = [];

    for await (const delta of client.chatCompletionStream(
      { model: config.model || "", messages: openaiMessages },
      controller.signal,
    )) {
      if (token.isCancellationRequested) break;
      if (delta.content) results.push(delta.content);
      if (delta.reasoning) {
        // Route reasoning to thinking UI
      }
    }
    cancelListener.dispose();

    return results.join("");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    stream.markdown(`> ❌ *Retry failed: ${errMsg}*\n`);
    return null;
  }
}
