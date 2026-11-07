/**
 * Gateway HTTP client for the R4 platform.
 *
 * All platform communication goes through Gateway HTTP routes.
 * This module is the single point of contact — no direct UMRS or
 * LiteLLM access is permitted from the extension.
 *
 * Uses Node.js http/https modules instead of fetch for reliable
 * TLS control (VS Code's Electron fetch ignores NODE_TLS_REJECT_UNAUTHORIZED).
 */

import * as vscode from "vscode";
import * as http from "http";
import * as https from "https";

// ── Types ──────────────────────────────────────────────────────

/** Backend flavour a client talks to.
 *
 * ``platform`` — an R4 Gateway; LLM traffic is prefixed with ``/gw/llm``.
 * ``public``   — a plain OpenAI-compatible endpoint (LiteLLM, Ollama,
 *                OpenAI, ...); LLM traffic goes straight to ``/v1``.
 */
export type GatewayKind = "platform" | "public";

export interface GatewayConfig {
  baseUrl: string;
  token: string;
  /** Defaults to "platform" for backwards compatibility. */
  kind?: GatewayKind;
}

/** Resolve the base URL that OpenAI-compatible routes hang off.
 *
 * Public endpoints are commonly registered with the ``/v1`` suffix already
 * present (e.g. ``https://api.example.org/v1``); it is stripped so callers
 * can always append ``/v1/...``.
 */
export function resolveLlmBase(baseUrl: string, kind: GatewayKind): string {
  const trimmed = baseUrl.replace(/\/+$/, "");
  if (kind === "platform") {
    return `${trimmed}/gw/llm`;
  }
  return trimmed.replace(/\/v1$/, "");
}

export interface Endpoint {
  job_id: string;
  model: string;
  status: string;
  backend: string;
  host?: string;
  port?: number;
  served_model_name?: string;
  litellm_url?: string;
  owner_type?: string;
  owner_id?: string;
  created_at?: string;
  ready_at?: string;
  last_used_at?: string;
  expires_at?: string;
  /** Scheduler/readiness detail (e.g. "queued: MaxMemPerLimit"). */
  detail?: string;
  /** Error message recorded when a provision failed. */
  error_message?: string;
  /** Source name injected by CLI --all-sources or multi-source polling. */
  _source?: string;
  /** vLLM --max-model-len (context window served by this endpoint). */
  max_model_len?: number;
  /** Deployment variant name (e.g. "v2", "128k", "tep-128k").
   *  Differentiates multiple endpoints for the same model with different
   *  parameters (ctx length, node count, parallelism strategy, etc.). */
  variant?: string;
}

export interface Job {
  id: string;
  type: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  error?: string;
  attempts?: number;
}

export interface ModelCapabilities {
  supports_vision?: boolean;
  supports_tool_calling?: boolean;
  supports_reasoning?: boolean;
  reasoning_effort_levels?: string[];
  reasoning_effort_default?: string;
}

export interface Model {
  id?: string;
  model_id?: string;
  name: string;
  scope: string;
  owner?: string;
  model_type?: string;
  /** Model availability status: staging, available, deprecated. */
  status?: string;
  storage_ref?: string;
  max_model_len?: number;
  capabilities?: ModelCapabilities;
  /** Source HuggingFace model id (e.g. moonshotai/Kimi-K2.7-Code). */
  huggingface_id?: string;
  /** Source/cluster name this model was listed from (multi-source polling). */
  _source?: string;
}

/** Options for provisioning an inference endpoint. */
export interface ProvisionOptions {
  /** Model name to provision (required). */
  model: string;
  /** Deployment tier / access level: system | project | user. */
  tier?: "system" | "project" | "user";
  /** TTL in seconds (0 = no expiry / never auto-recycle). */
  ttl_sec?: number;
  /** Max context length (vLLM --max-model-len). */
  max_model_len?: number;
  /** Number of GPUs per node. */
  gpu_count?: number;
  /** Number of Slurm nodes for multi-node pipeline parallelism. */
  node_count?: number;
  /** Override Slurm partition (must be in site candidates). */
  partition?: string;
  /** Reasoning-parser control for thinking models. */
  reasoning?: "auto" | "on" | "off";
  /** Environment variables to pass to the vLLM runtime (e.g. VLLM_MM_ENCODER_TP_MODE=data). */
  env_vars?: Record<string, string>;
  /**
   * Whether the server should block until the endpoint is ready. The
   * extension always submits with wait=false so the HTTP call returns a
   * 202 immediately and provisioning continues in the background.
   */
  wait?: boolean;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatRequest {
  model?: string;
  messages: ChatMessage[];
  stream?: boolean;
  temperature?: number;
  max_tokens?: number;
}

export interface ChatChoice {
  index: number;
  message: ChatMessage;
  finish_reason: string | null;
}

export interface ChatResponse {
  id: string;
  choices: ChatChoice[];
  model: string;
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

// ── OpenAI-compatible chat-completion types (LM provider) ──────

/** An OpenAI tool-call as emitted/consumed by the chat-completions API. */
export interface OpenAIToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export type OpenAIMessageContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

/** An OpenAI-compatible chat message (supports tool calls + tool results). */
export interface OpenAIChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | OpenAIMessageContentPart[] | null;
  tool_calls?: OpenAIToolCall[];
  tool_call_id?: string;
  name?: string;
  /** Reasoning text replayed on tool-call turns (vLLM / Kimi / DeepSeek). */
  reasoning_content?: string;
}

/** An OpenAI tool definition (function calling). */
export interface OpenAITool {
  type: "function";
  function: { name: string; description?: string; parameters?: object };
}

/** A full OpenAI chat-completion request. */
export interface ChatCompletionRequest {
  model: string;
  messages: OpenAIChatMessage[];
  tools?: OpenAITool[];
  tool_choice?: "auto" | "required" | "none";
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
  /** Thinking effort forwarded when the model picker exposes it. */
  reasoning_effort?: string;
  /** OpenAI-specific: request usage metadata at end of stream. */
  stream_options?: { include_usage: boolean };
}

/** A single streamed delta from the chat-completions SSE stream. */
export interface StreamUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface StreamDelta {
  content?: string;
  /**
   * Extra reasoning text emitted by vLLM --reasoning-parser (kimi_k2, glm45,
   * gemma4). vLLM's OpenAI server names this field `reasoning_content`; some
   * proxies (LiteLLM) surface it as `reasoning`. Both are normalised here.
   */
  reasoning?: string;
  tool_calls?: Array<{
    index: number;
    id?: string;
    function?: { name?: string; arguments?: string };
  }>;
  usage?: StreamUsage;
}

/** An OpenAI model object from /gw/llm/v1/models. */
export interface OpenAIModel {
  id: string;
  object?: string;
  owned_by?: string;
  max_model_len?: number;
  /** LiteLLM/OpenAI-style context advertisement (public gateways). */
  max_input_tokens?: number;
  max_output_tokens?: number;
  /** Alternative context-window field used by some OpenAI-compatible servers. */
  context_length?: number;
}

/**
 * Resolve the served context window for an OpenAI model entry.
 *
 * vLLM-backed platform gateways report `max_model_len`. Public
 * OpenAI-compatible gateways (LiteLLM) report `max_input_tokens` /
 * `context_length` instead. Returns undefined when nothing is advertised so
 * callers can apply their own fallback.
 */
export function normalizeContextWindow(m: OpenAIModel): number | undefined {
  for (const candidate of [m.max_model_len, m.max_input_tokens, m.context_length]) {
    if (typeof candidate === "number" && candidate > 0) return candidate;
  }
  return undefined;
}

/**
 * Last-resort context windows for well-known public models whose gateway omits
 * every context field from `/v1/models` (some LiteLLM proxies return only
 * id/object/created/owned_by, and the richer `/model/info` route is admin-only).
 * Matched as a case-insensitive substring of the model id. The user setting
 * `r4.modelContextWindows` (id → tokens) always wins over this table.
 */
const KNOWN_PUBLIC_CONTEXT_WINDOWS: ReadonlyArray<readonly [RegExp, number]> = [
  // Verified 2026-08-15 against api.rikyu.r-ccs.riken.jp/v1 overflow errors.
  [/kimi-k3/i, 1_048_576],
  [/glm-5\.2/i, 1_048_576],
  [/kimi-k2/i, 262_144],
  [/qwen3\.6-35b/i, 262_144],
];

/** Resolve a fallback context window for a model id, honoring user overrides. */
export function fallbackContextWindow(modelId: string): number | undefined {
  const id = (modelId ?? "").trim();
  if (!id) return undefined;
  const key = id.toLowerCase();
  const overrides = vscode.workspace
    .getConfiguration("r4")
    .get<Record<string, number>>("modelContextWindows", {});
  for (const [name, tokens] of Object.entries(overrides ?? {})) {
    if (
      typeof tokens === "number" &&
      tokens > 0 &&
      (key === name.toLowerCase() || key.includes(name.toLowerCase()))
    ) {
      return tokens;
    }
  }
  // Report each model's true served window (verified against the gateway's
  // overflow errors). Public/LiteLLM `/v1/models` omits every context field,
  // so without this fallback large public models would collapse to the 32k
  // default. Not clamped: the model picker must show the real per-model
  // window (e.g. kimi-k3/glm-5.2 = 1M, qwen3.6-35b/kimi-k2.6 = 256k).
  for (const [pattern, tokens] of KNOWN_PUBLIC_CONTEXT_WINDOWS) {
    if (pattern.test(id)) return tokens;
  }
  return undefined;
}

// ── MCP types ──────────────────────────────────────────────────

export interface MCPTool {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
  server?: string;
}

export interface MCPServer {
  name: string;
  id?: string;
  server_id?: string;
  version?: string;
  url?: string;
  status?: string;
  description?: string;
  tools?: MCPTool[];
}

export interface MCPToolCallRequest {
  tool: string;
  arguments: Record<string, unknown>;
}

export interface MCPToolCallResponse {
  content: unknown[];
  isError: boolean;
  server?: string;
}

export interface McpAppsSSEActivation {
  status: string;
  sseUrl: string;
}

// ── Workflow types ─────────────────────────────────────────────

export interface Workflow {
  workflow_id: string;
  name: string;
  description?: string;
  parameters?: Record<string, unknown>[];
  tags?: string[];
}

export interface WorkflowExecution {
  execution_id: string;
  workflow_id: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  error?: string;
  progress?: number;
}

export interface WorkflowResult {
  execution_id: string;
  workflow_id: string;
  status: string;
  outputs?: Record<string, unknown>;
  artifacts?: string[];
  duration_seconds?: number;
  error?: string;
}

// ── Error ──────────────────────────────────────────────────────

export class GatewayError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body?: string,
  ) {
    super(message);
    this.name = "GatewayError";
  }
}

// ── Client ─────────────────────────────────────────────────────

/** Check whether TLS skip-verify is enabled in settings. */
function shouldSkipTls(): boolean {
  return vscode.workspace
    .getConfiguration("r4")
    .get<boolean>("tlsSkipVerify", false);
}

/** Low-level Node.js HTTP(S) request that respects r4.tlsSkipVerify. */
function nodeRequest(
  url: string,
  options: {
    method: string;
    headers: Record<string, string>;
    body?: string;
    timeoutMs?: number;
  },
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const isHttps = parsed.protocol === "https:";
    const mod = isHttps ? https : http;

    const reqOptions: http.RequestOptions = {
      method: options.method,
      hostname: parsed.hostname,
      port: parsed.port || (isHttps ? 443 : 80),
      path: parsed.pathname + parsed.search,
      headers: options.headers,
      timeout: options.timeoutMs ?? 10_000,
    };

    if (isHttps && shouldSkipTls()) {
      (reqOptions as https.RequestOptions).rejectUnauthorized = false;
    }

    const req = mod.request(reqOptions, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => {
        resolve({
          status: res.statusCode ?? 0,
          body: Buffer.concat(chunks).toString("utf-8"),
        });
      });
    });

    req.on("error", (err) => reject(err));
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("request timed out"));
    });

    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

export class GatewayClient {
  private baseUrl: string;
  private token: string;
  private kind: GatewayKind;

  constructor(config: GatewayConfig) {
    // Strip trailing slash
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.token = config.token;
    this.kind = config.kind ?? "platform";
  }

  updateConfig(config: Partial<GatewayConfig>): void {
    if (config.baseUrl) {
      this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    }
    if (config.token) {
      this.token = config.token;
    }
    if (config.kind) {
      this.kind = config.kind;
    }
  }

  /** Whether this client talks to a plain OpenAI-compatible endpoint. */
  isPublic(): boolean {
    return this.kind === "public";
  }

  /**
   * Guard platform-only features (endpoints provisioning, jobs, MCP,
   * workflows). Public sources are plain OpenAI-compatible endpoints with no
   * ``/gw/*`` control plane, so calling these against a public source would hit
   * a non-existent route and surface a cryptic 404. Fail fast with a clear,
   * actionable message instead so the two source kinds never get mixed up.
   */
  private assertPlatform(feature: string): void {
    if (this.kind === "public") {
      throw new GatewayError(
        `${feature} is not available for public endpoints. This is a platform (UMRS gateway) feature — switch to a platform source to use it.`,
        400,
      );
    }
  }

  /** Base URL for OpenAI-compatible routes (``/v1/...``). */
  private llmBase(): string {
    return resolveLlmBase(this.baseUrl, this.kind);
  }

  // ── Generic request ────────────────────────────────────────

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    _signal?: AbortSignal,
    timeoutMs?: number,
  ): Promise<T> {
    // ``path`` may be an absolute URL (public OpenAI-compatible routes are
    // built from the resolved LLM base rather than the gateway base).
    const url = /^https?:\/\//i.test(path) ? path : `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      Accept: "application/json",
    };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    let resp: { status: number; body: string };
    try {
      resp = await nodeRequest(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        timeoutMs: timeoutMs ?? 10_000,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new GatewayError(`Gateway ${method} ${path} — ${msg}`, 0);
    }

    if (resp.status < 200 || resp.status >= 300) {
      throw new GatewayError(
        `Gateway ${method} ${path} returned ${resp.status}`,
        resp.status,
        resp.body,
      );
    }
    return JSON.parse(resp.body) as T;
  }

  // ── Health ─────────────────────────────────────────────────

  async checkHealth(): Promise<boolean> {
    // Public OpenAI-compatible endpoints have no /health route; a successful
    // model listing is the equivalent liveness signal.
    const probeUrl =
      this.kind === "public" ? `${this.llmBase()}/v1/models` : `${this.baseUrl}/health`;
    try {
      const resp = await nodeRequest(probeUrl, {
        method: "GET",
        headers: { Authorization: `Bearer ${this.token}` },
        timeoutMs: 5_000,
      });
      return resp.status >= 200 && resp.status < 300;
    } catch {
      return false;
    }
  }

  // ── Models ─────────────────────────────────────────────────

  async listModels(): Promise<Model[]> {
    // Public OpenAI-compatible endpoints have no UMRS registry; their catalog
    // is exactly what /v1/models advertises.
    if (this.kind === "public") {
      const llm = await this.listLLMModels();
      return llm.map((m) => ({
        id: m.id,
        model_id: m.id,
        name: m.id,
        scope: "public",
        owner: m.owned_by,
        status: "available",
        max_model_len: m.max_model_len,
      }));
    }
    const data = await this.request<{ models?: Model[]; data?: Model[] }>(
      "GET",
      "/gw/models",
    );
    return (data.models ?? data.data ?? []).map((model) => ({
      ...model,
      id: model.id ?? model.model_id ?? model.name,
    }));
  }

  // ── Endpoints ──────────────────────────────────────────────

  async listEndpoints(): Promise<Endpoint[]> {
    // A public endpoint is always "up": there is no provisioning lifecycle to
    // report, so every advertised model is surfaced as a ready endpoint.
    if (this.kind === "public") {
      const llm = await this.listLLMModels();
      return llm.map((m) => ({
        job_id: `public:${m.id}`,
        model: m.id,
        status: "ready",
        backend: "public",
        served_model_name: m.id,
        max_model_len: m.max_model_len,
      }));
    }
    const data = await this.request<{ endpoints?: Endpoint[]; data?: Endpoint[] }>(
      "GET",
      "/gw/endpoints",
    );
    return data.endpoints ?? data.data ?? [];
  }

  async provisionEndpoint(opts: ProvisionOptions | string): Promise<Endpoint> {
    // Provisioning (user intent to start an endpoint) must hit the provision
    // route. NGINX rewrites POST /gw/endpoints/provision -> /umrs/endpoints/provision.
    // The bare POST /gw/endpoints maps to the internal register handler, whose
    // EndpointRegisterRequest requires project_id/job_id/host/port and rejects a
    // { model }-only body with HTTP 422.
    //
    // We always submit with wait=false so the Gateway returns a 202 as soon as
    // the Slurm/K8s job is submitted, rather than holding the connection open
    // until the endpoint is ready (which routinely exceeded the client timeout
    // and surfaced as "Gateway error 0 — request timed out"). The endpoint
    // views poll for the READY transition. Even so, sbatch submission itself
    // can take a while under load, so allow a generous per-call timeout.
    //
    // Retry policy: transient network failures and 5xx errors are retried up to
    // 3 times with exponential backoff (2s, 4s, 8s). Timeouts and connection
    // refused are the most common transient failures during sbatch submission.
    this.assertPlatform("Endpoint provisioning");
    const o: ProvisionOptions =
      typeof opts === "string" ? { model: opts } : opts;
    const body: Record<string, unknown> = { model: o.model, wait: false };
    if (o.tier !== undefined) {
      body.tier = o.tier;
    }
    if (o.ttl_sec !== undefined) {
      body.ttl_sec = o.ttl_sec;
    }
    if (o.max_model_len !== undefined) {
      body.max_model_len = o.max_model_len;
    }
    if (o.gpu_count !== undefined) {
      body.gpu_count = o.gpu_count;
    }
    if (o.node_count !== undefined) {
      body.node_count = o.node_count;
    }
    if (o.partition !== undefined && o.partition !== "") {
      body.partition = o.partition;
    }
    if (o.reasoning !== undefined) {
      body.reasoning = o.reasoning;
    }
    if (o.env_vars) {
      body.env_vars = o.env_vars;
    }

    const maxRetries = 3;
    let lastError: Error | undefined;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        return await this.request<Endpoint>(
          "POST",
          "/gw/endpoints/provision",
          body,
          undefined,
          120_000,
        );
      } catch (err: unknown) {
        lastError = err instanceof Error ? err : new Error(String(err));

        // Only retry on transient failures: network errors, timeouts, 5xx
        const isRetryable =
          lastError instanceof GatewayError
            ? lastError.status === 0 || lastError.status >= 500
            : true; // non-GatewayError = network/timeout

        if (!isRetryable || attempt >= maxRetries) {
          throw lastError;
        }

        const delayMs = Math.pow(2, attempt) * 1000; // 2s, 4s, 8s
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
    }
    // Should be unreachable — TypeScript narrows via the throw above
    throw lastError!;
  }

  /**
   * Poll for endpoint readiness after provision submission.
   *
   * After a successful provision (202 Accepted), this method polls
   * GET /gw/endpoints until the endpoint transitions to "ready" or
   * a terminal state (failed/terminated/expired), or the timeout expires.
   *
   * Polling uses progressive backoff: 3s → 5s → 10s → 15s → 20s…
   * capped at maxIntervalMs. This gives the user real-time feedback
   * on the provisioning progress (queued → allocated → starting → ready).
   *
   * @param modelName  The model name used in the provision request.
   * @param jobId      Optional job_id from the provision response to
   *                   target a specific endpoint.
   * @param timeoutMs  Maximum total time to wait (default 10 minutes).
   * @param onProgress Callback for each poll cycle with current endpoint
   *                   state, so the UI can show progress.
   * @returns The ready Endpoint, or throws if timeout/terminal.
   */
  async waitForEndpointReady(
    modelName: string,
    jobId?: string,
    timeoutMs: number = 600_000,
    onProgress?: (endpoint: Endpoint) => void,
  ): Promise<Endpoint> {
    const deadline = Date.now() + timeoutMs;
    let interval = 3_000; // start at 3s
    const maxInterval = 20_000;
    let consecutiveErrors = 0;

    const terminalStates = new Set(["failed", "terminated", "expired", "cancelled"]);

    while (Date.now() < deadline) {
      let endpoints: Endpoint[];
      try {
        endpoints = await this.listEndpoints();
        consecutiveErrors = 0; // reset on success
      } catch (err: unknown) {
        consecutiveErrors++;
        // Transient failures (network, 5xx): retry with backoff. Give up
        // after 5 consecutive failures to avoid infinite loop.
        if (consecutiveErrors >= 5) {
          const msg = err instanceof Error ? err.message : String(err);
          throw new Error(
            `Lost connectivity during endpoint readiness polling after ${consecutiveErrors} consecutive failures: ${msg}`,
          );
        }
        // Progressive backoff on errors: 5s, 10s, 15s, 20s, 20s
        const errorDelay = Math.min(5_000 * consecutiveErrors, maxInterval);
        await new Promise((r) => setTimeout(r, errorDelay));
        continue;
      }

      const match = jobId
        ? endpoints.find((ep) => ep.job_id === jobId)
        : endpoints.find(
            (ep) => ep.model === modelName && !terminalStates.has(ep.status.toLowerCase()),
          );

      if (!match) {
        // No matching endpoint found yet — backend may still be registering
        await new Promise((r) => setTimeout(r, interval));
        interval = Math.min(interval * 1.5, maxInterval);
        continue;
      }

      if (onProgress) {
        onProgress(match);
      }

      if (match.status.toLowerCase() === "ready") {
        return match;
      }

      if (terminalStates.has(match.status.toLowerCase())) {
        const reason = match.detail ?? match.error_message ?? "unknown error";
        throw new Error(
          `Endpoint provisioning failed: ${match.status} — ${reason}`,
        );
      }

      await new Promise((r) => setTimeout(r, interval));
      interval = Math.min(interval * 1.5, maxInterval);
    }

    throw new Error(
      `Timed out waiting for ${modelName} endpoint to become ready after ${timeoutMs / 1000}s`,
    );
  }

  async teardownEndpoint(endpointId: string, force = false): Promise<void> {
    // Hit the explicit `/deprovision` sub-route so the request maps to the
    // UMRS deprovision handler (which runs scancel / k8s delete and releases
    // the port), NOT the registry-only `remove_endpoint` handler.
    //
    // The prod NGINX edge rewrites `/gw/endpoints/{id}/deprovision` ->
    // `/umrs/endpoints/{id}/deprovision`; the legacy Python gateway exposes
    // the same sub-route. A bare `DELETE /gw/endpoints/{id}` under NGINX
    // rewrites to `/umrs/endpoints/{id}` which only unregisters the endpoint
    // and leaves the Slurm/K8s job running — exactly what we must avoid.
    //
    // `force` issues an immediate hard cancel. It is used when cancelling an
    // endpoint that is still provisioning so the launching job is killed right
    // away rather than waiting for a graceful in-use check.
    this.assertPlatform("Endpoint teardown");
    const qs = force ? "?force=true" : "";
    await this.request<unknown>(
      "DELETE",
      `/gw/endpoints/${encodeURIComponent(endpointId)}/deprovision${qs}`,
    );
  }

  // ── Jobs ───────────────────────────────────────────────────

  async listJobs(status?: string): Promise<Job[]> {
    this.assertPlatform("Jobs");
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    const data = await this.request<{ jobs?: Job[]; data?: Job[] }>(
      "GET",
      `/gw/jobs${qs}`,
    );
    return data.jobs ?? data.data ?? [];
  }

  async cancelJob(jobId: string): Promise<void> {
    this.assertPlatform("Job cancellation");
    await this.request<unknown>("POST", `/gw/jobs/${encodeURIComponent(jobId)}/cancel`);
  }

  async getJobLogs(jobId: string): Promise<string> {
    this.assertPlatform("Job logs");
    const data = await this.request<{ logs?: string; data?: string }>(
      "GET",
      `/gw/jobs/${encodeURIComponent(jobId)}/logs`,
    );
    return data.logs ?? data.data ?? "";
  }

  // ── Chat (non-streaming) ──────────────────────────────────

  async chat(req: ChatRequest): Promise<ChatResponse> {
    return this.request<ChatResponse>(
      "POST",
      `${this.llmBase()}/v1/chat/completions`,
      {
        ...req,
        stream: false,
      },
    );
  }

  // ── Chat (streaming) ──────────────────────────────────────

  async *chatStream(
    req: ChatRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<string, void, unknown> {
    const response = await openStreamConnection(this.llmBase(), this.token, req, signal);
    await validateStreamResponse(response);
    yield* parseSSEStream(response, signal);
  }

  // ── LLM provider (OpenAI-compatible, tool-aware) ──────────

  /** List models that have a ready endpoint (OpenAI /v1/models, aggregated). */
  async listLLMModels(): Promise<OpenAIModel[]> {
    const data = await this.request<{ data?: OpenAIModel[] }>(
      "GET",
      `${this.llmBase()}/v1/models`,
    );
    return (data.data ?? []).map((m) => ({
      ...m,
      // Public/OpenAI-compatible gateways (LiteLLM) advertise the context
      // window as max_input_tokens or context_length instead of the vLLM
      // max_model_len field. Normalize so every consumer sees one field;
      // without this the extension falls back to a 32k default and the model
      // picker reports a ~40k context window for 128k–1M models. When the
      // gateway advertises nothing at all, fall back to a known-model table
      // (or the r4.modelContextWindows override) so large public models like
      // kimi-k3 are not clamped to the 32k default.
      max_model_len: normalizeContextWindow(m) ?? fallbackContextWindow(m.id),
    }));
  }

  /** Stream a chat completion, surfacing both text and tool-call deltas. */
  async *chatCompletionStream(
    req: ChatCompletionRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamDelta, void, unknown> {
    const response = await openChatCompletionConnection(
      this.llmBase(),
      this.token,
      req,
      signal,
    );
    await validateStreamResponse(response);
    yield* parseChatCompletionSSE(response, signal);
  }

  // ── MCP ────────────────────────────────────────────────────

  async listMCPTools(): Promise<MCPTool[]> {
    this.assertPlatform("MCP tools");
    const data = await this.request<{ tools?: MCPTool[]; data?: MCPTool[] }>(
      "GET",
      "/gw/mcp/tools",
    );
    return data.tools ?? data.data ?? [];
  }

  async listMCPServers(): Promise<MCPServer[]> {
    this.assertPlatform("MCP servers");
    const data = await this.request<{ servers?: MCPServer[]; data?: MCPServer[] }>(
      "GET",
      "/gw/mcp/servers",
    );
    return data.servers ?? data.data ?? [];
  }

  async callMCPTool(req: MCPToolCallRequest, timeoutMs?: number): Promise<MCPToolCallResponse> {
    this.assertPlatform("MCP tool calls");
    const raw = await this.request<Record<string, unknown>>(
      "POST",
      "/gw/mcp/call",
      req,
      undefined,
      timeoutMs ?? 60_000,
    );

    // Normalize: some server versions return "is_error" (snake_case).
    const isError = raw.is_error === true || raw.isError === true;
    const content = raw.content;
    const server = raw.server;

    return {
      content: (content ?? []) as unknown[],
      isError,
      server: typeof server === "string" ? server : undefined,
    };
  }

  async activateMcpAppsSSE(): Promise<McpAppsSSEActivation> {
    this.assertPlatform("MCP Apps");
    // R4 drift (2026-08-22): this route exists only in the retired Python
    // gateway — NGINX production edge never mounts /gw/mcp/sse/*. Pre-probe
    // so callers get a precise "not exposed by this gateway" message.
    await this.request(
      "HEAD",
      "/gw/mcp/sse/activate",
    ).catch((err) => {
      if ((err as GatewayError)?.status === 404 || (err as GatewayError)?.status === 405) {
        throw new GatewayError(
          "MCP Apps SSE activation is not exposed by this gateway (route " +
            "retired; exists only on the legacy Python gateway)",
          404,
        );
      }
      // Any other failure: fall through to the real request and let its
      // error surface drive the user-facing message.
      return;
    });
    const data = await this.request<McpAppsSSEActivation>(
      "GET",
      "/gw/mcp/sse/activate",
    );

    // Gateway returns a relative path; present callers with absolute URL.
    const raw = data.sseUrl ?? "";
    if (!raw) {
      return { ...data, sseUrl: "" };
    }

    if (/^https?:\/\//i.test(raw)) {
      return data;
    }

    return {
      ...data,
      sseUrl: `${this.baseUrl}${raw.startsWith("/") ? raw : `/${raw}`}`,
    };
  }

  // ── Workflows ──────────────────────────────────────────────

  async listWorkflows(): Promise<Workflow[]> {
    this.assertPlatform("Workflows");
    const data = await this.request<{ workflows?: Workflow[]; data?: Workflow[] }>(
      "GET",
      "/gw/workflows",
    );
    return data.workflows ?? data.data ?? [];
  }

  async triggerWorkflow(
    workflowName: string,
    parameters?: Record<string, unknown>,
  ): Promise<WorkflowExecution> {
    this.assertPlatform("Workflow triggering");
    return this.request<WorkflowExecution>(
      "POST",
      `/gw/workflows/${encodeURIComponent(workflowName)}/trigger`,
      { parameters: parameters ?? {} },
    );
  }

  async getWorkflowExecution(executionId: string): Promise<WorkflowExecution> {
    return this.request<WorkflowExecution>(
      "GET",
      `/gw/workflows/executions/${encodeURIComponent(executionId)}`,
    );
  }

  async getWorkflowResults(executionId: string): Promise<WorkflowResult> {
    return this.request<WorkflowResult>(
      "GET",
      `/gw/workflows/executions/${encodeURIComponent(executionId)}/results`,
    );
  }

  async cancelWorkflowExecution(executionId: string): Promise<void> {
    await this.request<unknown>(
      "POST",
      `/gw/workflows/executions/${encodeURIComponent(executionId)}/cancel`,
    );
  }
}

// ── Streaming helpers (standalone for lizard compatibility) ───

/** Open an HTTP(S) connection for SSE streaming. */
function openStreamConnection(
  llmBase: string,
  token: string,
  req: ChatRequest,
  signal?: AbortSignal,
): Promise<http.IncomingMessage> {
  const url = new URL(`${llmBase}/v1/chat/completions`);
  const isHttps = url.protocol === "https:";
  const mod = isHttps ? https : http;

  const reqOptions: http.RequestOptions = {
    method: "POST",
    hostname: url.hostname,
    port: url.port || (isHttps ? 443 : 80),
    path: url.pathname + url.search,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    timeout: 60_000,
  };

  if (isHttps && shouldSkipTls()) {
    (reqOptions as https.RequestOptions).rejectUnauthorized = false;
  }

  const body = JSON.stringify({ ...req, stream: true });

  return new Promise<http.IncomingMessage>((resolve, reject) => {
    const httpReq = mod.request(reqOptions, resolve);
    httpReq.on("error", reject);
    httpReq.on("timeout", () => {
      httpReq.destroy();
      reject(new Error("stream request timed out"));
    });
    if (signal) {
      signal.addEventListener("abort", () => httpReq.destroy(), {
        once: true,
      });
    }
    httpReq.write(body);
    httpReq.end();
  });
}

/** Validate the stream response status code. */
async function validateStreamResponse(
  response: http.IncomingMessage,
): Promise<void> {
  if (
    response.statusCode &&
    response.statusCode >= 200 &&
    response.statusCode < 300
  ) {
    return;
  }
  const chunks: Buffer[] = [];
  for await (const chunk of response) {
    chunks.push(chunk as Buffer);
  }
  const bodyText = Buffer.concat(chunks).toString("utf-8");
  const bodySnippet = bodyText.slice(0, 300).replace(/\n/g, " ");
  throw new GatewayError(
    `Gateway chat stream returned ${response.statusCode}: ${bodySnippet}`,
    response.statusCode ?? 0,
    bodyText,
  );
}

/** Parse SSE events from a streaming HTTP response. */
async function* parseSSEStream(
  response: http.IncomingMessage,
  signal?: AbortSignal,
): AsyncGenerator<string, void, unknown> {
  let buffer = "";
  for await (const chunk of response) {
    if (signal?.aborted) {
      response.destroy();
      break;
    }
    buffer += (chunk as Buffer).toString("utf-8");
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const delta = extractSSEDelta(line);
      if (delta === null) {
        return; // [DONE]
      }
      if (delta) {
        yield delta;
      }
    }
  }
}

/** Extract content delta from a single SSE line. Returns null for [DONE], empty string to skip. */
function extractSSEDelta(line: string): string | null {
  const trimmed = line.trim();
  if (!trimmed || !trimmed.startsWith("data: ")) {
    return "";
  }
  const payload = trimmed.slice(6);
  if (payload === "[DONE]") {
    return null;
  }
  try {
    const parsed = JSON.parse(payload);
    return parsed.choices?.[0]?.delta?.content ?? "";
  } catch {
    return ""; // Malformed JSON chunk — skip
  }
}

/** Open an HTTP(S) connection for a tool-aware chat-completion SSE stream. */
function openChatCompletionConnection(
  llmBase: string,
  token: string,
  req: ChatCompletionRequest,
  signal?: AbortSignal,
): Promise<http.IncomingMessage> {
  const url = new URL(`${llmBase}/v1/chat/completions`);
  const isHttps = url.protocol === "https:";
  const mod = isHttps ? https : http;

  const reqOptions: http.RequestOptions = {
    method: "POST",
    hostname: url.hostname,
    port: url.port || (isHttps ? 443 : 80),
    path: url.pathname + url.search,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    timeout: 300_000,
  };

  if (isHttps && shouldSkipTls()) {
    (reqOptions as https.RequestOptions).rejectUnauthorized = false;
  }

  const body = JSON.stringify({ ...req, stream: true });

  return new Promise<http.IncomingMessage>((resolve, reject) => {
    const httpReq = mod.request(reqOptions, resolve);
    httpReq.on("error", reject);
    httpReq.on("timeout", () => {
      httpReq.destroy();
      reject(new Error("chat completion stream timed out"));
    });
    if (signal) {
      signal.addEventListener("abort", () => httpReq.destroy(), { once: true });
    }
    httpReq.write(body);
    httpReq.end();
  });
}

/** Parse SSE events into structured deltas (text + tool calls). */
async function* parseChatCompletionSSE(
  response: http.IncomingMessage,
  signal?: AbortSignal,
): AsyncGenerator<StreamDelta, void, unknown> {
  let buffer = "";
  for await (const chunk of response) {
    if (signal?.aborted) {
      response.destroy();
      break;
    }
    buffer += (chunk as Buffer).toString("utf-8");
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const delta = extractStreamDelta(line);
      if (delta === null) {
        return; // [DONE]
      }
      if (delta) {
        yield delta;
      }
    }
  }
}

/** Extract a structured delta from one SSE line. null = [DONE], undefined = skip. */
function extractStreamDelta(line: string): StreamDelta | null | undefined {
  const trimmed = line.trim();
  if (!trimmed || !trimmed.startsWith("data: ")) {
    return undefined;
  }
  const payload = trimmed.slice(6);
  if (payload === "[DONE]") {
    return null;
  }
  try {
    const parsed = JSON.parse(payload);
    if (parsed.usage && typeof parsed.usage.prompt_tokens === "number") {
      return {
        usage: {
          prompt_tokens: parsed.usage.prompt_tokens,
          completion_tokens: parsed.usage.completion_tokens ?? 0,
          total_tokens: parsed.usage.total_tokens ?? parsed.usage.prompt_tokens,
        },
      };
    }
    const delta = parsed.choices?.[0]?.delta;
    if (!delta) {
      return undefined;
    }
    const out: StreamDelta = {};
    if (typeof delta.content === "string" && delta.content.length > 0) {
      out.content = delta.content;
    }
    // vLLM emits parsed reasoning as `reasoning_content`; LiteLLM/other proxies
    // may rename it to `reasoning`. Accept either so the thinking text routes to
    // the collapsible thinking UI instead of leaking into the answer.
    const reasoning =
      typeof delta.reasoning_content === "string" && delta.reasoning_content.length > 0
        ? delta.reasoning_content
        : typeof delta.reasoning === "string" && delta.reasoning.length > 0
          ? delta.reasoning
          : undefined;
    if (reasoning !== undefined) {
      out.reasoning = reasoning;
    }
    if (Array.isArray(delta.tool_calls)) {
      out.tool_calls = delta.tool_calls;
    }
    return out.content !== undefined ||
      out.reasoning !== undefined ||
      out.tool_calls !== undefined
      ? out
      : undefined;
  } catch {
    return undefined; // Malformed JSON chunk — skip
  }
}

