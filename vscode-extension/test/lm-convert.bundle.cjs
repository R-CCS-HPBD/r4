"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/lm-convert.ts
var lm_convert_exports = {};
__export(lm_convert_exports, {
  buildThinkingEffortSchema: () => buildThinkingEffortSchema,
  collectTrailingToolResultIds: () => collectTrailingToolResultIds,
  convertMessages: () => convertMessages,
  convertTools: () => convertTools,
  countMessageChars: () => countMessageChars,
  deriveReasoningEffortFromModelId: () => deriveReasoningEffortFromModelId,
  getConfiguredThinkingEffort: () => getConfiguredThinkingEffort,
  isReasoningFirstModel: () => isReasoningFirstModel,
  modelSupportsThinking: () => modelSupportsThinking,
  resolveModelEffortConfig: () => resolveModelEffortConfig,
  resolveReasoningEffortConfig: () => resolveReasoningEffortConfig,
  sanitizeLeakedMetadata: () => sanitizeLeakedMetadata
});
module.exports = __toCommonJS(lm_convert_exports);
var vscode2 = __toESM(require("vscode"));

// src/reasoning-cache.ts
function createToolReasoningKey(toolCallId) {
  return `tool:${toolCallId}`;
}
function createPostToolReasoningKey(toolCallIds) {
  return `post-tool:${JSON.stringify(toolCallIds)}`;
}

// src/reasoning-replay.ts
var vscode = __toESM(require("vscode"));
var R4_REPLAY_MARKER_MIME = "application/vnd.r4.replay-marker";
var R4_REPLAY_MARKER_WRITER_ID = "r4-replay-marker";
var ENCODED_JSON_MARKER_PREFIX = "j:";
function parseReplayMarkerData(data) {
  const decoded = new TextDecoder().decode(data);
  const separatorIndex = decoded.indexOf("\\");
  if (separatorIndex < 0) {
    return { valid: false, error: "marker-prefix-missing" };
  }
  const markerPrefix = decoded.slice(0, separatorIndex);
  if (markerPrefix !== R4_REPLAY_MARKER_WRITER_ID) {
    return { valid: false, error: "marker-prefix-mismatch" };
  }
  const markerPayload = decoded.slice(separatorIndex + 1);
  if (!markerPayload.startsWith(ENCODED_JSON_MARKER_PREFIX)) {
    return { valid: false, error: "marker-payload-format" };
  }
  const encodedPayload = markerPayload.slice(ENCODED_JSON_MARKER_PREFIX.length);
  if (!encodedPayload) {
    return { valid: false, error: "marker-payload-empty" };
  }
  try {
    const json = Buffer.from(encodedPayload, "base64url").toString("utf8");
    const value = JSON.parse(json);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { valid: false, error: "marker-payload-not-object" };
    }
    const reasoning = value.reasoning;
    if (!reasoning || typeof reasoning !== "object" || Array.isArray(reasoning)) {
      return { valid: true };
    }
    const text = reasoning.text;
    if (typeof text !== "string" || text.length === 0) {
      return { valid: true };
    }
    return { valid: true, reasoningText: text };
  } catch {
    return { valid: false, error: "marker-json-invalid" };
  }
}
function parseReplayMarkerPart(part) {
  if (!(part instanceof vscode.LanguageModelDataPart)) {
    return void 0;
  }
  if (part.mimeType !== R4_REPLAY_MARKER_MIME) {
    return void 0;
  }
  return parseReplayMarkerData(part.data);
}
function parseFirstReplayMarkerReasoning(message) {
  for (const part of message.content) {
    const parsed = parseReplayMarkerPart(part);
    if (parsed?.valid && parsed.reasoningText) {
      return parsed.reasoningText;
    }
  }
  return void 0;
}

// src/lm-convert.ts
function isLanguageModelThinkingPart(part) {
  const Ctor = vscode2.LanguageModelThinkingPart;
  return typeof Ctor === "function" && part instanceof Ctor;
}
var INTERNAL_METADATA_MIME_TYPES = /* @__PURE__ */ new Set([
  "cache_control",
  "application/vnd.r4.replay-marker"
]);
function isInternalMetadataPart(part) {
  if (part instanceof vscode2.LanguageModelDataPart) {
    return INTERNAL_METADATA_MIME_TYPES.has(part.mimeType);
  }
  return false;
}
var LEAKED_CACHE_CONTROL_RE = /\{"\$mid":\d+,"mimeType":"cache_control","data":"[^"]*"\}/g;
function sanitizeLeakedMetadata(text) {
  return text.replace(LEAKED_CACHE_CONTROL_RE, "");
}
function normalizeThinkingText(value) {
  return Array.isArray(value) ? value.join("") : value;
}
function mapRole(role) {
  switch (role) {
    case vscode2.LanguageModelChatMessageRole.Assistant:
      return "assistant";
    default:
      return "user";
  }
}
function lookupCachedReasoning(cache, toolCalls, recentToolResultIds) {
  if (toolCalls.length > 0) {
    for (const tc of toolCalls) {
      const cached = cache.get(createToolReasoningKey(tc.id)) ?? cache.get(tc.id);
      if (cached) {
        return cached.text;
      }
    }
  } else if (recentToolResultIds.length > 0) {
    return cache.get(createPostToolReasoningKey(recentToolResultIds))?.text;
  }
  return void 0;
}
function resolveReasoningContent(message, thinkingContent, toolCalls, recentToolResultIds, cache) {
  const markerText = parseFirstReplayMarkerReasoning(message);
  if (markerText) {
    return markerText;
  }
  if (thinkingContent) {
    return thinkingContent;
  }
  return lookupCachedReasoning(cache, toolCalls, recentToolResultIds) ?? "";
}
function convertMessages(messages, isThinkingModel, reasoningCache) {
  const result = [];
  let recentToolResultIds = [];
  for (const message of messages) {
    const role = mapRole(message.role);
    let content = "";
    let thinkingContent = "";
    const toolCalls = [];
    const toolResults = [];
    for (const part of message.content) {
      if (isInternalMetadataPart(part)) {
        continue;
      }
      if (part instanceof vscode2.LanguageModelTextPart) {
        content += part.value;
      } else if (isLanguageModelThinkingPart(part)) {
        thinkingContent += normalizeThinkingText(part.value);
      } else if (part instanceof vscode2.LanguageModelDataPart) {
        if (part.mimeType.startsWith("image/")) {
        }
      } else if (part instanceof vscode2.LanguageModelToolCallPart) {
        toolCalls.push({
          id: part.callId,
          type: "function",
          function: {
            name: part.name,
            arguments: JSON.stringify(part.input ?? {})
          }
        });
      } else if (part instanceof vscode2.LanguageModelToolResultPart) {
        toolResults.push({
          callId: part.callId,
          content: stringifyToolResult(part.content)
        });
      }
    }
    if (role === "assistant") {
      if (content || toolCalls.length > 0) {
        const msg = {
          role: "assistant",
          content: content || ""
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
            reasoningCache
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
        tool_call_id: tr.callId
      });
      recentToolResultIds.push(tr.callId);
    }
  }
  return result;
}
function buildUserMessage(msg) {
  const contentParts = [];
  const textParts = [];
  for (const part of msg.content) {
    if (isInternalMetadataPart(part)) {
      continue;
    }
    if (part instanceof vscode2.LanguageModelTextPart) {
      const sanitized = sanitizeLeakedMetadata(part.value);
      textParts.push(sanitized);
      contentParts.push({ type: "text", text: sanitized });
    } else if (part instanceof vscode2.LanguageModelDataPart) {
      if (part.mimeType.startsWith("image/")) {
        const base64 = Buffer.from(part.data).toString("base64");
        contentParts.push({
          type: "image_url",
          image_url: { url: `data:${part.mimeType};base64,${base64}` }
        });
      }
    }
  }
  if (contentParts.length === 0) {
    return void 0;
  }
  const hasDataParts = contentParts.some((p) => p.type !== "text");
  return {
    role: "user",
    content: hasDataParts ? contentParts : textParts.join("")
  };
}
function convertTools(tools) {
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
      parameters: t.inputSchema ?? {
        type: "object",
        properties: {}
      }
    }
  }));
}
function collectTrailingToolResultIds(messages) {
  const ids = [];
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
function countMessageChars(messages) {
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
function stringifyToolResult(content) {
  const parts = [];
  for (const item of content) {
    if (isInternalMetadataPart(item)) {
      continue;
    }
    if (item instanceof vscode2.LanguageModelTextPart) {
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
function isReasoningFirstModel(modelId) {
  const id = modelId.toLowerCase();
  return id.includes("kimi") || id.includes("glm") || /gemma[-_ ]?4/.test(id) || id.includes("gpt-oss") || id.includes("deepseek") || id.includes("thinking") || id.includes("qwen3");
}
function modelSupportsThinking(modelId, reasoningModels) {
  return reasoningModels.has(modelId.toLowerCase()) || isReasoningFirstModel(modelId);
}
var EFFORT_LABELS = {
  none: "None",
  low: "Low",
  medium: "Medium",
  high: "High",
  max: "Max"
};
function effortLabel(level) {
  return EFFORT_LABELS[level] ?? level.charAt(0).toUpperCase() + level.slice(1);
}
var MODEL_REASONING_EFFORT = [
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
  ["gemma4", ["none", "low", "medium", "high"], "medium"]
];
function deriveReasoningEffortFromModelId(modelId) {
  const nameLc = (modelId || "").toLowerCase();
  if (!nameLc) {
    return void 0;
  }
  if (nameLc.includes("kimi-k2.7") || nameLc.includes("kimi_k2.7")) {
    return void 0;
  }
  for (const [pattern, levels, defaultLevel] of MODEL_REASONING_EFFORT) {
    if (nameLc.includes(pattern)) {
      return { levels: [...levels], default: defaultLevel };
    }
  }
  return void 0;
}
function resolveReasoningEffortConfig(capabilities) {
  const levels = capabilities?.reasoning_effort_levels;
  const defaultLevel = capabilities?.reasoning_effort_default;
  if (!levels?.length || !defaultLevel || !levels.includes(defaultLevel)) {
    return void 0;
  }
  return { levels: [...levels], default: defaultLevel };
}
function resolveModelEffortConfig(modelId, capabilities) {
  return resolveReasoningEffortConfig(capabilities) ?? deriveReasoningEffortFromModelId(modelId);
}
function getConfiguredThinkingEffort(options, allowed, defaultLevel) {
  if (!allowed.length) {
    return void 0;
  }
  if (allowed.length === 1) {
    return allowed[0];
  }
  const configured = options.modelOptions?.reasoningEffort ?? options.modelConfiguration?.reasoningEffort ?? options.configuration?.reasoningEffort;
  if (typeof configured === "string" && allowed.includes(configured)) {
    return configured;
  }
  return defaultLevel;
}
function buildThinkingEffortSchema(levels, defaultLevel) {
  return {
    properties: {
      reasoningEffort: {
        type: "string",
        title: "Thinking",
        enum: [...levels],
        enumItemLabels: levels.map(effortLabel),
        default: defaultLevel,
        group: "navigation"
      }
    }
  };
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  buildThinkingEffortSchema,
  collectTrailingToolResultIds,
  convertMessages,
  convertTools,
  countMessageChars,
  deriveReasoningEffortFromModelId,
  getConfiguredThinkingEffort,
  isReasoningFirstModel,
  modelSupportsThinking,
  resolveModelEffortConfig,
  resolveReasoningEffortConfig,
  sanitizeLeakedMetadata
});
