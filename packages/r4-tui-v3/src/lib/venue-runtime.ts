/**
 * Frontend-venue (remote session) runtime helpers — pure, bridge-free.
 *
 * Wave 2026-09-21 (ADR-046 A2): a `frontend` session's turns execute in the
 * sandbox worker on umrs-front. The TUI projects the server's runtime row
 * (state / phase / partial text / lease) into these shapes; nothing here
 * fabricates state — absent fields stay absent.
 */

/** ADR-099 D-2: the user chooses the web access when the session opens
 * (lib/web-access.ts); the remote source picker no longer cycles a
 * preselected `public` (`w`). */
export type WebPolicy = "public" | "allowlist" | "none";

export interface VenueRuntime {
  /** Server row state: provisioning | running | failed | stopped | "" (unknown). */
  state: string;
  /** Worker-reported phase: idle | running | awaiting_approval | awaiting_user | error | exited | "". */
  phase: string;
  turnSeq: number;
  partial: string;
  detail: string;
  /** Live tool-activity lines the worker streams (r4 venue worker, 2026-09-27);
   * [] when the deployment predates the field — never fabricated. */
  toolActivity: string[];
  leaseRemainingSec: number | null;
  sourceNames: string[];
  defaultSource: string;
  webPolicy: string;
  reason: string;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function num(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** Map a bridge `venue` payload (session.venue_status / venue_start / venue_sync). */
export function mapVenueRuntime(
  venue: unknown,
  extra: { runtime?: unknown; lease_remaining_sec?: unknown; venue_state?: unknown } = {},
): VenueRuntime {
  const v = (venue && typeof venue === "object" ? venue : {}) as Record<string, unknown>;
  const rt = ((extra.runtime ?? v.runtime) && typeof (extra.runtime ?? v.runtime) === "object"
    ? (extra.runtime ?? v.runtime)
    : {}) as Record<string, unknown>;
  const remaining =
    typeof extra.lease_remaining_sec === "number" ? extra.lease_remaining_sec : null;
  return {
    state: str(extra.venue_state ?? v.state),
    phase: str(rt.phase),
    turnSeq: num(rt.turn_seq),
    partial: str(rt.partial),
    detail: str(rt.detail),
    toolActivity: Array.isArray(rt.tool_activity) ? rt.tool_activity.map(str) : [],
    leaseRemainingSec: remaining,
    sourceNames: Array.isArray(v.source_names) ? v.source_names.map(str) : [],
    defaultSource: str(v.default_source),
    webPolicy: str(v.web_policy),
    reason: str(v.reason),
  };
}

/** Fast-poll cadence after `venue/start` answers `provisioning` (bounded). */
export const START_POLL_MS = 2_000;
export const START_POLL_MAX_MS = 300_000;

export interface StartTransition {
  kind: "running" | "failed" | "stopped" | "pending";
  text: string;
}

/** What a post-start status row means for the user (null = status unavailable yet). */
export function startTransition(rt: VenueRuntime | null): StartTransition {
  if (!rt) return { kind: "pending", text: "remote session status not available yet" };
  if (rt.state === "running") {
    return { kind: "running", text: "remote session running on the server" };
  }
  if (rt.state === "failed") {
    return { kind: "failed", text: "remote session failed to start: " + (rt.reason || "unknown") };
  }
  if (rt.state === "stopped") {
    return { kind: "stopped", text: "remote session stopped before it started" };
  }
  return { kind: "pending", text: "remote session provisioning on the server…" };
}

/** Snapshot-side projection (`session.venue_runtime` on every bridge state). */
export function mapSnapshotVenueRuntime(raw: unknown): VenueRuntime | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  return {
    state: str(r.state),
    phase: str(r.phase),
    turnSeq: 0,
    partial: "",
    detail: "",
    toolActivity: Array.isArray(r.tool_activity) ? r.tool_activity.map(str) : [],
    leaseRemainingSec: leaseRemainingFromIso(str(r.lease_expires_at)),
    sourceNames: Array.isArray(r.source_names) ? r.source_names.map(str) : [],
    defaultSource: str(r.default_source),
    webPolicy: str(r.web_policy),
    reason: str(r.reason),
  };
}

export function leaseRemainingFromIso(iso: string, now: number = Date.now()): number | null {
  if (!iso) return null;
  const stamp = Date.parse(iso);
  if (!Number.isFinite(stamp)) return null;
  return Math.max(0, Math.floor((stamp - now) / 1000));
}

export function formatLeaseRemaining(sec: number | null): string {
  if (sec === null) return "";
  if (sec >= 3600) return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`;
  return `${Math.floor(sec / 60)}m`;
}

export type ChipState = "ok" | "warn" | "error" | "info" | "dim";
export type VenueChip = { label: string; state: ChipState };

const CHIP_STATE_BY_VENUE_STATE: Record<string, ChipState> = {
  provisioning: "info",
  failed: "error",
  stopped: "dim",
};

function venueChipState(rt: VenueRuntime): ChipState {
  if (rt.state !== "running") return CHIP_STATE_BY_VENUE_STATE[rt.state] ?? "dim";
  if (rt.leaseRemainingSec !== null && rt.leaseRemainingSec < 300) return "warn";
  return rt.phase === "error" ? "error" : "ok";
}

function venueChipLabel(rt: VenueRuntime): string {
  const lease = formatLeaseRemaining(rt.leaseRemainingSec);
  const phase = rt.phase && rt.state === "running" ? "/" + rt.phase : "";
  const leaseTail = lease ? " · lease " + lease : "";
  return "remote:" + rt.state + phase + leaseTail;
}

/** Status-bar chip: `remote:running · lease 47m` — hidden when no runtime. */
export function formatVenueChip(rt: VenueRuntime | null): VenueChip | null {
  if (!rt || !rt.state) return null;
  return { label: venueChipLabel(rt), state: venueChipState(rt) };
}

/** One-line `/venue` status notice (pure; no nested template literals for the polyglot gate). */
export function formatVenueStatusLine(rt: VenueRuntime): string {
  const parts = ["remote runtime: " + rt.state + (rt.phase ? "/" + rt.phase : "")];
  parts.push("sources " + (rt.sourceNames.join(", ") || "-"));
  parts.push("web " + (rt.webPolicy || "-"));
  const lease = formatLeaseRemaining(rt.leaseRemainingSec);
  if (lease) parts.push("lease " + lease);
  if (rt.reason) parts.push(rt.reason);
  return parts.join(" · ");
}

/** Bridge errors arrive as `venue.<code>: <server message>` (bridge_server). */
export interface VenueError {
  code: string;
  message: string;
  /** Names after "sources not pushed:" on a source_not_pushed refusal. */
  missing: string[];
}

export function parseVenueError(text: string): VenueError | null {
  const match = /^venue\.([a-z_]+):\s*(.*)$/s.exec(text.trim());
  if (!match) return null;
  const code = match[1] ?? "";
  const message = match[2] ?? "";
  let missing: string[] = [];
  if (code === "source_not_pushed") {
    const tail = /sources not pushed:\s*([^(]+)/.exec(message);
    missing = (tail?.[1] ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return { code, message, missing };
}

/** The question the worker's heartbeat names (`question <id>`), or null. */
export function questionIdFromDetail(detail: string | undefined | null): string | null {
  const match = /^question (\S+)$/.exec(String(detail ?? "").trim());
  return match ? match[1]! : null;
}

/** Remote source rows (bridge `source.list_remote`). */
export interface RemoteSourceRow {
  name: string;
  kind: string;
  url: string;
  provider: string | null;
  hasApiKey: boolean;
  /** Server badge text: pushed[ (47m)] | stale | tombstoned | - | ? */
  remote: string;
  pushed: boolean;
  eligible: boolean;
}

export function mapRemoteSourceRow(raw: Record<string, unknown>): RemoteSourceRow {
  const remote = str(raw.remote);
  return {
    name: str(raw.name),
    kind: str(raw.kind),
    url: str(raw.url),
    provider: raw.provider == null ? null : str(raw.provider),
    hasApiKey: Boolean(raw.has_api_key),
    remote,
    pushed: typeof raw.pushed === "boolean" ? raw.pushed : remote.startsWith("pushed"),
    eligible:
      typeof raw.eligible === "boolean"
        ? raw.eligible
        : str(raw.kind) === "public" && Boolean(raw.has_api_key),
  };
}

export type RemoteBadge = { glyph: string; state: ChipState; needsPush: boolean };

/** Badge glyph + chip state for a remote row. */
export function remoteBadge(row: RemoteSourceRow): RemoteBadge {
  if (row.remote.startsWith("pushed")) return { glyph: row.remote, state: "ok", needsPush: false };
  if (row.remote === "stale") return { glyph: "stale (re-push)", state: "warn", needsPush: true };
  if (row.remote === "tombstoned") return { glyph: "tombstoned (re-push)", state: "warn", needsPush: true };
  if (row.remote === "?") return { glyph: "server unreachable", state: "error", needsPush: false };
  return { glyph: "not pushed", state: "dim", needsPush: true };
}

/** Rows a remote session can use: public sources carrying a key. */
export function eligibleRemoteSources(rows: RemoteSourceRow[]): RemoteSourceRow[] {
  return rows.filter((r) => r.eligible);
}

// ── remote-turn watcher rules (companion-chat remote branch) ────────────────

/** Delta between two partial-text snapshots; `reset` when the new text is not
 *  a continuation (new round) — the caller emits a round break then the full text. */
export function partialDelta(prev: string, next: string): { delta: string; reset: boolean } {
  if (!next) return { delta: "", reset: false };
  if (next.startsWith(prev)) return { delta: next.slice(prev.length), reset: false };
  return { delta: next, reset: true };
}

export type RemotePhase = "running" | "awaiting_approval" | "awaiting_user" | "idle" | "error" | "exited" | "";

/** Shape of one `session.venue_sync` answer the watcher consumes. */
export interface RemoteSyncAnswer {
  adopted?: boolean;
  runtime?: {
    phase?: string;
    partial?: string;
    detail?: string;
    turn_seq?: number;
    /** Live tool-activity lines (worker ≥ 2026-09-27); absent on older deployments. */
    tool_activity?: string[];
  };
  venue_state?: string;
  lease_remaining_sec?: number | null;
  state?: { total_messages?: number; messages?: Array<{ role?: string; content?: unknown }> };
}

export interface RemoteSyncStep {
  phase: string;
  total: number;
  /** Text to append (continuation of the shown partial), "" when nothing new. */
  delta: string;
  /** True when the new partial is not a continuation (new round → round break first). */
  reset: boolean;
  /** Partial text shown after this step. */
  shown: string;
  done: boolean;
  /** Newest assistant row text once done (empty when none). */
  finalText: string;
  errorDetail: string;
}

function syncTotal(sync: RemoteSyncAnswer): number {
  const total = sync.state?.total_messages;
  if (typeof total === "number") return total;
  return sync.state?.messages?.length ?? 0;
}

export function newestAssistantText(messages: Array<{ role?: string; content?: unknown }> | undefined): string {
  const rows = Array.isArray(messages) ? messages : [];
  const last = [...rows].reverse().find((m) => m.role === "assistant");
  if (!last) return "";
  return typeof last.content === "string" ? last.content : JSON.stringify(last.content ?? "");
}

/** Chat-turn status line for a worker phase. */
export function remotePhaseStatus(phase: string): string {
  if (phase === "running") return "remote worker running…";
  if (phase === "awaiting_approval") return "remote worker awaiting your approval";
  // ADR-099 D-8: the worker asked a durable question (ask_user).
  if (phase === "awaiting_user") return "remote worker is asking you a question";
  return phase ? `remote: ${phase}` : "queued to the remote session…";
}

/**
 * Pure step of the remote-turn watcher: given the previous shown partial and
 * the journal size at send time, what should the client emit and is the
 * turn over? Keeps the async generator branch-light.
 */
export function evaluateRemoteSync(
  sync: RemoteSyncAnswer,
  baseTotal: number,
  shown: string,
): RemoteSyncStep {
  const runtime = sync.runtime ?? {};
  const phase = String(runtime.phase ?? "");
  const total = syncTotal(sync);
  let delta = "";
  let reset = false;
  let nextShown = shown;
  const partial = String(runtime.partial ?? "");
  if (phase === "running" && partial) {
    const step = partialDelta(shown, partial);
    delta = step.delta;
    reset = step.reset;
    nextShown = partial;
  }
  const done = remoteTurnDone({ baseTotal, total, phase, adopted: Boolean(sync.adopted) });
  const step: RemoteSyncStep = {
    phase,
    total,
    delta,
    reset,
    shown: nextShown,
    done,
    finalText: done ? newestAssistantText(sync.state?.messages) : "",
    errorDetail: String(runtime.detail ?? ""),
  };
  return done ? step : endedRuntimeStep(step, sync.venue_state);
}

/** Runtime row states after which no worker heartbeat can follow. */
const ENDED_VENUE_STATES: ReadonlySet<string> = new Set(["stopped", "failed"]);

/**
 * The runtime was stopped, expired or failed mid-turn (TUI audit r2 #59):
 * teardown kills the worker, so its last heartbeat (phase "running") stays
 * on the row forever. The row state is the truth — end the turn as an
 * error instead of spinning on the stale phase. "paused" stays live (the
 * worker keeps heartbeating while paused).
 */
function endedRuntimeStep(step: RemoteSyncStep, venueState: unknown): RemoteSyncStep {
  const state = String(venueState ?? "");
  if (!ENDED_VENUE_STATES.has(state)) return step;
  const why = step.errorDetail ? `: ${step.errorDetail}` : "";
  return { ...step, phase: "error", done: true, errorDetail: `remote runtime ${state} before the turn finished${why}` };
}

/**
 * A remote turn is complete once the journal grew by the user+assistant pair
 * and the worker is no longer in a running phase. `error` completes it too
 * (the failure is surfaced, never spun on).
 */
export function remoteTurnDone(args: {
  baseTotal: number;
  total: number;
  phase: string;
  adopted: boolean;
}): boolean {
  const busy = args.phase === "running" || args.phase === "awaiting_approval" || args.phase === "awaiting_user";
  if (busy) return false;
  if (args.phase === "error" || args.phase === "exited") return true;
  return args.adopted && args.total >= args.baseTotal + 2;
}
