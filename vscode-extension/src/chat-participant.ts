/**
 * R4 Chat Participant - registers @r4 in VS Code Chat.
 *
 * Agent-Engine integration (B2):
 *  - AgentEngine owns the agentic loop (LLM > tools > execute > feedback)
 *  - chat-participant renders ONE response (B4: single-render invariant)
 *  - Plan mode state machine (B6)
 *  - Capability prompt merging (B5)
 *  - Token usage (B13), follow-up suggestions (B14)
 *  - Clickable tool-call anchors (B15)
 */

import * as vscode from "vscode";
import {
  GatewayClient,
  GatewayError,
  type ChatMessage,
  type MCPTool,
} from "./gateway-client";
import {
  getCompanionCapabilities,
  getCompanionSystemPrompt,
} from "./views/companion";
import { AgentEngine, type AgentTurnConfig } from "./agent-engine";
import { AgentToolBridge } from "./agent-tools";
import { createThinkingSequenceId, reportChatThinkingProgress, ThinkingStreamGate } from "./thinking";

const PARTICIPANT_ID = "r4.chat";

/** VS Code's chat UI shows "Generating..." during streaming. stream.progress() replaces it. */
function setProgress(stream: vscode.ChatResponseStream, msg: string): void {
  stream.progress(msg || "");
}

// >-- Plan mode helpers ----------------------------------------------------

/** Build the plan-mode system prompt (B5/B6). */
function planModeSystemPrompt(): string {
  return (
    "You are a Research Agent. The user is asking you to perform a plan of work. " +
    "Output a concise, numbered plan with at most 3-4 steps. " +
    "At the end of your plan, add the following line exactly (do not modify it):\n\n" +
    "### USER_ACTION_REQUIRED\n" +
    "Reply " + String.fromCharCode(34) + "yes" + String.fromCharCode(34) + " to proceed with this plan, or " + String.fromCharCode(34) + "no" + String.fromCharCode(34) + " to suggest changes."
  );
}

/** Build the plan-mode footer shown after a plan turn (B4). */
function planModeFooter(): string {
  return "### Plan Ready\n\n" +
    "Reply " + String.fromCharCode(34) + "yes" + String.fromCharCode(34) + " to proceed with the plan or " + String.fromCharCode(34) + "no" + String.fromCharCode(34) + " to suggest changes.";
}

// >-- Plan-state resolution ------------------------------------------------

/**
 * Resolve plan-state: computes whether a plan override is needed and
 * whether tool calls should be enabled. Shared between handleChat and runCompanionChat.
 *
 * Exported for plan-mode state machine testing.
 */
export function resolvePlanState(interactionMode: string | undefined, planModeState: any): {
  planOverride: string | undefined;
  enableToolCalls: boolean;
} {
  let planOverride: string | undefined;
  let enableToolCalls = true;

  if (interactionMode === "plan") {
    const ps = planModeState;
    if (ps && ps.awaiting_confirmation) {
      planOverride = planModeSystemPrompt();
      enableToolCalls = false;
    } else {
      enableToolCalls = false;
    }
  }

  return { planOverride, enableToolCalls };
}

// >-- Session state --------------------------------------------------------

interface R4SessionState {
  interaction_mode?: "plan" | "normal";
  plan_mode_state?: { awaiting_confirmation: boolean; plan_text?: string };
  auto_approve_tools?: boolean;
  capability_prompt?: string;
  /** Selected model for chat turns (B20.1) */
  selected_model?: string;
}

// >-- Main class -----------------------------------------------------------

export class R4ChatParticipant implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];
  /** Stores the last agent turn result for B15 detail view. */
  private _lastResult: import("./agent-engine").AgentTurnResult | null = null;
  private sessionState: R4SessionState = {};
  private toolBridge: AgentToolBridge | undefined;

  constructor(private readonly getClient: () => GatewayClient | undefined) {
    const participant = vscode.chat.createChatParticipant(
      PARTICIPANT_ID,
      this.handleRequest.bind(this),
    );
    participant.iconPath = vscode.Uri.joinPath(
      vscode.extensions.getExtension("rccs.r4-platform")?.extensionUri ??
        vscode.Uri.file(""),
      "resources",
      "r4-icon.svg",
    );
    this.disposables.push(participant);
  }

  // >-- Main request handler -----------------------------------------

  private async handleRequest(
    request: vscode.ChatRequest,
    context: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
  ): Promise<vscode.ChatResult> {
    const client = this.getClient();
    if (!client) {
      stream.markdown(
        "R4 Gateway is not configured. Run **R4: Login to Gateway** first.",
      );
      return {};
    }

    try {
      switch (request.command) {
        case "models":
          return await this.handleModels(client, stream);
        case "endpoints":
          return await this.handleEndpoints(client, stream);
        case "jobs":
          return await this.handleJobs(client, stream);
        case "tools":
          return await this.handleTools(client, stream);
        case "workflows":
          return await this.handleWorkflows(client, stream);
        case "companion":
          return await this.handleCompanion(
            client, request, context, stream, token,
          );
        case "stats":
          return await this.handleStats(context, stream);
        case "model":
          return await this.handleModelPicker(client, stream);
        case "history":
          return await this.handleHistorySearch(client, request, context, stream);
        default:
          return await this.handleChat(client, request, context, stream, token);
      }
    } catch (err) {
      return this.handleError(err, stream);
    }
  }

  // >-- Chat -----------------------------------------------------------

  private async handleChat(
    client: GatewayClient,
    request: vscode.ChatRequest,
    context: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
  ): Promise<vscode.ChatResult> {
    // Replace VS Code's default "Generating..." with a custom indicator.
    setProgress(stream, "R4 Agent is working...");

    // B2: Build messages from chat context.
    const messages: ChatMessage[] = [];
    for (const turn of context.history) {
      if (turn instanceof vscode.ChatRequestTurn) {
        messages.push({ role: "user", content: turn.prompt });
      } else if (turn instanceof vscode.ChatResponseTurn) {
        const parts: string[] = [];
        for (const part of turn.response) {
          if (part instanceof vscode.ChatResponseMarkdownPart) {
            parts.push(part.value.value);
          }
        }
        if (parts.length > 0) {
          messages.push({ role: "assistant", content: parts.join("") });
        }
      }
    }
    messages.push({ role: "user", content: request.prompt });

    // B20.1: Use explicitly selected model if set.
    let model = this.sessionState.selected_model ?? "";
    if (!model) {
      try {
        const endpoints = await client.listEndpoints();
        const ready = endpoints.find(
          (ep) => ep.status.toLowerCase() === "ready",
        );
        if (ready) {
          model = ready.model || "";
        } else if (endpoints.length === 0) {
          stream.markdown("No active endpoints. Start one first:\n\n");
          stream.button({ command: "r4.endpointUp", title: "$(play) Provision Endpoint" });
          return {};
        }
      } catch {
        // proceed without model override
      }
    }

    // B20.4: Multi-file context from open tabs.
    const openTabContexts = await this.collectOpenTabContexts();
    if (openTabContexts.length > 0) {
      messages.push({ role: "system", content: openTabContexts });
    }

    // Resolve plan state.
    const planState = resolvePlanState(
      this.sessionState.interaction_mode,
      this.sessionState.plan_mode_state,
    );

    // Create engine per-turn (B2).
    const engine = new AgentEngine(
      client,
      this.toolBridge,
      [], // MCP tools
      [], // workspace tools
      this.sessionState.auto_approve_tools ?? false,
      (toolName) => { stream.progress("Executing " + toolName + "..."); },
      (toolName, success) => { stream.progress(success ? "Done: " + toolName : "Error: " + toolName); },
    );

    const config: AgentTurnConfig = {
      messages,
      model,
      enableToolCalls: planState.enableToolCalls,
      mode: planState.planOverride ? "plan" : "normal",
      autoApprove: this.sessionState.auto_approve_tools ?? false,
    };

    // Add plan override system prompt.
    if (planState.planOverride) {
      config.messages.push({ role: "system", content: planState.planOverride });
    }

    const result = await engine.runTurn(config, token, stream);
    this._lastResult = result;

    // B4: Plan-mode footer.
    if (planState.planOverride && !result.content && result.toolCalls.length === 0) {
      stream.markdown(planModeFooter());
    }

    // B14: Follow-up suggestions.
    if (result.followUp) {
      stream.markdown("\n\n" + result.followUp);
    }

    // B13: Token usage.
    if (result.usage) {
      stream.markdown(
        "\n\n> " +
        "Token usage: " + result.usage.prompt_tokens.toLocaleString() +
        " prompt, " + result.usage.completion_tokens.toLocaleString() +
        " completion, " + result.usage.total_tokens.toLocaleString() + " total.",
      );
    }

    // B15: Clickable tool call badges via markdown links.
    const seenToolNames = new Set<string>();
    for (const tc of result.toolCalls) {
      if (seenToolNames.has(tc.toolName)) continue;
      seenToolNames.add(tc.toolName);
      const status = tc.success ? "\u2705" : "\u274c";
      stream.markdown("[ " + status + " " + tc.toolName + " ](command:r4.agent.viewToolCall?" + encodeURIComponent(JSON.stringify([tc.toolName])) + ") ");
    }

    // Handle plan confirmation (B6).
    if (planState.planOverride && result.content) {
      const userResponse = request.prompt.trim().toLowerCase();
      if (userResponse === "yes" || userResponse === "y") {
        this.sessionState.plan_mode_state = { awaiting_confirmation: false };
        this.sessionState.interaction_mode = "normal";
        stream.markdown("Executing plan...\n\n");
        return await this.handleChat(client, request, context, stream, token);
      } else if (userResponse === "no" || userResponse === "n") {
        this.sessionState.plan_mode_state = { awaiting_confirmation: false };
        this.sessionState.interaction_mode = "normal";
        stream.markdown("Plan cancelled. You can try again.");
        return {};
      }
    }

    // Clear custom progress before the response finishes.
    setProgress(stream, "");

    return {};
  }

  // >-- /companion -----------------------------------------------------

  private async handleCompanion(
    client: GatewayClient,
    request: vscode.ChatRequest,
    context: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
  ): Promise<vscode.ChatResult> {
    const prompt = request.prompt.trim();
    if (!prompt) {
      stream.markdown("### Research Companion\n\n");
      stream.markdown("Use " + String.fromCharCode(96) + "/companion <capability> <question>" + String.fromCharCode(96) + " to start...\n\n");
      stream.markdown("| Capability | Description |\n|------------|-------------|\n");
      for (const cap of getCompanionCapabilities()) {
        stream.markdown("| " + String.fromCharCode(96) + cap.id + String.fromCharCode(96) + " | " + cap.description + " |\n");
      }
      stream.markdown("...");
      return {};
    }

    const spaceIdx = prompt.indexOf(" ");
    const capabilityId = spaceIdx > 0 ? prompt.slice(0, spaceIdx) : prompt;
    const question = spaceIdx > 0 ? prompt.slice(spaceIdx + 1).trim() : "";

    const systemPrompt = getCompanionSystemPrompt(capabilityId);

    if (!systemPrompt) {
      return this.runCompanionChat(
        client, context, stream, token,
        "You are a research companion — an AI assistant specialized in " +
          "helping HPC researchers with literature review, data analysis, " +
          "experiment design, code review, and cluster job management. " +
          "Provide thorough, scientifically rigorous responses.",
        prompt,
      );
    }

    if (!question) {
      stream.markdown("**" + capabilityId + "** mode activated. Ask your research question.\n\n");
      return {};
    }

    return this.runCompanionChat(
      client, context, stream, token, systemPrompt, question,
    );
  }

  private async runCompanionChat(
    client: GatewayClient,
    context: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
    systemPrompt: string,
    userMessage: string,
  ): Promise<vscode.ChatResult> {
    const model = vscode.workspace.getConfiguration("r4").get<string>("defaultModel", "");

    const messages: ChatMessage[] = [{ role: "system", content: systemPrompt }];
    for (const turn of context.history) {
      if (turn instanceof vscode.ChatRequestTurn) {
        messages.push({ role: "user", content: turn.prompt });
      } else if (turn instanceof vscode.ChatResponseTurn) {
        const parts: string[] = [];
        for (const part of turn.response) {
          if (part instanceof vscode.ChatResponseMarkdownPart) {
            parts.push(part.value.value);
          }
        }
        if (parts.length > 0) {
          messages.push({ role: "assistant", content: parts.join("") });
        }
      }
    }
    messages.push({ role: "user", content: userMessage });

    const planState = resolvePlanState(
      this.sessionState.interaction_mode,
      this.sessionState.plan_mode_state,
    );

    this.sessionState.capability_prompt = systemPrompt;

    const engine = new AgentEngine(
      client, this.toolBridge, [], [],
      this.sessionState.auto_approve_tools ?? false,
      (toolName) => stream.progress("Executing " + toolName + "..."),
      (toolName, success) => stream.progress(success ? "Done: " + toolName : "Error: " + toolName),
    );

    const config: AgentTurnConfig = {
      messages, model,
      enableToolCalls: planState.enableToolCalls,
      mode: planState.planOverride ? "plan" : "normal",
      autoApprove: this.sessionState.auto_approve_tools ?? false,
    };

    if (planState.planOverride) {
      config.messages.push({ role: "system", content: planState.planOverride });
    }

    const result = await engine.runTurn(config, token, stream);
    this._lastResult = result;

    // B4/B6: Plan footer.
    if (planState.planOverride && !result.content && result.toolCalls.length === 0) {
      stream.markdown(planModeFooter());
    }

    // B14: Follow-up.
    if (result.followUp) { stream.markdown("\n\n" + result.followUp); }

    // B13: Token usage.
    if (result.usage) {
      stream.markdown("\n\n> Token usage: " + result.usage.prompt_tokens.toLocaleString() + " prompt, " + result.usage.completion_tokens.toLocaleString() + " completion, " + result.usage.total_tokens.toLocaleString() + " total.");
    }

    // B15: Markdown-link badges.
    const seen = new Set<string>();
    for (const tc of result.toolCalls) {
      if (seen.has(tc.toolName)) continue;
      seen.add(tc.toolName);
      stream.markdown("[ " + (tc.success ? "\u2705" : "\u274c") + " " + tc.toolName + " ](command:r4.agent.viewToolCall?" + encodeURIComponent(JSON.stringify([tc.toolName])) + ") ");
    }

    // Plan confirmation.
    if (planState.planOverride && result.content) {
      const lastTurn = context.history[context.history.length - 1];
      const prompt = (lastTurn instanceof vscode.ChatRequestTurn) ? lastTurn.prompt.trim().toLowerCase() : "";
      if (prompt === "yes" || prompt === "y") {
        this.sessionState.plan_mode_state = { awaiting_confirmation: false };
        this.sessionState.interaction_mode = "normal";
        stream.markdown("Executing plan...\n\n");
        return await this.runCompanionChat(client, context, stream, token, systemPrompt, userMessage);
      } else if (prompt === "no" || prompt === "n") {
        this.sessionState.plan_mode_state = { awaiting_confirmation: false };
        this.sessionState.interaction_mode = "normal";
        stream.markdown("Plan cancelled.");
        return {};
      }
    }

    return {};
  }

  // >-- /models --------------------------------------------------------

  private async handleModels(
    client: GatewayClient, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    const models = await client.listModels();
    if (models.length === 0) {
      stream.markdown("No models found in the UMRS registry.");
      return {};
    }
    stream.markdown("### Available Models\n\n");
    stream.markdown("| Name | Scope | Type |\n|------|-------|------|\n");
    for (const m of models) {
      stream.markdown("| " + m.name + " | " + m.scope + " | " + (m.model_type ?? "\u2014") + " |\n");
    }
    return {};
  }

  // >-- /endpoints -----------------------------------------------------

  private async handleEndpoints(
    client: GatewayClient, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    const endpoints = await client.listEndpoints();
    if (endpoints.length === 0) {
      stream.markdown("No endpoints registered.");
      return {};
    }
    stream.markdown("### Active Endpoints\n\n");
    stream.markdown("| Job ID | Model | Status |\\n|------|-------|--------|\\n");
    for (const ep of endpoints) {
      stream.markdown("| " + (ep.job_id ?? "unknown") + " | " + (ep.model ?? "unknown") + " | " + (ep.status ?? "unknown") + " |\\n");
    }
    return {};
  }

  // >-- /jobs --------------------------------------------------------

  private async handleJobs(
    client: GatewayClient, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    const jobs = await client.listJobs();
    if (jobs.length === 0) {
      stream.markdown("No jobs found.");
      return {};
    }
    stream.markdown("### Recent Jobs\n\n");
    stream.markdown("| Job ID | Status | Type |\\n|--------|--------|------|\\n");
    for (const j of jobs.slice(0, 10)) {
      stream.markdown("| " + (j.id ?? "unknown") + " | " + (j.status ?? "unknown") + " | " + (j.type ?? "unknown") + " |\\n");
    }
    return {};
  }

  // >-- /tools -------------------------------------------------------

  private async handleTools(
    client: GatewayClient, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    const tools = await client.listMCPTools();
    if (tools.length === 0) {
      stream.markdown("No MCP tools registered.");
      return {};
    }
    stream.markdown("### MCP Tools\n\n");
    stream.markdown("| Name | Description |\n|------|-------------|\n");
    for (const t of tools) {
      stream.markdown("| " + t.name + " | " + (t.description ?? "\u2014") + " |\n");
    }
    return {};
  }

  // >-- /workflows ---------------------------------------------------

  private async handleWorkflows(
    client: GatewayClient, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    const workflows = await client.listWorkflows();
    if (workflows.length === 0) {
      stream.markdown("No workflows registered.");
      return {};
    }
    stream.markdown("### Workflows\n\n");
    stream.markdown("| Name | Description | Tags |\n|------|-------------|------|\n");
    for (const w of workflows) {
      stream.markdown("| " + w.name + " | " + (w.description ?? "\u2014") + " | " + (w.tags?.join(", ") ?? "\u2014") + " |\n");
    }
    return {};
  }

  // >-- /stats -------------------------------------------------------

  private async handleStats(
    context: vscode.ChatContext, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    let inputTokens = 0;
    for (const turn of context.history) {
      if (turn instanceof vscode.ChatRequestTurn) {
        inputTokens += Math.ceil(turn.prompt.length / 4);
      }
    }
    const maxInput = 32_000;
    stream.markdown("...");
    return {};
  }

  // >-- Error handling -----------------------------------------------

  private handleError(err: unknown, stream: vscode.ChatResponseStream): vscode.ChatResult {
    if (err instanceof GatewayError) {
      if (err.status === 401) {
        stream.markdown("**Authentication failed.** Run **R4: Login to Gateway**.");
      } else if (err.status === 503) {
        stream.markdown("**No active endpoint.** Run **R4: Provision Endpoint** first.");
      } else {
        stream.markdown("**Gateway error " + err.status + "**: " + err.message);
      }
    } else if (err instanceof Error && err.name === "AbortError") {
      stream.markdown("*Request cancelled.*");
    } else {
      stream.markdown("**Error**: " + (err instanceof Error ? err.message : String(err)));
    }
    return {};
  }

  /** Handles tool-call anchor/command-clicks (B15). */
  async handleToolCallDetail(toolName: string): Promise<void> {
    const last = this._lastResult;
    if (!last) {
      vscode.window.showInformationMessage(toolName + " — no recent agent result.");
      return;
    }
    const info = last.toolCalls.find((tc) => tc.toolName === toolName);
    const details = info
      ? (info.success ? "Success" : "Failed: " + (info.error || "unknown"))
      : "No tool call record found.";
    vscode.window.showInformationMessage(toolName + " — " + details);
  }


  // >-- B20.1 In-chat model picker ---------------------------------------

  private async handleModelPicker(
    client: GatewayClient, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    stream.progress("Loading models...");
    const models = await client.listModels();
    if (models.length === 0) {
      stream.markdown("No models found in the registry.");
      return {};
    }
    const modelNames = models.map(m => m.name);
    const selected = await vscode.window.showQuickPick(modelNames, {
      placeHolder: "Select a model to use for the next turn",
    });
    if (!selected) {
      stream.markdown("Model selection cancelled.");
      return {};
    }
    this.sessionState.selected_model = selected;
    stream.markdown("Selected model: **" + selected + "**. This model will be used for your next chat turn.");
    return {};
  }

  // >-- B20.3 Chat history search ----------------------------------------

  private async handleHistorySearch(
    client: GatewayClient, request: vscode.ChatRequest,
    context: vscode.ChatContext, stream: vscode.ChatResponseStream,
  ): Promise<vscode.ChatResult> {
    const query = request.prompt.trim().toLowerCase();
    if (!query) {
      stream.markdown("Usage: `/history <query>` to search chat history.");
      return {};
    }

    const results: { source: string; content: string }[] = [];

    for (const turn of context.history) {
      if (turn instanceof vscode.ChatRequestTurn) {
        if (turn.prompt.toLowerCase().includes(query)) {
          results.push({ source: "User", content: turn.prompt });
        }
      } else if (turn instanceof vscode.ChatResponseTurn) {
        for (const part of turn.response) {
          if (part instanceof vscode.ChatResponseMarkdownPart) {
            const content = part.value.value.toLowerCase();
            if (content.includes(query)) {
              results.push({ source: "Assistant", content: part.value.value });
            }
          }
        }
      }
    }

    if (results.length === 0) {
      stream.markdown("No history matches found for: **" + query + "**.");
      return {};
    }

    stream.markdown("### History Results\n\nFound " + results.length + " match" + (results.length === 1 ? "" : "es") + ":\n\n");

    for (const [i, result] of results.entries()) {
      const source = result.source === "User" ? "\ud83d\udc64 You" : "\ud83e\udde0 Agent";
      const preview = result.content.length > 300
        ? result.content.substring(0, 300) + "..." : result.content;
      stream.markdown("**" + (i + 1) + ". " + source + "**\n\n");
      stream.markdown("```\n" + preview + "```\n\n");
    }

    return {};
  }

  // >-- B20.4 Multi-file context from open tabs --------------------------

  private async collectOpenTabContexts(): Promise<string> {
    const editors = vscode.window.visibleTextEditors;
    if (editors.length === 0) {
      return "";
    }

    const contexts: string[] = [];
    for (const editor of editors) {
      try {
        const document = editor.document;
        if (document && document.uri && document.uri.scheme === "file") {
          const text = document.getText();
          if (text.trim().length > 0) {
            contexts.push(
              "--- OPEN TAB: " + document.fileName + " ---\n" + text
            );
          }
        }
      } catch {
        // skip unreadable files
      }
    }

    return contexts.join("\n\n");
  }

  dispose(): void { for (const d of this.disposables) { d.dispose(); } }
}
