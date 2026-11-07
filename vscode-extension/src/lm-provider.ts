/**
 * R4 Language Model Chat Provider.
 *
 * Registers R4 Platform LLM endpoints (served through the Gateway
 * OpenAI-compatible /gw/llm/* routes) as native VS Code language models.
 * This makes them selectable in the Copilot chat/edit/agent model picker.
 *
 * Architecture: provider → GatewayClient → Gateway HTTP (/gw/llm/v1/*).
 * Multi-source: aggregates models from ALL platform sources in sources.json.
 * Tool calling is supported and mapped to/from the OpenAI tool-call wire format.
 */

import * as vscode from "vscode";
import type { OpenAIToolCall, StreamDelta } from "./gateway-client";
import { GatewayClient } from "./gateway-client";
import type { LlmSourceConfig } from "./auth";
import {
  buildThinkingEffortSchema,
  collectTrailingToolResultIds,
  convertMessages,
  convertTools,
  countMessageChars,
  getConfiguredThinkingEffort,
  isReasoningFirstModel,
  modelSupportsThinking,
  resolveModelEffortConfig,
  type ModelConfigurationOptions,
  type ReasoningEffortConfig,
} from "./lm-convert";
import { estimateTokenCount, updateCharsPerToken } from "./lm-tokens";
import { getStreamDebugLogger, RequestLifecycle } from "./lm-stream-log";
import {
  cacheStreamedReasoning,
  type ReasoningEntry,
} from "./reasoning-cache";
import { createReplayMarkerPart } from "./reasoning-replay";
import { ThinkTagFilter } from "./think-filter";
import {
  createThinkingSequenceId,
  reportLanguageModelThinking,
  reportLanguageModelThinkingDone,
  ThinkingStreamGate,
} from "./thinking";
import {
  collectVisionCatalogKeys,
  filterReadyVisionModelIds,
  llmIdMatchesVisionCatalog,
  messagesHaveImages,
  pickVisionClient,
  resolveVisionProxyMessages,
} from "./vision-proxy";

export const R4_LM_VENDOR = "r4";

/** Fallback context window when an endpoint does not advertise one. */
const DEFAULT_MAX_INPUT_TOKENS = 32_000;
/**
 * Output budget advertised to VS Code's model picker and reserved when
 * computing maxInputTokens (input + output ≈ max_model_len).
 *
 * NOT sent as max_tokens on the wire — the upstream server (vLLM, LiteLLM)
 * decides the actual output limit, typically max_model_len − prompt_len.
 * This matches the "model decides" pattern used by DeepSeek V4 for Copilot.
 */
const DEFAULT_MAX_OUTPUT_TOKENS = 8_192;

/** Prefix for encoding source name in model IDs for routing. */
const LM_SOURCE_ID_PREFIX = "__r4_src:";

type ModelPickerChatInformation = vscode.LanguageModelChatInformation & {
  configurationSchema?: object;
};

/** Accumulates streamed tool-call fragments keyed by their stream index. */
interface ToolCallAccumulator {
  id: string;
  name: string;
  args: string;
}

interface ResponseStreamState {
  accumulatedReasoning: string;
  emittedToolCallIds: string[];
}

// ---------------------------------------------------------------------------
// Source ID encoding — embeds source name in model.id for routing back.
// ---------------------------------------------------------------------------

function encodeSourceInId(source: string, modelId: string): string {
  // No source label (active-source entries) → plain ID. Encoding an empty
  // source would produce "__r4_src::<model>", which decode/strip cannot
  // parse and the raw prefixed ID would leak to the Gateway (503).
  if (!source) return modelId;
  return `${LM_SOURCE_ID_PREFIX}${source}:${modelId}`;
}

function decodeSourceFromId(modelId: string): string | undefined {
  if (!modelId.startsWith(LM_SOURCE_ID_PREFIX)) return undefined;
  const rest = modelId.slice(LM_SOURCE_ID_PREFIX.length);
  const idx = rest.indexOf(":");
  if (idx > 0) return rest.slice(0, idx);
  return undefined;
}

function stripSourceFromId(modelId: string): string {
  // Defensive: strip the prefix even when the source segment is empty
  // ("__r4_src::model") so a malformed ID never reaches the Gateway.
  if (!modelId.startsWith(LM_SOURCE_ID_PREFIX)) return modelId;
  const rest = modelId.slice(LM_SOURCE_ID_PREFIX.length);
  const idx = rest.indexOf(":");
  return idx >= 0 ? rest.slice(idx + 1) : modelId;
}

// ---------------------------------------------------------------------------

/** Human-readable origin shown under each model in the picker. */
function buildModelDetail(sourceLabel: string, sourceKind: "platform" | "public"): string {
  const named = sourceLabel && sourceLabel !== "R4";
  if (sourceKind === "public") {
    return named ? `R4 Public (${sourceLabel})` : "R4 Public";
  }
  return named ? `R4 Platform (${sourceLabel})` : "R4 Platform";
}

/**
 * Build model info entries from a single client.
 * Returns an array of LanguageModelChatInformation with source-encoded IDs.
 */
async function buildModelEntries(
  client: GatewayClient,
  sourceLabel: string,
  sourceKind: "platform" | "public" = "platform",
): Promise<vscode.LanguageModelChatInformation[]> {
  let llmModels: any[] = [];
  let catalogModels: any[] = [];
  let endpoints: any[] = [];
  try {
    llmModels = (await client.listLLMModels()) ?? [];
    catalogModels = (await client.listModels().catch(() => [])) ?? [];
    endpoints = (await client.listEndpoints().catch(() => [])) ?? [];
  } catch {
    return [];
  }

  // Endpoint max_model_len lookup (per model name)
  const endpointMaxLen = new Map<string, number>();
  for (const ep of endpoints) {
    const mml = ep.max_model_len;
    if (typeof mml !== "number" || mml <= 0) continue;
    const names: string[] = [];
    if (ep.served_model_name) names.push(ep.served_model_name.toLowerCase());
    if (ep.model) names.push(ep.model.toLowerCase());
    for (const name of names) {
      const existing = endpointMaxLen.get(name);
      if (existing === undefined || mml > existing) {
        endpointMaxLen.set(name, mml);
      }
    }
  }

  const visionCatalogKeys = collectVisionCatalogKeys(catalogModels);
  const reasoningModels = new Set(
    catalogModels
      .filter(
        (m) =>
          m.capabilities?.supports_reasoning === true ||
          (m.name || "").toLowerCase().includes("thinking"),
      )
      .flatMap((m) => [
        (m.name || "").toLowerCase(),
        (m.id || m.model_id || "").toLowerCase(),
      ])
      .filter(Boolean),
  );

  const catalogByKey = new Map<string, any>();
  for (const entry of catalogModels) {
    const idKey = (entry.id || entry.model_id || "").toLowerCase();
    const nameKey = (entry.name || "").toLowerCase();
    if (idKey) catalogByKey.set(idKey, entry);
    if (nameKey) catalogByKey.set(nameKey, entry);
  }

  const llmModelIds = llmModels.map((m) => m.id).filter(Boolean);

  return llmModels.map((m) => {
    let maxInput = DEFAULT_MAX_INPUT_TOKENS;
    if (m.max_model_len && m.max_model_len > 0) {
      maxInput = Math.max(1024, m.max_model_len - DEFAULT_MAX_OUTPUT_TOKENS);
    } else {
      const epLen = endpointMaxLen.get((m.id || "").toLowerCase());
      if (epLen && epLen > 0) {
        maxInput = Math.max(1024, epLen - DEFAULT_MAX_OUTPUT_TOKENS);
      }
    }

    const modelKey = (m.id || "").toLowerCase();
    const isVision = llmIdMatchesVisionCatalog(m.id, visionCatalogKeys);
    const hasVisionProxy = filterReadyVisionModelIds(catalogModels, llmModelIds, m.id).length > 0;
    const supportsThinking = modelSupportsThinking(m.id, reasoningModels);
    const catalogEntry = catalogByKey.get(modelKey);
    const effortConfig = resolveModelEffortConfig(m.id, catalogEntry?.capabilities);

    const detail = buildModelDetail(sourceLabel, sourceKind);

    const info: ModelPickerChatInformation = {
      id: encodeSourceInId(sourceLabel, m.id),
      name: m.id,
      family: "r4",
      version: "1.0.0",
      maxInputTokens: maxInput,
      maxOutputTokens: DEFAULT_MAX_OUTPUT_TOKENS,
      detail,
      capabilities: {
        toolCalling: true,
        imageInput: isVision || hasVisionProxy,
      },
    };
    if (supportsThinking && effortConfig && effortConfig.levels.length > 1) {
      info.configurationSchema = buildThinkingEffortSchema(
        effortConfig.levels,
        effortConfig.default,
      );
    }
    return info;
  });
}

// ---------------------------------------------------------------------------

export class R4LanguageModelProvider implements vscode.LanguageModelChatProvider {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeLanguageModelChatInformation = this._onDidChange.event;

  private readonly reasoningCache = new Map<string, ReasoningEntry>();
  private isActive = true;
  private charsPerToken = 4.0;
  private reasoningModels = new Set<string>();
  private visionCatalogKeys: string[] = [];
  private modelEffortConfigs = new Map<string, ReasoningEffortConfig>();

  constructor(
    private readonly getClient: () => GatewayClient | undefined,
    /** All LLM-capable source configs (platform + public) for aggregation. */
    private readonly listSourceConfigs: () => LlmSourceConfig[],
    private readonly getSourceLabel: () => string = () => "R4",
  ) {}

  /** Notify VS Code that the available model set may have changed. */
  refresh(): void {
    this._onDidChange.fire();
  }

  async prepareForDeactivate(): Promise<void> {
    this.isActive = false;
    this._onDidChange.fire();
    try {
      await vscode.lm.selectChatModels({ vendor: R4_LM_VENDOR });
    } catch {
      // Host may not support selectChatModels on older builds.
    }
  }

  async provideLanguageModelChatInformation(
    _options: { readonly silent: boolean },
    _token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelChatInformation[]> {
    if (!this.isActive) {
      return [];
    }

    const activeClient = this.getClient();

    // Collect fetch promises: active source first (if available), then all
    // other sources.  We deduplicate by model ID at the aggregation step so
    // there is no need to compare URLs against private fields.
    const fetchPromises: Promise<vscode.LanguageModelChatInformation[]>[] = [];

    // Active source (has a live client).
    if (activeClient) {
      fetchPromises.push(buildModelEntries(activeClient, ""));
    }

    // All other sources — create clients and fetch in parallel.
    for (const cfg of this.listSourceConfigs()) {
      // Create a client for this source and fetch.
      const c = new GatewayClient({ baseUrl: cfg.url, token: cfg.token, kind: cfg.kind });
      fetchPromises.push(buildModelEntries(c, cfg.name, cfg.kind));
    }

    // Aggregate all results, deduplicating by the original model ID
    // (later sources overwrite earlier ones).
    const all = (await Promise.all(fetchPromises)).flat();
    const seen = new Map<string, vscode.LanguageModelChatInformation>();
    for (const info of all) {
      const originalId = stripSourceFromId(info.id);
      seen.set(originalId, info);
    }
    return [...seen.values()];
  }

  async provideLanguageModelChatResponse(
    model: vscode.LanguageModelChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    token: vscode.CancellationToken,
  ): Promise<void> {
    // Look up the correct client based on the source encoded in model.id.
    const sourceName = decodeSourceFromId(model.id);
    let client = this.getClient(); // active source client (default)

    if (sourceName) {
      // Find the matching source config and create/use its client.
      const configs = this.listSourceConfigs();
      const cfg = configs.find((c) => c.name === sourceName);
      if (cfg) {
        client = new GatewayClient({ baseUrl: cfg.url, token: cfg.token, kind: cfg.kind });
      }
    }

    if (!client) {
      throw new Error("R4: not connected to Gateway. Run R4: Login to Gateway.");
    }

    // Use the original model ID (without source encoding) for API calls.
    const modelId = stripSourceFromId(model.id);

    const isThinkingModel = modelSupportsThinking(modelId, this.reasoningModels);
    let requestMessages = messages;
    const isNativeVisionModel = llmIdMatchesVisionCatalog(
      modelId,
      this.visionCatalogKeys,
    );
    if (!isNativeVisionModel && messagesHaveImages(messages)) {
      const visionClient = pickVisionClient(
        client,
        this.listSourceConfigs(),
        (cfg) => new GatewayClient({ baseUrl: cfg.url, token: cfg.token, kind: cfg.kind }),
      );
      const resolved = await resolveVisionProxyMessages(visionClient, messages, modelId, token);
      requestMessages = resolved.messages;
      if (resolved.notice) {
        progress.report(new vscode.LanguageModelTextPart(`${resolved.notice}\n\n`));
      }
    }
    const openaiMessages = convertMessages(requestMessages, isThinkingModel, this.reasoningCache);
    const tools = convertTools(options.tools);
    const toolChoice =
      tools.length > 0
        ? options.toolMode === vscode.LanguageModelChatToolMode.Required
          ? "required"
          : "auto"
        : undefined;
    const effortConfig = this.modelEffortConfigs.get(modelId.toLowerCase());
    const thinkingEffort = effortConfig
      ? getConfiguredThinkingEffort(
          options as ModelConfigurationOptions,
          effortConfig.levels,
          effortConfig.default,
        )
      : undefined;
    const totalRequestChars = countMessageChars(openaiMessages);
    const trailingToolResultIds = collectTrailingToolResultIds(openaiMessages);

    const controller = new AbortController();
    token.onCancellationRequested(() => controller.abort());

    const pending = new Map<number, ToolCallAccumulator>();
    const streamState: ResponseStreamState = {
      accumulatedReasoning: "",
      emittedToolCallIds: [],
    };
    const thinkingId = createThinkingSequenceId();
    const thinkingGate = new ThinkingStreamGate();
    const reportThinking = (text: string) => {
      streamState.accumulatedReasoning += text;
      thinkingGate.reportThinking(
        (t) => reportLanguageModelThinking(progress, t, thinkingId),
        text,
      );
    };
    const thinkFilter = new ThinkTagFilter(reportThinking);
    const finalizeThinking = () =>
      thinkingGate.finalize(() => reportLanguageModelThinkingDone(progress));

    const log = getStreamDebugLogger();
    const reasoningFirst = isReasoningFirstModel(modelId);
    let sawSeparateReasoning = false;
    let firstContent = true;

    // Request-lifecycle diagnostics (always on, metadata only — no content).
    // Answers: is the provider invoked during the hang? first-byte latency?
    // does the request finish quickly while Copilot keeps spinning?
    const life = new RequestLifecycle(modelId);
    life.begin(
      `msgs=${openaiMessages.length} tools=${tools.length} ` +
        `toolChoice=${toolChoice ?? "none"} maxIn=${model.maxInputTokens} ` +
        `thinking=${isThinkingModel} reasoningFirst=${reasoningFirst}`,
    );

    try {
      for await (const delta of client.chatCompletionStream(
        {
          model: modelId,
          messages: openaiMessages,
          tools: tools.length > 0 ? tools : undefined,
          tool_choice: toolChoice,
          ...(thinkingEffort ? { reasoning_effort: thinkingEffort } : {}),
          stream_options: { include_usage: true },
        },
        controller.signal,
      )) {
      life.onDelta(delta);
      if (token.isCancellationRequested) {
        cacheReasoningOnCancel(isThinkingModel, streamState, this.reasoningCache);
        break;
      }
      log?.(delta);

      if (delta.usage) {
        this.charsPerToken = updateCharsPerToken(
          totalRequestChars,
          delta.usage.prompt_tokens,
          this.charsPerToken,
        );
        continue;
      }

      if (delta.reasoning) {
        sawSeparateReasoning = true;
        reportThinking(delta.reasoning);
      }

      if (delta.content) {
        if (firstContent && reasoningFirst && !sawSeparateReasoning) {
          thinkFilter.beginInThinking();
        }
        firstContent = false;
        const visible = thinkFilter.push(delta.content);
        if (visible) {
          finalizeThinking();
          thinkingGate.markAnswerStarted();
          progress.report(new vscode.LanguageModelTextPart(visible));
        }
      }

      accumulateToolCalls(delta, pending, streamState);
    }

    const tail = thinkFilter.flush();
    if (tail) {
      finalizeThinking();
      thinkingGate.markAnswerStarted();
      progress.report(new vscode.LanguageModelTextPart(tail));
    }

    finalizeThinking();
    emitToolCalls(pending, progress, streamState);
    reportReplayMarker(streamState, progress);

    cacheStreamedReasoning(
      isThinkingModel,
      streamState.accumulatedReasoning,
      streamState.emittedToolCallIds,
      trailingToolResultIds,
      this.reasoningCache,
    );
    } catch (err) {
      life.error(err);
      throw err;
    } finally {
      life.end(token.isCancellationRequested, streamState.emittedToolCallIds.length);
    }
  }

  async provideTokenCount(
    _model: vscode.LanguageModelChatInformation,
    text: string | vscode.LanguageModelChatRequestMessage,
    _token: vscode.CancellationToken,
  ): Promise<number> {
    return estimateTokenCount(text, this.charsPerToken);
  }

  /** Report token counts so VS Code can display the context window indicator. */
  async provideTokenChatTokens(
    model: vscode.LanguageModelChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    _token: vscode.CancellationToken,
  ): Promise<any> {
    let inputTokens = 0;
    for (const msg of messages) {
      for (const part of msg.content) {
        if (part instanceof vscode.LanguageModelTextPart) {
          inputTokens += estimateTokenCount(part.value, this.charsPerToken);
        }
      }
    }
    return {
      input: inputTokens,
      output: 0,
      maxInputTokens: model.maxInputTokens,
    };
  }
}

/** Register the R4 language model provider and wire it to login/logout. */
export function registerR4LanguageModelProvider(
  context: vscode.ExtensionContext,
  getClient: () => GatewayClient | undefined,
  listLlmSourceConfigs: () => LlmSourceConfig[],
  getSourceLabel: () => string = () => "R4",
): R4LanguageModelProvider | undefined {
  if (typeof vscode.lm?.registerLanguageModelChatProvider !== "function") {
    return undefined;
  }
  const provider = new R4LanguageModelProvider(
    getClient,
    listLlmSourceConfigs,
    getSourceLabel,
  );
  context.subscriptions.push(
    vscode.lm.registerLanguageModelChatProvider(R4_LM_VENDOR, provider),
  );
  return provider;
}

function cacheReasoningOnCancel(
  isThinkingModel: boolean,
  state: ResponseStreamState,
  cache: Map<string, ReasoningEntry>,
): void {
  if (!isThinkingModel || !state.accumulatedReasoning || state.emittedToolCallIds.length === 0) {
    return;
  }
  cacheStreamedReasoning(
    true,
    state.accumulatedReasoning,
    state.emittedToolCallIds,
    [],
    cache,
  );
}

function reportReplayMarker(
  state: ResponseStreamState,
  progress: vscode.Progress<vscode.LanguageModelResponsePart>,
): void {
  if (!state.accumulatedReasoning) {
    return;
  }
  const marker = createReplayMarkerPart({ reasoningText: state.accumulatedReasoning });
  if (marker) {
    progress.report(marker);
  }
}

function accumulateToolCalls(
  delta: StreamDelta,
  pending: Map<number, ToolCallAccumulator>,
  state: ResponseStreamState,
): void {
  if (!delta.tool_calls) {
    return;
  }
  for (const tc of delta.tool_calls) {
    const acc = pending.get(tc.index) ?? { id: "", name: "", args: "" };
    if (tc.id) {
      acc.id = tc.id;
    }
    if (tc.function?.name) {
      acc.name = tc.function.name;
    }
    if (tc.function?.arguments) {
      acc.args += tc.function.arguments;
    }
    pending.set(tc.index, acc);
  }
}

function emitToolCalls(
  pending: Map<number, ToolCallAccumulator>,
  progress: vscode.Progress<vscode.LanguageModelResponsePart>,
  state: ResponseStreamState,
): void {
  const indices = [...pending.keys()].sort((a, b) => a - b);
  for (const idx of indices) {
    const acc = pending.get(idx);
    if (!acc || !acc.name) {
      continue;
    }
    let input: object = {};
    if (acc.args) {
      try {
        input = JSON.parse(acc.args);
      } catch {
        input = {};
      }
    }
    const callId = acc.id || `call_${idx}`;
    state.emittedToolCallIds.push(callId);
    progress.report(new vscode.LanguageModelToolCallPart(callId, acc.name, input));
  }
}
