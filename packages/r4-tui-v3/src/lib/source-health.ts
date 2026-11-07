/**
 * Live health of registered sources, as far as the console can tell
 * WITHOUT a key (G8, onboarding plan 2026-10-15 §5 B).
 *
 * The console never holds or sends a source key: keys stay in the r4 config
 * and only the r4 CLI and the bridge use them. It probes only routes that
 * need none, and never sets an auth header:
 *  - platform gateway : GET {url}/ready — the gateway's unauthenticated
 *                       readiness (gateway/nginx/conf.d/server.conf); a 404
 *                       falls back to GET {url}/health (a direct-UMRS URL,
 *                       as r4's own readiness check does)
 *  - ollama, no key   : GET {url}/api/tags
 *  - public, no key   : GET {url}/models
 *  - any public source with a key (or key status unknown) — a proxied Ollama
 *    too — and bedrock: not probed → "unknown"
 *
 * Authenticated facts (model counts, auth expiry) are the r4 CLI's to report
 * (`r4 source list --health --json`, a later step). They plug in through
 * SourceHealth.state / .models, which sourceHealthSuffix already renders.
 *
 * Always bounded: 5 s per request, parallel across the inventory.
 */

import type { SourceRow } from "./sources";

export type HealthState = "ok" | "down" | "unknown";

export interface SourceHealth {
  source: string;
  ok: boolean;
  /** Absent on older rows → derived from `ok` (see healthState). */
  state?: HealthState;
  latencyMs: number;
  detail: string;
  /** Model count — an authenticated fact only the r4 CLI supplies (later step). */
  models?: number;
}

export interface SourceLike {
  name: string;
  kind?: string; // platform | public | ollama special: kind=ollama
  url: string;
  api_key?: boolean;
  provider?: string | null;
}

const PROBE_TIMEOUT_MS = 5_000;

export const NOT_PROBED_KEY = "not probed — the key stays with r4";
export const NOT_PROBED_PROVIDER = "not probed — provider auth is outside the console";

type ProbePlan = { path: string; fallback?: string } | { skip: string };

/** What the console may ask a source without a key (pure). */
export function probePlan(source: SourceLike): ProbePlan {
  const kind = (source.kind ?? "").toLowerCase();
  const provider = (source.provider ?? "").toLowerCase();
  if (kind === "platform") return { path: "/ready", fallback: "/health" };
  if (provider === "bedrock") return { skip: NOT_PROBED_PROVIDER };
  // A source that holds a key (or may) needs it — Ollama behind an auth
  // proxy included; asking without it would only report a false "down".
  if (source.api_key !== false) return { skip: NOT_PROBED_KEY };
  if (kind === "ollama" || provider === "ollama") return { path: "/api/tags" };
  return { path: "/models" };
}

export function healthState(health: SourceHealth): HealthState {
  return health.state ?? (health.ok ? "ok" : "down");
}

function downHealth(source: string, latencyMs: number, reason: string): SourceHealth {
  return { source, ok: false, state: "down", latencyMs, detail: `${reason} · ${latencyMs}ms` };
}

/** GET with a hard timeout and no credentials of any kind. */
async function keylessGet(url: string, timeoutMs: number): Promise<{ status: number; ok: boolean; text: string }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    const text = response.ok ? "" : (await response.text()).slice(0, 2_000);
    return { status: response.status, ok: response.ok, text };
  } finally {
    clearTimeout(timer);
  }
}

/** "not ready: umrs unreachable" from a gateway /ready body; else "http N". */
function notOkReason(status: number, text: string): string {
  try {
    const checks = (JSON.parse(text) as { checks?: unknown } | null)?.checks;
    if (checks && typeof checks === "object") {
      const failing = Object.entries(checks)
        .filter(([, value]) => value !== "ok")
        .map(([name, value]) => `${name} ${String(value)}`);
      if (failing.length > 0) return `not ready: ${failing.join(", ").slice(0, 80)}`;
    }
  } catch {
    /* not JSON */
  }
  return `http ${status}`;
}

/** One health sample; never throws, never sends a key. */
export async function probeSource(source: SourceLike, timeoutMs = PROBE_TIMEOUT_MS): Promise<SourceHealth> {
  const plan = probePlan(source);
  if ("skip" in plan) {
    return { source: source.name, ok: false, state: "unknown", latencyMs: 0, detail: plan.skip };
  }
  const baseUrl = source.url.replace(/\/+$/, "");
  const started = Date.now();
  try {
    let result = await keylessGet(`${baseUrl}${plan.path}`, timeoutMs);
    if (result.status === 404 && plan.fallback) {
      result = await keylessGet(`${baseUrl}${plan.fallback}`, timeoutMs);
    }
    const latencyMs = Date.now() - started;
    if (result.ok) {
      return { source: source.name, ok: true, state: "ok", latencyMs, detail: `ok · ${latencyMs}ms` };
    }
    return downHealth(source.name, latencyMs, notOkReason(result.status, result.text));
  } catch {
    return downHealth(source.name, Date.now() - started, "unreachable");
  }
}

/** Check every source in parallel; returns the same order as requested. */
export async function probeSources(sources: SourceLike[]): Promise<SourceHealth[]> {
  const tasks = sources.map((source) => probeSource(source));
  return Promise.all(tasks);
}

/** Header marker text: healthy=● greenish, down=◐ warnish, unknown=? dim. */
export function sourceBadge(health: SourceHealth | undefined): { glyph: string; fg: "ok" | "warn" | "dim" } {
  if (!health) return { glyph: "·", fg: "dim" };
  const state = healthState(health);
  if (state === "ok") return { glyph: "●", fg: "ok" };
  if (state === "unknown") return { glyph: "?", fg: "dim" };
  return { glyph: "◐", fg: "warn" };
}

const ROW_GLYPH: Record<HealthState, string> = { ok: "✓", down: "✗", unknown: "?" };

/** " (ok · 12ms · 7 models)" — the detail already carries the latency. */
export function sourceHealthSuffix(health: SourceHealth | undefined): string {
  if (!health) return "";
  const models = health.models === undefined ? "" : ` · ${health.models} models`;
  return ` (${health.detail}${models})`;
}

/** One /source overlay row label (pure): active mark, kind, key flag, health. */
export function sourceRowLabel(row: SourceRow, health: SourceHealth | undefined): string {
  const glyph = health ? ROW_GLYPH[healthState(health)] : "·";
  const keyFlag = row.has_api_key === false ? " · !key" : "";
  const remote = row.remote ? ` · ${row.remote}` : "";
  return `${row.active ? "●" : "○"} ${row.name} · ${row.kind}${keyFlag} ${glyph}${sourceHealthSuffix(health)}${remote}`;
}
