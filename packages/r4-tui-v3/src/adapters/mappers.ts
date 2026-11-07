/**
 * Map r4 CLI / Gateway JSON shapes into v3 UI types.
 */

import type { UiEndpoint, UiModel, UiJob, UiSkill } from "../types";

/** Generic nullish-coalesce helper — absorbing()`?? ` chains across the
 * mappers so each mapper stays CCN-thin while the langauge-native semantics
 * of nullish coalescing stay byte-identical at the request sites. The branch
 * budget lives here exactly once per chain length. */
function coalesce<T>(...vals: Array<T | null | undefined>): T | undefined {
  for (const v of vals) {
    if (v !== null && v !== undefined) return v;
  }
  return undefined;
}

function coalesceOr<T>(fallback: T, ...vals: Array<T | null | undefined>): T {
  return coalesce(...vals) ?? fallback;
}

export interface R4EndpointJson {
  instance_id?: string;
  job_id?: string;
  model?: string;
  served_model_name?: string;
  status?: string;
  endpoint_url?: string;
  service_url?: string;
  tier?: string;
  backend?: string;
  host?: string;
  port?: number;
  owner_type?: string;
  created_at?: string;
  ready_at?: string;
  expires_at?: string;
}

export interface R4ModelJson {
  id?: string;
  model_id?: string;
  name?: string;
  scope?: string;
  type?: string;
  status?: string;
  source?: string;
  kind?: string;
}

export interface R4JobJson {
  id?: string;
  job_id?: string;
  type?: string;
  job_type?: string;
  status?: string;
  project_id?: string;
  created_at?: string;
  updated_at?: string;
  /** P5 capability rows (present only when the backend carries them — P5
   * contract: hidden when absent, never fabricated). */
  state_reason?: string;
  attempt_phase?: string;
  phase?: string;
  terminal_reason?: string;
  attempts?: number;
  desired_max_attempts?: number;
  restart_policy?: string;
}

export interface R4SkillJson {
  name?: string;
  source?: string;
  has_mcp?: boolean;
  description?: string;
  active?: boolean;
  tools?: unknown[];
}

/** T5-topic-5: cold-start absorption — readyAt minus createdAt, only when
 * both timestamps parse cleanly (never fabricated). */
function coldStartMsOf(raw: R4EndpointJson): number | undefined {
  if (!raw.ready_at || !raw.created_at) return undefined;
  const ready = Date.parse(raw.ready_at);
  const created = Date.parse(raw.created_at);
  if (!Number.isFinite(ready) || !Number.isFinite(created)) return undefined;
  const ms = ready - created;
  return ms >= 0 ? ms : undefined;
}

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function endpointSourceNameOf(raw: R4EndpointJson): string | undefined {
  const rec = raw as Record<string, unknown>;
  return coalesce(stringOrUndefined(rec.source_name), stringOrUndefined(rec._source));
}

function endpointIdOf(raw: R4EndpointJson): string {
  return String(coalesceOr(Math.random().toString(36).slice(2), raw.instance_id, raw.job_id));
}

function endpointUrlOf(raw: R4EndpointJson): string {
  return coalesceOr("", raw.endpoint_url, raw.service_url);
}

/** A configured public source's row: the CLI's `instance_id: "public"`
 * (tier "public" or the provider name). */
function isPublicEndpointRow(raw: R4EndpointJson): boolean {
  return raw.instance_id === "public" || raw.tier === "public";
}

export function mapEndpoint(raw: R4EndpointJson): UiEndpoint {
  return {
    id: endpointIdOf(raw),
    model: coalesceOr("unknown", raw.model),
    servedModelName: coalesceOr("unknown", raw.served_model_name, raw.model),
    status: normalizeEndpointStatus(raw.status),
    url: endpointUrlOf(raw),
    coldStartMs: coldStartMsOf(raw),
    sourceName: endpointSourceNameOf(raw),
    tier: normalizeTier(raw.tier),
    backend: coalesceOr("unknown", raw.backend),
    host: coalesceOr("", raw.host),
    port: coalesceOr(0, raw.port),
    ownerType: raw.owner_type,
    createdAt: coalesce(raw.created_at) ?? new Date().toISOString(),
    readyAt: raw.ready_at,
    expiresAt: raw.expires_at,
    isPublic: isPublicEndpointRow(raw) || undefined,
  };
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function stringArrayOrUndefined(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.map((t) => String(t)).filter(Boolean) : undefined;
}

function randomFallbackId(): string {
  return Math.random().toString(36).slice(2);
}

export function mapModel(raw: R4ModelJson): UiModel {
  return {
    id: coalesceOr("unknown", raw.model_id, raw.id, raw.name),
    name: coalesceOr("unknown", raw.name, raw.model_id, raw.id),
    scope: normalizeTier(raw.scope),
    type: raw.type,
    status: raw.status,
    source: stringOrUndefined(raw.source),
    kind: stringOrUndefined(raw.kind),
  };
}

export function mapJob(raw: R4JobJson): UiJob {
  return {
    id: String(coalesceOr(randomFallbackId(), raw.job_id, raw.id)),
    type: coalesceOr("unknown", raw.job_type, raw.type),
    status: normalizeJobStatus(raw.status),
    projectId: raw.project_id,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    // P5 capability rows — present only when the backend carries them;
    // absence is hidden, never fabricated.
    stateReason: coalesce(raw.state_reason) ?? undefined,
    attemptPhase: coalesce(raw.attempt_phase, raw.phase) ?? undefined,
    terminalReason: coalesce(raw.terminal_reason) ?? undefined,
    attempts: numberOrUndefined(raw.attempts),
    desiredMaxAttempts: numberOrUndefined(raw.desired_max_attempts),
    restartPolicy: nonEmptyString(raw.restart_policy),
  };
}

export function mapSkill(raw: R4SkillJson): UiSkill {
  return {
    name: coalesceOr("unknown", raw.name),
    source: coalesceOr("unknown", raw.source),
    hasMcp: coalesceOr(false, raw.has_mcp),
    description: coalesceOr("", raw.description),
    active: typeof raw.active === "boolean" ? raw.active : undefined,
    tools: stringArrayOrUndefined(raw.tools),
  };
}

function normalizeEndpointStatus(status?: string): UiEndpoint["status"] {
  if (!status) return "stopped";
  const s = status.toLowerCase();
  if (["ready", "running"].includes(s)) return "ready";
  if (["provisioning", "pending"].includes(s)) return "provisioning";
  if (["degraded"].includes(s)) return "degraded";
  if (["failed", "error"].includes(s)) return "failed";
  return "stopped";
}

function normalizeJobStatus(status?: string): UiJob["status"] {
  if (!status) return "planned";
  const s = status.toLowerCase();
  if (["planned"].includes(s)) return "planned";
  if (["queued", "pending"].includes(s)) return "queued";
  if (["running", "in_progress", "active"].includes(s)) return "running";
  if (["completed", "done", "success", "succeeded"].includes(s)) return "completed";
  if (["failed", "error"].includes(s)) return "failed";
  if (["cancelled", "canceled"].includes(s)) return "cancelled";
  return "planned";
}

function normalizeTier(tier?: string): "system" | "project" | "user" {
  if (tier === "system" || tier === "project" || tier === "user") return tier;
  return "user";
}
