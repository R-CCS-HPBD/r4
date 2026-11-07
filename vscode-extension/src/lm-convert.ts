import * as vscode from "vscode";
import type {
  OpenAIChatMessage,
  OpenAIMessageContentPart,
  OpenAITool,
  OpenAIToolCall,
} from "./gateway-client";
import {
  createPostToolReasoningKey,
  createToolReasoningKey,
  type ReasoningEntry,
} from "./reasoning-cache";
import { parseFirstReplayMarkerReasoning } from "./reasoning-replay";

type ThinkingPartLike = { value: string | string[] };

function isLanguageModelThinkingPart(part: unknown): part is ThinkingPartLike {
  const Ctor = (vscode as { LanguageModelThinkingPart?: new (...args: unknown[]) => unknown })
    .LanguageModelThinkingPart;
  return typeof Ctor === "function" && part instanceof Ctor;
}

/**
 * MIME types that VS Code or other extensions inject as internal metadata
 * (prompt-cache markers, replay markers, etc.). These must never be
 * serialized into the OpenAI request payload.
 */
const INTERNAL_METADATA_MIME_TYPES = new Set([
  "cache_control",
  "application/vnd.r4.replay-marker",
]);

/** Returns true if the part is internal metadata that should be skipped. */
function isInternalMetadataPart(part: unknown): boolean {
  if (part instanceof vscode.LanguageModelDataPart) {
    return INTERNAL_METADATA_MIME_TYPES.has(part.mimeType);
  }
  return false;
}

/**
 * Regex that matches the leaked cache_control JSON fragment:
 *   {"$mid":NN,"mimeType":"cache_control","data":"ZXBoZW1lcmFs"}
 * VS Code's RPC marshalling adds $mid; the data field is base64-encoded
 * (e.g. "ZXBoZW1lcmFs" = "ephemeral"). Once this leaks into conversation
 * history, every subsequent request contains it.
 */
const LEAKED_CACHE_CONTROL_RE =
  /\{"\$mid":\d+,"mimeType":"cache_control","data":"[^"]*"\}/g;

/**
 * Strip any leaked internal-metadata JSON fragments from text.
 * Applied to both tool-result strings and user-message text so that
 * metadata that already entered the conversation history does not
 * persist across turns.
 */
export function sanitizeLeakedMetadata(text: string): string {
  return text.replace(LEAKED_CACHE_CONTROL_RE, "");
}

function normalizeThinkingText(value: string | string[]): string {
  return Array.isArray(value) ? value.join("") : value;
}

function mapRole(role: vscode.LanguageModelChatMessageRole): "user" | "assistant" {
  switch (role) {
    case vscode.LanguageModelChatMessageRole.Assistant:
      return "assistant";
    default:
      return "user";
  }
}

function lookupCachedReasoning(
  cache: Map<string, ReasoningEntry>,
  toolCalls: OpenAIToolCall[],
  recentToolResultIds: readonly string[],
): string | undefined {
  if (toolCalls.length > 0) {
    for (const tc of toolCalls) {
      const cached =
        cache.get(createToolReasoningKey(tc.id)) ?? cache.get(tc.id);
      if (cached) {
        return cached.text;
      }
    }
  } else if (recentToolResultIds.length > 0) {
    return cache.get(createPostToolReasoningKey(recentToolResultIds))?.text;
  }
  return undefined;
}

function resolveReasoningContent(
  message: vscode.LanguageModelChatRequestMessage,
  thinkingContent: string,
  toolCalls: OpenAIToolCall[],
  recentToolResultIds: readonly string[],
  cache: Map<string, ReasoningEntry>,
): string {
  const markerText = parseFirstReplayMarkerReasoning(message);
  if (markerText) {
    return markerText;
  }
  if (thinkingContent) {
    return thinkingContent;
  }
  return lookupCachedReasoning(cache, toolCalls, recentToolResultIds) ?? "";
}

/**
 * Convert VS Code chat messages to OpenAI-compatible format.
 * Injects cached / replayed reasoning_content for thinking-model tool turns.
 */
export function convertMessages(
  messages: readonly vscode.LanguageModelChatRequestMessage[],
  isThinkingModel: boolean,
  reasoningCache: Map<string, ReasoningEntry>,
): OpenAIChatMessage[] {
  const result: OpenAIChatMessage[] = [];
  let recentToolResultIds: string[] = [];

  for (const message of messages) {
    const role = mapRole(message.role);
    let content = "";
    let thinkingContent = "";
    const toolCalls: OpenAIToolCall[] = [];
    const toolResults: Array<{ callId: string; content: string }> = [];

    for (const part of message.content) {
      if (isInternalMetadataPart(part)) {
        continue;
      }
      if (part instanceof vscode.LanguageModelTextPart) {
        content += part.value;
      } else if (isLanguageModelThinkingPart(part)) {
        thinkingContent += normalizeThinkingText(part.value);
      } else if (part instanceof vscode.LanguageModelDataPart) {
        if (part.mimeType.startsWith("image/")) {
          // Images are handled when building the user message below.
        }
      } else if (part instanceof vscode.LanguageModelToolCallPart) {
        toolCalls.push({
          id: part.callId,
          type: "function",
          function: {
            name: part.name,
            arguments: JSON.stringify(part.input ?? {}),
          },
        });
      } else if (part instanceof vscode.LanguageModelToolResultPart) {
        toolResults.push({
          callId: part.callId,
          content: stringifyToolResult(part.content),
        });
      }
    }

    if (role === "assistant") {
      if (content || toolCalls.length > 0) {
        const msg: OpenAIChatMessage = {
          role: "assistant",
          content: content || "",
        };
        if (toolCalls.length > 0) {
          msg.tool_calls = toolCalls;
        }
        if (isThinkingModel) {
          msg.reasoning_content = resolveReasoningContent(
            message,
            thinkingContent,
            toolCalls,
            recentToolResultIds,
            reasoningCache,
          );
        }
        result.push(msg);
        recentToolResultIds = [];
      }
    } else {
      const userMsg = buildUserMessage(message);
      if (userMsg) {
        recentToolResultIds = [];
        result.push(userMsg);
      } else if (toolResults.length === 0) {
        recentToolResultIds = [];
      }
    }

    for (const tr of toolResults) {
      result.push({
        role: "tool",
        content: tr.content,
        tool_call_id: tr.callId,
      });
      recentToolResultIds.push(tr.callId);
    }
  }

  return result;
}

function buildUserMessage(
  msg: vscode.LanguageModelChatRequestMessage,
): OpenAIChatMessage | undefined {
  const contentParts: OpenAIMessageContentPart[] = [];
  const textParts: string[] = [];

  for (const part of msg.content) {
    if (isInternalMetadataPart(part)) {
      continue;
    }
    if (part instanceof vscode.LanguageModelTextPart) {
      const sanitized = sanitizeLeakedMetadata(part.value);
      textParts.push(sanitized);
      contentParts.push({ type: "text", text: sanitized });
    } else if (part instanceof vscode.LanguageModelDataPart) {
      if (part.mimeType.startsWith("image/")) {
        const base64 = Buffer.from(part.data).toString("base64");
        contentParts.push({
          type: "image_url",
          image_url: { url: `data:${part.mimeType};base64,${base64}` },
        });
      }
    }
  }

  if (contentParts.length === 0) {
    return undefined;
  }

  const hasDataParts = contentParts.some((p) => p.type !== "text");
  return {
    role: "user",
    content: hasDataParts ? contentParts : textParts.join(""),
  };
}

export function convertTools(
  tools: readonly vscode.LanguageModelChatTool[] | undefined,
): OpenAITool[] {
  if (!tools) {
    return [];
  }
  return tools.map((t) => ({
    type: "function",
    function: {
      name: t.name,
      // vLLM (0.22.x) rejects tools whose `description` is omitted with a
      // 400 "Input should be a valid string" ToolDescription error. Coerce
      // undefined/null to an empty string so the field is always present
      // and valid.
      description: t.description ?? "",
      parameters: (t.inputSchema as object | undefined) ?? {
        type: "object",
        properties: {},
      },
    },
  }));
}

export function collectTrailingToolResultIds(messages: OpenAIChatMessage[]): string[] {
  const ids: string[] = [];
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "tool" && msg.tool_call_id) {
      ids.unshift(msg.tool_call_id);
      continue;
    }
    break;
  }
  return ids;
}

export function countMessageChars(messages: OpenAIChatMessage[]): number {
  let total = 0;
  for (const msg of messages) {
    if (typeof msg.content === "string") {
      total += msg.content.length;
    } else if (Array.isArray(msg.content)) {
      for (const part of msg.content) {
        if (part.type === "text") {
          total += part.text.length;
        }
      }
    }
    total += msg.reasoning_content?.length ?? 0;
    if (msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        total += tc.function?.name?.length ?? 0;
        total += tc.function?.arguments?.length ?? 0;
      }
    }
  }
  return total;
}

function stringifyToolResult(content: ReadonlyArray<unknown>): string {
  const parts: string[] = [];
  for (const item of content) {
    if (isInternalMetadataPart(item)) {
      continue;
    }
    if (item instanceof vscode.LanguageModelTextPart) {
      parts.push(item.value);
    } else if (typeof item === "string") {
      parts.push(item);
    } else {
      try {
        parts.push(JSON.stringify(item));
      } catch {
        parts.push(String(item));
      }
    }
  }
  return sanitizeLeakedMetadata(parts.join(""));
}

/** Models whose chat template may emit inline thinking tags without a parser. */
export function isReasoningFirstModel(modelId: string): boolean {
  const id = modelId.toLowerCase();
  return (
    id.includes("kimi") ||
    id.includes("glm") ||
    /gemma[-_ ]?4/.test(id) ||
    id.includes("gpt-oss") ||
    id.includes("deepseek") ||
    id.includes("thinking") ||
    id.includes("qwen3")
  );
}

export function modelSupportsThinking(modelId: string, reasoningModels: Set<string>): boolean {
  return reasoningModels.has(modelId.toLowerCase()) || isReasoningFirstModel(modelId);
}

export type ThinkingEffort = string;

export type ReasoningEffortConfig = {
  levels: string[];
  default: string;
};

const EFFORT_LABELS: Record<string, string> = {
  none: "None",
  low: "Low",
  medium: "Medium",
  high: "High",
  max: "Max",
};

function effortLabel(level: string): string {
  return EFFORT_LABELS[level] ?? level.charAt(0).toUpperCase() + level.slice(1);
}

export type ModelConfigurationOptions = vscode.ProvideLanguageModelChatResponseOptions & {
  readonly modelOptions?: Record<string, unknown>;
  readonly modelConfiguration?: Record<string, unknown>;
  readonly configuration?: Record<string, unknown>;
};

export interface CatalogModelCapabilities {
  supports_reasoning?: boolean;
  reasoning_effort_levels?: string[];
  reasoning_effort_default?: string;
}

/** Client fallback when catalog capabilities are not backfilled yet (mirrors UMRS). */
const MODEL_REASONING_EFFORT: ReadonlyArray<readonly [string, readonly string[], string]> = [
  ["kimi", ["none", "high"], "high"],
  ["glm-5.2", ["none", "high", "max"], "max"],
  ["glm-5.1", ["none", "low", "medium", "high"], "medium"],
  ["glm", ["none", "low", "medium", "high"], "medium"],
  ["gpt-oss-swallow", ["medium"], "medium"],
  ["llm-jp-4", ["low", "medium", "high"], "medium"],
  ["deepseek-v4", ["none", "high", "max"], "high"],
  ["gemma-4", ["none", "low", "medium", "high"], "medium"],
  ["gemma_4", ["none", "low", "medium", "high"], "medium"],
  ["gemma 4", ["none", "low", "medium", "high"], "medium"],
  ["gemma4", ["none", "low", "medium", "high"], "medium"],
];

export function deriveReasoningEffortFromModelId(modelId: string): ReasoningEffortConfig | undefined {
  const nameLc = (modelId || "").toLowerCase();
  if (!nameLc) {
    return undefined;
  }
  // K2.7 Code: thinking always on; no user-facing effort control.
  if (nameLc.includes("kimi-k2.7") || nameLc.includes("kimi_k2.7")) {
    return undefined;
  }
  for (const [pattern, levels, defaultLevel] of MODEL_REASONING_EFFORT) {
    if (nameLc.includes(pattern)) {
      return { levels: [...levels], default: defaultLevel };
    }
  }
  return undefined;
}

export function resolveReasoningEffortConfig(
  capabilities?: CatalogModelCapabilities,
): ReasoningEffortConfig | undefined {
  const levels = capabilities?.reasoning_effort_levels;
  const defaultLevel = capabilities?.reasoning_effort_default;
  if (!levels?.length || !defaultLevel || !levels.includes(defaultLevel)) {
    return undefined;
  }
  return { levels: [...levels], default: defaultLevel };
}

export function resolveModelEffortConfig(
  modelId: string,
  capabilities?: CatalogModelCapabilities,
): ReasoningEffortConfig | undefined {
  return resolveReasoningEffortConfig(capabilities) ?? deriveReasoningEffortFromModelId(modelId);
}

export function getConfiguredThinkingEffort(
  options: ModelConfigurationOptions,
  allowed: readonly string[],
  defaultLevel: string,
): ThinkingEffort | undefined {
  if (!allowed.length) {
    return undefined;
  }
  if (allowed.length === 1) {
    return allowed[0];
  }

  const configured =
    options.modelOptions?.reasoningEffort ??
    options.modelConfiguration?.reasoningEffort ??
    options.configuration?.reasoningEffort;
  if (typeof configured === "string" && allowed.includes(configured)) {
    return configured;
  }
  return defaultLevel;
}

export function buildThinkingEffortSchema(
  levels: readonly string[],
  defaultLevel: string,
): object {
  return {
    properties: {
      reasoningEffort: {
        type: "string",
        title: "Thinking",
        enum: [...levels],
        enumItemLabels: levels.map(effortLabel),
        default: defaultLevel,
        group: "navigation",
      },
    },
  };
}
