/**
 * Loops store (TUI/rccs sweep batch 6, 2026-09-27): caller-scoped research
 * loops as live rows. Contract mirrors the teams store: rows arrive only
 * when the bridge really answers (never fabricated); one interval serves
 * the running set and re-follows an open `/loops detail` card until the
 * loop is terminal.
 */

import { createStore } from "solid-js/store";
import { getCompanionBridge } from "../bridge";
import { actions } from "./index";

export interface LoopRow {
  loop_id: string;
  status: string;
  phase: string;
  current_iteration: number;
  max_iterations: number;
  research_goal: string;
  /** Parallel-tracks count when the loop really runs tracked (ADR-066 A2). */
  max_tracks?: number;
  /** Owning project (2026-10-01 umrs list field); "" on absence — the pane
   * keeps unscoped rows visible, never fabricates a match. */
  project_id?: string;
}

interface LoopsState {
  rows: LoopRow[];
  error: string | null;
}

const [loopsStore, setLoopsState] = createStore<LoopsState>({
  rows: [],
  error: null,
});

export { loopsStore, setLoopsState };

export const LOOP_TERMINAL = new Set([
  "completed",
  "failed",
  "stopped",
  "error",
  "cancelled",
  "canceled",
]);

export function loopIsLive(status: string): boolean {
  return !LOOP_TERMINAL.has(status.toLowerCase());
}

let _timer: ReturnType<typeof setInterval> | null = null;
const LOOP_POLL_MS = 8_000;
let _watchDetailId: string | null = null;

async function _request<T>(method: string, params: Record<string, unknown>): Promise<T> {
  const bridge = getCompanionBridge();
  return (await bridge.request(method, params, 15_000)) as T;
}

function _hasLiveLoop(): boolean {
  return loopsStore.rows.some((row) => loopIsLive(row.status));
}

/** One background refresh. loops.list / loops.status are reads, which the
 * bridge answers mid-turn too (plan H-8; the poller used to step aside during
 * a turn). A failed background poll keeps the last card and error state —
 * notices only on interactive calls (audit r2 #49). */
function _pollTick(): void {
  if (!_hasLiveLoop() && _watchDetailId === null) {
    if (_timer !== null) clearInterval(_timer);
    _timer = null;
    return;
  }
  void loopsActions.list({ quiet: true });
  if (_watchDetailId) void loopsActions.detail(_watchDetailId, { quiet: true });
}

/** Test hook: run one poller tick now. */
export function _loopPollTickForTests(): void {
  _pollTick();
}

function renderDetail(
  loopId: string,
  payload: Record<string, unknown> & {
    found?: boolean;
    message?: string;
    recent_iterations?: Array<Record<string, unknown>>;
    tracks?: Array<Record<string, unknown>>;
  },
): void {
  if (payload.found === false) {
    actions.upsertSystemNotice(`loop:${loopId}`, `loop ${loopId}: ${payload.message ?? "not found"}`);
    _watchDetailId = null;
    return;
  }
  const tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
  const trackNote =
    tracks.length > 0
      ? ` · tracks ${tracks.filter((t) => t.status === "active").length}/${tracks.length} active`
      : "";
  const head = [
    `loop ${loopId} — ${payload.status ?? "?"} (${payload.phase ?? ""})`,
    `iter ${payload.current_iteration ?? "?"}/${payload.max_iterations ?? "?"}` +
      ` · hyp ${payload.total_hypotheses_tested ?? 0}` +
      ` · exp ${payload.total_experiments_run ?? 0}` +
      ` · findings ${payload.total_findings ?? 0}` +
      trackNote,
  ];
  const trackRows = tracks.map((t) => {
    const hid = String(t.track_id ?? "?");
    const hyp = typeof t.hypothesis === "string"
      ? t.hypothesis.replace(/\s+/g, " ").slice(0, 52)
      : "";
    const turns = typeof t.turns === "number" ? `turns ${t.turns}` : "";
    return `  ${hid} ${String(t.status ?? "?")}${hyp ? ` · ${hyp}` : ""}${turns ? ` · ${turns}` : ""}`;
  });
  const iterations = Array.isArray(payload.recent_iterations) ? payload.recent_iterations : [];
  const iterRows = iterations.length
    ? iterations.map((it) => {
        const n = String(it.iteration_number ?? "?");
        const phase = String(it.phase ?? it.status ?? "?");
        const status = String(it.status ?? "?");
        const hyp =
          typeof it.hypothesis === "string" && it.hypothesis
            ? it.hypothesis.replace(/\s+/g, " ").slice(0, 56)
            : "";
        return `  #${n} ${phase} · ${status}${hyp ? ` · ${hyp}` : ""}`;
      })
    : trackRows.length === 0
      ? ["  (no iterations recorded yet)"]
      : [];
  const rows = [
    ...trackRows,
    ...(trackRows.length && iterRows.length ? ["  ── recent iterations ──"] : []),
    ...iterRows,
  ];
  actions.upsertSystemNotice(`loop:${loopId}`, head.join("\n"));
  actions.setNoticeDetails(`loop:${loopId}`, rows);
}

function _ensurePoller(): void {
  if (_timer !== null || (!_hasLiveLoop() && _watchDetailId === null)) return;
  _timer = setInterval(_pollTick, LOOP_POLL_MS);
}

export const loopsActions = {
  async list(opts: { quiet?: boolean } = {}): Promise<void> {
    try {
      const payload = await _request<{ loops?: LoopRow[]; count?: number }>("loops.list", {});
      const rows = Array.isArray(payload.loops) ? payload.loops : [];
      setLoopsState({ rows: rows.map((r) => ({
        loop_id: String(r.loop_id ?? "?"),
        status: String(r.status ?? "unknown"),
        phase: String(r.phase ?? ""),
        current_iteration: Number(r.current_iteration ?? 0),
        max_iterations: Number(r.max_iterations ?? 0),
        research_goal: String(r.research_goal ?? ""),
        project_id: typeof r.project_id === "string" && r.project_id !== "" ? r.project_id : undefined,
        max_tracks:
          typeof (r as unknown as Record<string, unknown>).max_tracks === "number"
            ? ((r as unknown as Record<string, unknown>).max_tracks as number)
            : undefined,
      })), error: null });
      _ensurePoller();
    } catch (err) {
      if (opts.quiet) return;
      setLoopsState("error", err instanceof Error ? err.message : String(err));
    }
  },

  /** Render the loop card + recent iterations; registers follow-refresh
   * while the loop is live (a second `detail` on the same id just refreshes). */
  async detail(loopId: string, opts: { quiet?: boolean } = {}): Promise<void> {
    try {
      const payload = await _request<Record<string, unknown>>("research_loop.status", {
        loop_id: loopId,
      });
      renderDetail(loopId, payload as Record<string, unknown> & { status?: string });
      const status = String((payload as Record<string, unknown>).status ?? "");
      if (loopIsLive(status)) {
        _watchDetailId = loopId;
        _ensurePoller();
      } else if (_watchDetailId === loopId) {
        _watchDetailId = null;
      }
    } catch (err) {
      if (opts.quiet) return;
      actions.upsertSystemNotice(
        `loop:${loopId}`,
        `loop ${loopId} detail failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  },

  async stop(loopId: string): Promise<void> {
    try {
      const payload = await _request<{ status?: string }>("loops.stop", { loop_id: loopId });
      actions.sendSystemNotice(
        `loop ${loopId} → stop (${payload.status ?? "requested"})`,
      );
      await loopsActions.list();
    } catch (err) {
      actions.sendSystemNotice(
        `loop ${loopId} stop failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  },
};
