/**
 * Teams store (T5 wave) — multi-agent teamup sessions (fun concept, A-801)
 * surfaced through the Companion bridge. Contract:
 *
 * - rows arrive only when the server actually reports them (no fabricated
 *   demo rows; BridgeErrors render honored-format only);
 * - every verb-fallback path lands as a chat notice with the named reason;
 * - the store's `tick` is idempotent (poller-safe).
 */

import { createStore } from "solid-js/store";
import { getCompanionBridge } from "../bridge";
import { actions } from "./index";
import {
  appendTeamEvent,
  finalMessageFor,
  liveAgentsFromRing,
  teamEventNoticeLine,
  type TeamEventLine,
} from "../lib/team-live";
import * as teamupText from "../lib/teamup-text";
import {
  PLAN_RESULT_KINDS,
  agentResultLines,
  appendPlanEvent,
  applyPlanEvent,
  isPlanEvent,
  type PlanEventLine,
  type TeamRunRow,
} from "../lib/plan-live";

export interface TeamSessionRow {
  session_id: string;
  name: string;
  status: string;
  iteration: number;
  updated_at?: string;
  created_at?: string;
  /** Owning project (2026-10-01 server field); null/undefined on legacy
   * snapshots — honest absence, the pane keeps such rows visible. */
  project_id?: string | null;
  /** ADR-092 slice 3: the Work pane's agents column (absent on older
   * bridges). Slice 4 retired the team's compute budget: no cost column. */
  roles?: string[];
  max_iterations?: number | null;
}

/** One agent row on teamup.status — runtime state plus the visibility
 * contract added 2026-09-27: the verbatim prompt (with source) and model.
 * Older bridges may omit these; consumers stay undefined-tolerant. */
export interface TeamAgentRow {
  role: string;
  status: string;
  current_task?: string | null;
  last_action_at?: string | null;
  errors?: string[];
  messages_sent?: number;
  messages_received?: number;
  prompt?: string;
  prompt_source?: "default" | "override";
  model?: string | null;
}

export interface TeamSessionStatus {
  session_id: string;
  name: string;
  status: string;
  iteration: number;
  max_iterations: number;
  agents: Record<string, TeamAgentRow>;
  current_sota?: unknown;
  started_at?: string;
  updated_at?: string;
  /** Crew name from the session config (honest absence for legacy rows). */
  crew_pack?: string | null;
}

interface TeamsState {
  sessions: TeamSessionRow[];
  status: Record<string, TeamSessionStatus>;
  /** Live push rings per session (bridge team_event lines, newest last). */
  live: Record<string, TeamEventLine[]>;
  loading: boolean;
  error: string | null;
  /** ADR-092 slice 3: TeamService plan runs (team.runs + run-keyed
   * team_event lines), keyed by run id. */
  planRuns: Record<string, TeamRunRow>;
  /** Run-keyed live rings (newest last). */
  planLive: Record<string, PlanEventLine[]>;
}

const [teamsStore, setTeamsState] = createStore<TeamsState>({
  sessions: [],
  status: {},
  live: {},
  loading: false,
  error: null,
  planRuns: {},
  planLive: {},
});

export { teamsStore, setTeamsState };

/** One interval serving every running session (created lazily, torn down
 * the tick nothing is running). */
let _liveTimer: ReturnType<typeof setInterval> | null = null;
const LIVE_POLL_MS = 4_000;
const TEAM_NOTICE_DETAIL_CAP = 8;

function _hasLiveSession(): boolean {
  return (
    teamsStore.sessions.some((row) => row.status === "running") ||
    Object.values(teamsStore.status).some((s) => s && s.status === "running")
  );
}

function _ensureLivePoller(): void {
  if (_liveTimer !== null || !_hasLiveSession()) return;
  _liveTimer = setInterval(_liveTick, LIVE_POLL_MS);
}

/** One live poll: status of each running session, then the list. */
function _liveTick(): void {
  if (!_hasLiveSession()) {
    if (_liveTimer !== null) clearInterval(_liveTimer);
    _liveTimer = null;
    return;
  }
  // Status and list are reads, which the bridge answers mid-turn too (plan
  // H-8, 2026-10-18; the poller used to step aside during a chat turn).
  // Poll failures stay silent — notices only on interactive verbs.
  for (const row of teamsStore.sessions) {
    if (row.status === "running") void teamsActions._pollStatusQuiet(row.session_id);
  }
  void teamsActions._pollListQuiet();
}

/** Test hook: run one live poll now. */
export function _teamsLiveTickForTests(): void {
  _liveTick();
}


async function _request<T>(
  method: string,
  params: Record<string, unknown>,
  timeoutMs = 30_000,
): Promise<T> {
  const bridge = getCompanionBridge();
  const result = (await bridge.request(method, params, timeoutMs)) as T;
  return result;
}

/** team.plan_run / team.plan_cancel reply after the human answers the card
 * (never-auto, MD-5): a person reads at human speed, not in 30 s. */
const PLAN_CARD_TIMEOUT_MS = 15 * 60_000;
/** team.plan_cancel asks nobody; it waits for the run to settle. */
const PLAN_CANCEL_TIMEOUT_MS = 60_000;

interface PlanRunReply {
  ok?: boolean;
  run_id?: string;
  plan?: string;
  status?: string;
  error?: string;
  problems?: string[];
}

function _serverErrMessage(payload: unknown): string | null {
  if (payload && typeof payload === "object") {
    const m = payload as Record<string, unknown>;
    if (typeof m.error === "string") return m.error;
    if (typeof m.detail === "string") return m.detail;
  }
  return null;
}

export const teamsActions = {
  async list(): Promise<void> {
    setTeamsState("loading", true);
    try {
      const result = await _request<{ sessions: TeamSessionRow[]; count: number }>(
        "teamup.list",
        {},
      );
      const rows = Array.isArray(result.sessions) ? result.sessions : [];
      setTeamsState({
        sessions: rows,
        loading: false,
        error: null,
      });
      _ensureLivePoller();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setTeamsState((current) => ({
        ...current,
        error: `teamup.list failed: ${message}`,
        loading: false,
      }));
    }
  },

  async status(sessionId: string): Promise<TeamSessionStatus | null> {
    try {
      const result = (await _request<any>("teamup.status", {
        session_id: sessionId,
      })) as TeamSessionStatus;
      setTeamsState("status", { ...teamsStore.status, [sessionId]: result });
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.status failed: ${message}`);
      return null;
    }
  },

  /** Pollers' silent variants: no notices, same truth lands. */
  async _pollStatusQuiet(sessionId: string): Promise<void> {
    try {
      const result = (await _request<any>("teamup.status", {
        session_id: sessionId,
      })) as TeamSessionStatus;
      setTeamsState("status", { ...teamsStore.status, [sessionId]: result });
    } catch {
      // mailbox busy / broker busy — next tick retries; notices are for verbs
    }
  },

  async _pollListQuiet(): Promise<void> {
    try {
      const result = await _request<{ sessions: TeamSessionRow[]; count: number }>(
        "teamup.list",
        {},
      );
      const rows = Array.isArray(result.sessions) ? result.sessions : [];
      setTeamsState({ sessions: rows, loading: false, error: null });
    } catch {
      // next tick retries; news belongs to interactive verbs only
    }
  },

  async run(sessionId: string): Promise<void> {
    try {
      // Async since 2026-09-27: iteration STARTs on the server; the reply
      // lands immediately and events stream live (no more 30 s timeout).
      const result = await _request<{
        ok?: boolean;
        session_id: string;
        started?: boolean;
        expected_iteration?: number;
        status: string;
      }>("teamup.run", { session_id: sessionId });
      await teamsActions.list();
      _ensureLivePoller();
      actions.sendSystemNotice(teamupText.teamRunText(sessionId, result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.run failed: ${message}`);
    }
  },

  async pause(sessionId: string): Promise<void> {
    await teamsActions._lifecycle(sessionId, "pause");
  },

  async resume(sessionId: string): Promise<void> {
    await teamsActions._lifecycle(sessionId, "resume");
  },

  async cancel(sessionId: string): Promise<void> {
    await teamsActions._lifecycle(sessionId, "cancel");
  },

  async create(params: {
    name: string;
    goal?: string;
    roles?: string;
    hardware?: string;
    maxIterations?: number;
    autoApprove?: boolean;
    start?: boolean;
  }): Promise<string | null> {
    try {
      const payload: Record<string, unknown> = {
        name: params.name,
        goal: params.goal ?? "",
        hardware: params.hardware ?? "local",
        max_iterations: params.maxIterations ?? 10,
        auto_approve: params.autoApprove ?? false,
        start: params.start ?? false,
      };
      if (params.roles) payload.roles = params.roles;
      const result = await _request<{
        session_id: string;
        name: string;
        status: string;
        roles: string[];
        started: boolean;
      }>("teamup.create", payload);
      await teamsActions.list();
      actions.sendSystemNotice(teamupText.teamCreateText(result));
      return result.session_id;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.create failed: ${message}`);
      return null;
    }
  },

  async logs(sessionId: string, limit = 12): Promise<string[]> {
    try {
      const result = await _request<{ entries: string[]; count: number }>(
        "teamup.logs",
        { session_id: sessionId, limit },
      );
      const entries = Array.isArray(result.entries) ? result.entries : [];
      actions.sendSystemNotice(teamupText.teamLogsText(sessionId, result));
      return entries;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.logs failed: ${message}`);
      return [];
    }
  },

  /** One role agent → its own chat tab (2026-10-01): the bridge spawns a
   * persona'd companion session on the team's model with the role's verbatim
   * prompt; the user then chats with THAT agent directly. */
  async openAgentTab(teamId: string, role: string): Promise<void> {
    try {
      const res = await _request<{
        session_id?: string;
        title?: string;
        role?: string;
      }>("teamup.spawn_agent_session", { session_id: teamId, role });
      if (!res.session_id) {
        actions.sendSystemNotice(`agent tab failed: no session id returned`);
        return;
      }
      const tabId = actions.tabCreate({ sessionId: res.session_id, title: res.title ?? role });
      if (tabId) actions.sendSystemNotice(`agent ${role} opened in tab ${tabId}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`agent tab failed: ${message}`);
    }
  },

  /** Root-agent prompt tailoring (B3, 2026-09-27): one meta-call from the
   * session's root (PM/first role) writes every role's prompt from THIS
   * project; a refresh makes the override source visible in the overlay. */
  async tailor(sessionId: string): Promise<void> {
    try {
      const result = await _request<{
        session_id: string;
        root?: string | null;
        tailored?: string[];
        kept_default?: string[];
      }>("teamup.tailor", { session_id: sessionId });
      await teamsActions.status(sessionId);
      actions.sendSystemNotice(teamupText.teamTailorText(sessionId, result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.tailor failed: ${message}`);
    }
  },

  /** Armed-fire team start (B-family, 2026-09-27): bootstrap (crew + tailor)
   * + async first iteration through the bridge. Used by the armed one-shot. */
  async startCrew(
    goal: string,
    opts: { crew?: "research" | "hpc" | "code"; maxIterations?: number; parallelRoles?: number },
  ): Promise<{ crew?: string; accepted: boolean } | null> {
    try {
      // Async start (2026-09-27): reply lands immediately; the team opens
      // when the server posts `team_started` (ingestEvent).
      const result = await _request<{
        ok?: boolean;
        accepted?: boolean;
        crew?: string;
      }>("teamup.start", {
        goal,
        crew: opts.crew ?? "research",
        max_iterations: opts.maxIterations ?? 6,
        // 0/undefined would fall to the bridge default (1) anyway; only
        // honest >1 values wire the parallel fan-out knob.
        ...(opts.parallelRoles && opts.parallelRoles > 1
          ? { parallel_roles: opts.parallelRoles }
          : {}),
        run: true,
      });
      _ensureLivePoller();
      return { crew: result.crew, accepted: Boolean(result.accepted) };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`team start failed: ${message}`);
      return null;
    }
  },

  async engines(): Promise<void> {
    try {
      const result = await _request<{
        engines: Array<{
          name: string;
          version: string;
          trust_tier: string;
          available: boolean;
          note?: string;
        }>;
        count: number;
      }>("teamup.engines", {});
      actions.sendSystemNotice(teamupText.teamEnginesText(result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.engines failed: ${message}`);
    }
  },

  async crews(): Promise<void> {
    try {
      // ADR-092 slice 4: hpc is the teamup preset; every other crew is the
      // plan template it names (`/teamup on <crew>` arms that plan).
      const result = await _request<{
        crews: Array<{
          name: string;
          template?: string | null;
          aliases?: string[];
          description?: string;
        }>;
        count: number;
      }>("teamup.crews", {});
      actions.sendSystemNotice(teamupText.teamCrewsText(result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.crews failed: ${message}`);
    }
  },

  /** `/teamup hardware` — profiles a team session can target (teamup.hardware). */
  async hardware(): Promise<void> {
    try {
      const result = await _request<{
        profiles: Array<{ id: string; name?: string; description?: string }>;
        count: number;
      }>("teamup.hardware", {});
      actions.sendSystemNotice(teamupText.teamHardwareText(result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.hardware failed: ${message}`);
    }
  },

  async report(sessionId: string): Promise<void> {
    try {
      const result = await _request<{ report: string }>("teamup.report", {
        session_id: sessionId,
      });
      actions.sendSystemNotice(teamupText.teamReportText(sessionId, result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.report failed: ${message}`);
    }
  },

  async _lifecycle(sessionId: string, verb: "pause" | "resume" | "cancel"): Promise<void> {
    try {
      const result = await _request<{ session_id: string; status: string }>(
        `teamup.${verb}`,
        { session_id: sessionId },
      );
      await teamsActions.list();
      void teamsActions.status(sessionId);
      if (verb === "cancel") _finalizeTeamNotice(sessionId, "cancelled");
      actions.sendSystemNotice(teamupText.teamLifecycleText(verb, result));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`teamup.${verb} failed: ${message}`);
    }
  },

  /**
   * Live push landing (bridge team_event lines). Appends to the session's
   * ring, refreshes the status row at iteration boundaries, and drives the
   * one-row-per-session chat notice the user follows during a run.
   * Run-keyed plan lines (ADR-092 slice 3) go to ingestPlanEvent.
   */
  ingestEvent(event: TeamEventLine | PlanEventLine): void {
    if (isPlanEvent(event)) {
      teamsActions.ingestPlanEvent(event);
      return;
    }
    _ingestTeamEvent(event as TeamEventLine);
  },

  // ── team plans (ADR-092 slice 3) ─────────────────────────────────────────

  /** TeamService plan runs for the Work pane (team.runs); quiet when the
   * bridge predates it (older bridges simply list no plan rows). */
  async listPlanRuns(): Promise<void> {
    try {
      const result = await _request<{ runs?: TeamRunRow[] }>("team.runs", {});
      const rows = Array.isArray(result?.runs) ? result.runs : [];
      const next: Record<string, TeamRunRow> = {};
      for (const row of rows) {
        if (row && row.kind !== "team" && typeof row.run_id === "string") next[row.run_id] = row;
      }
      setTeamsState("planRuns", next);
    } catch {
      // older bridge / busy mailbox: rows stay as they were
    }
  },

  /** Start a plan through the bridge: the card asks the human (never-auto,
   * MD-5); the reply lands once the run started, and it streams live. */
  async startPlan(
    template: string,
    goal: string,
    args?: Record<string, string>,
  ): Promise<string | null> {
    try {
      const params: Record<string, unknown> = { template, goal };
      if (args && Object.keys(args).length > 0) params.args = args;
      const reply = await _request<PlanRunReply>("team.plan_run", params, PLAN_CARD_TIMEOUT_MS);
      if (!reply?.ok || !reply.run_id) {
        actions.sendSystemNotice(teamupText.planRefusalText(template, reply as Record<string, unknown>));
        return null;
      }
      const runId = reply.run_id;
      setTeamsState("planRuns", runId, (row) =>
        ({ ...(row ?? { kind: "plan", run_id: runId }), name: row?.name ?? reply.plan ?? template, status: reply.status ?? "running", live: true }),
      );
      actions.upsertSystemNotice(`plan:${runId}`, teamupText.planStartedText(template, reply as Record<string, unknown>));
      return runId;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`team.plan_run failed: ${message}`);
      return null;
    }
  },

  /** Cancel a plan run: the human's own action (Work pane `c`), so the
   * bridge raises no approval card (maintainer, 2026-10-11); the engine's own
   * cancel, and the reply waits for the run to settle (≤30 s server-side). */
  async cancelPlan(runId: string): Promise<void> {
    try {
      const reply = await _request<PlanRunReply>(
        "team.plan_cancel",
        { run_id: runId, reason: "Work pane c" },
        PLAN_CANCEL_TIMEOUT_MS,
      );
      actions.sendSystemNotice(
        reply?.ok === false && reply.error
            ? `plan ${runId}: ${reply.error}`
            : `plan ${runId} → ${reply?.status ?? "?"}`,
      );
      void teamsActions.listPlanRuns();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`team.plan_cancel failed: ${message}`);
    }
  },

  /** A run's result (or live progress) as a keyed notice with per-agent rows. */
  async planResult(runId: string): Promise<void> {
    try {
      const view = await _request<Record<string, unknown>>("team.plan_result", { run_id: runId });
      const status = String(view?.status ?? "?");
      const ring = teamsStore.planLive[runId] ?? [];
      const lines = agentResultLines(ring);
      const output = view?.output && typeof view.output === "object" ? Object.keys(view.output as object) : [];
      const error = typeof view?.error === "string" ? ` — ${view.error.slice(0, 160)}` : "";
      const key = `plan:${runId}`;
      actions.upsertSystemNotice(
        key,
        `plan ${String(view?.plan ?? teamsStore.planRuns[runId]?.name ?? runId)} — ${status}${error}` +
          (output.length ? ` · outputs {${output.join(",")}}` : "") +
          (lines.length ? ` · ${lines.length} agent result${lines.length === 1 ? "" : "s"}` : ""),
      );
      actions.setNoticeDetails(key, lines.length ? lines : ["(no agent results streamed to this console)"]);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      actions.sendSystemNotice(`team.plan_result failed: ${message}`);
    }
  },

  /** One run-keyed team_event line: ring, row, and the per-agent receipt. */
  ingestPlanEvent(event: PlanEventLine): void {
    const runId = event.run_id;
    setTeamsState("planLive", runId, (ring) => appendPlanEvent(ring ?? [], event));
    setTeamsState("planRuns", runId, (row) => applyPlanEvent(row, event));
    if (PLAN_RESULT_KINDS.has(event.kind)) {
      handleAgentResult(event);
      return;
    }
    if (event.kind === "gate_requested") {
      actions.sendSystemNotice(teamupText.planGateText(event.plan ?? runId, event));
      return;
    }
    if (event.kind === "plan_finished") _planFinishedNotice(event);
  },
};

/**
 * The agent_result handler (ADR-092 slice 3): every finished agent of a
 * plan run lands as one row of the run's keyed notice — ✓ answer preview,
 * ✖ null with its reason, ⊘ cancelled — so the user reads each agent's
 * result as it arrives, not only the run's end.
 */
export function handleAgentResult(event: PlanEventLine): void {
  const runId = event.run_id;
  const key = `plan:${runId}`;
  const ring = teamsStore.planLive[runId] ?? [];
  const lines = agentResultLines(ring);
  const row = teamsStore.planRuns[runId];
  actions.upsertSystemNotice(key, teamupText.planResultRowText(event.plan ?? row?.name ?? runId, row, lines));
  actions.setNoticeDetails(key, lines);
}

function _planFinishedNotice(event: PlanEventLine): void {
  const runId = event.run_id;
  const key = `plan:${runId}`;
  const lines = agentResultLines(teamsStore.planLive[runId] ?? []);
  const name = event.plan ?? teamsStore.planRuns[runId]?.name ?? runId;
  actions.upsertSystemNotice(key, teamupText.planFinishedText(name, event, lines));
  actions.setNoticeDetails(key, lines);
}

function _ingestTeamEvent(event: TeamEventLine): void {
  // Error lane: no session yet exists — also answered first-class.
  if (event?.kind === "team_start_error") {
    actions.sendSystemNotice(teamupText.teamStartErrorText(event as unknown as Record<string, unknown>));
    return;
  }
  const sid = event?.session_id;
  if (!sid || !event.kind) return;
  setTeamsState("live", sid, (ring) => appendTeamEvent(ring ?? [], event));
  _updateTeamNotice(sid);
  if (event.kind === "iteration_complete" || event.kind === "iteration_started") {
    void teamsActions._pollStatusQuiet(sid);
  }
  if (event.kind === "run_finished") {
    void teamsActions._pollStatusQuiet(sid);
    _runFinishedNotice(event);
  }
  if (event.kind === "team_started") {
    void teamsActions._pollListQuiet();
    const degraded = event.tailor?.degraded;
    actions.upsertSystemNotice(`team:${event.session_id}`, teamupText.teamStartedText(event as unknown as Record<string, unknown>));
    if (degraded) {
      // The team runs on crew-spec baseline prompts; the tailor meta
      // call failed (endpoint/class error) and the reason is named.
      actions.sendSystemNotice(teamupText.tailorDegradedText(degraded));
    }
    void teamsActions._pollStatusQuiet(event.session_id ?? "");
  }
  _ensureLivePoller();
}

function _teamNoticeText(sid: string): string {
  const ring = teamsStore.live[sid] ?? [];
  const status = teamsStore.status[sid];
  const last = ring[ring.length - 1];
  const name = status?.name ?? last?.session_name ?? sid.slice(0, 12);
  const iter = last?.iteration ?? status?.iteration ?? 0;
  return teamupText.teamProgressText(name, iter, last?.max_iterations ?? status?.max_iterations, last);
}

function _updateTeamNotice(sid: string): void {
  const key = `team:${sid}`;
  const ring = teamsStore.live[sid] ?? [];
  actions.upsertSystemNotice(key, _teamNoticeText(sid));
  actions.setNoticeDetails(key, ring.slice(-TEAM_NOTICE_DETAIL_CAP).map(teamEventNoticeLine));
}

function _finalizeTeamNotice(sid: string, statusText: string): void {
  const key = `team:${sid}`;
  const name = teamsStore.status[sid]?.name ?? sid.slice(0, 12);
  actions.upsertSystemNotice(key, `team ${name} — ${statusText}`);
}

/** Per-role conclusions: each agent's LAST full answer from the ring
 * (server-bounded carriage). Only real messages appear; a run with none
 * says so honestly. The chat receipt bounds each excerpt, the overlay
 * holds the full text (Work pane → team row → agent row). */
export function conclusionLinesFor(sid: string): string[] {
  const ring = teamsStore.live[sid] ?? [];
  const lines: string[] = [];
  for (const agent of liveAgentsFromRing(ring)) {
    const finalText = finalMessageFor(ring, agent.role);
    if (finalText === undefined) continue;
    const flat = finalText.replace(/\s+/g, " ").trim();
    if (!flat) continue;
    const excerpt = flat.slice(0, 240);
    const more = flat.length > 240 ? ` … (+${flat.length - 240} chars)` : "";
    lines.push(`  ${agent.role}: ${excerpt}${more}`);
  }
  return lines;
}

function _postConclusionReceipt(sid: string, name: string, iter: number | undefined): void {
  const lines = conclusionLinesFor(sid);
  const body = lines.length > 0 ? lines.join("\n") : "  (no agent messages recorded this run)";
  actions.sendSystemNotice(
    `⏺ team ${name} — conclusion (iteration ${iter ?? "?"}):\n${body}\nfull text: Work pane → team row (Enter) → agent row (Enter)`,
  );
}

/** run_finished (async iterations): the definitive outcome line, verbatim. */
function _runFinishedNotice(event: TeamEventLine): void {
  const sid = event.session_id;
  const key = `team:${sid}`;
  const name = event.session_name ?? teamsStore.status[sid]?.name ?? sid.slice(0, 12);
  const iter = event.iteration;
  actions.upsertSystemNotice(key, teamupText.runFinishedText(event, name));
  if (event.error_text) return;
  // The conclusion of the work lands as its own receipt (2026-09-29):
  // a finished iteration with no visible outcome read as "nothing happened".
  _postConclusionReceipt(sid, name, iter);
}

