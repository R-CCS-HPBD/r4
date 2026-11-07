/**
 * Work pane — the unified agentic-work control surface (plan F3+F4).
 *
 * One pane, three row kinds + inbox:
 *   approvals  — pending gates (tool approvals + runtime approvalGates)
 *   agents     — local agent runs (runtime store)
 *   jobs       — remote UMRS/hpc jobs (poll + project_job_event), verbs r/c
 *   workflows  — UMRS executions (Enter status) + trigger catalog (Enter =
 *                definition card with run instructions; t runs, or puts
 *                the trigger line in the composer when values are needed)
 *
 * Grammar: `glyph name · venue · state · age · verbs`. Deep views stay where
 * they are (job graph overlay, graph viewers) — rows jump to them.
 */

import { For, Show, createMemo, createSignal, onMount } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import { paneLayout } from "../lib/pane-layout";
import { workspace, actions, activeTab } from "../stores";
import { agentRuntime, agentActions } from "../stores/agent-runtime";
import { umrsWorkflowStore, umrsWorkflowActions, executionRowDetail } from "../stores/umrs-workflow";
import { cardTriggerCommand } from "../lib/workflow-card-rows";
import { openExecutionGraph } from "../lib/workflow-graph-open";
import { activeWorkProjectId, inWorkScope } from "../lib/work-scope";
import { draftWorkflowTrigger } from "../components/workflow-def-overlay";
import { teamsStore, teamsActions } from "../stores/teams";
import { loopsStore, loopsActions, loopIsLive } from "../stores/loops";
import { summarizeRing } from "../lib/team-live";
import { planEventLine, planRowLabel, planRunIsTerminal, teamAgentChips } from "../lib/plan-live";
import { isOfflineBackend } from "../lib/console-mode";
import { getCompanionBridge } from "../bridge";
import { palette, statusIcon } from "../themes";
import { truncate, truncateSmart } from "../lib/text";
import { KeyTrap } from "../components/key-trap";
import type { UiEndpoint, UiJob } from "../types";
import { PUBLIC_ENDPOINT_GLYPH, endpointLabel, endpointStateText } from "../lib/endpoint-label";

const ENDPOINT_GLYPHS: Partial<Record<UiEndpoint["status"], string>> = { ready: "●", degraded: "◐", failed: "✖" };

/** A configured public source gets the neutral ◇, never ○ "stopped" (wave 82). */
function endpointGlyph(ep: UiEndpoint): string {
  if (ep.isPublic) return PUBLIC_ENDPOINT_GLYPH;
  return ENDPOINT_GLYPHS[ep.status] ?? "○";
}
import type { KeyEvent } from "@opentui/core";

type WorkRow = {
  section: "approvals" | "agents" | "jobs" | "work" | "teams" | "endpoints" | "assets" | "loops";
  key: string;
  glyph: string;
  label: string;
  detail?: string | null;
};

/**
 * T5: teamup sessions own their glyphs (same family as statusIcon/jobs):
 * simple, uppercase-free, no invented lifecycle names.
 */
function teamStatusGlyph(status: string): string {
  if (status === "running") return "◉";
  if (status === "paused") return "◐";
  if (status === "completed" || status === "success") return "✓";
  if (status === "partial") return "◑";
  if (status === "failed") return "✖";
  if (status === "cancelled") return "✖";
  return "◐";
}

/** Plan rows key as `plan:<run_id>`; teamup rows keep the bare session id. */
const PLAN_KEY = "plan:";

function age(iso?: string): string {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m`;
  return `${Math.floor(ms / 3_600_000)}h`;
}

/**
 * Row-width budget: driven by the actual pane layout (45% of the terminal in
 * dock45 / 38% in dock38 / full-width sheet), never the old constant 86 —
 * the constant cropped titles mid-token on the live PTY (V3 finding).
 */
function useWorkRowWidth(): () => number {
  const dims = useTerminalDims();
  return () => {
    const w = dims().width;
    const layout = paneLayout(w);
    const frac = layout === "dock45" ? 0.45 : layout === "dock38" ? 0.38 : 0.97;
    return Math.max(30, Math.floor(w * frac) - 3);
  };
}

/**
 * P5 (capability-detected absorption): the job row label gains chips ONLY
 * when the backend actually carries the fields (never fabricated badges).
 */
function jobRowLabel(job: UiJob): string {
  const parts: string[] = [`${job.id} · ${job.type} · ${job.status}`];
  if (job.projectId) parts.push(` · ${job.projectId}`);
  const ago = age(job.updatedAt ?? job.createdAt);
  if (ago) parts.push(` · ${ago}`);
  return parts.join("");
}

/**
 * P5 (capability-detected absorption): capability fields live on their OWN
 * second-line detail row — label budget never trades the semantic content
 * for whitespace, and the per-row detail grammar pre-exists.
 */
function jobRowDetail(job: UiJob): string {
  const chips: string[] = [];
  if (job.terminalReason) chips.push(job.terminalReason);
  if (job.stateReason) chips.push(`#[${job.stateReason}]`);
  if (job.attemptPhase) chips.push(`@${job.attemptPhase}`);
  if (job.desiredMaxAttempts && job.attempts !== undefined)
    chips.push(`×${job.attempts}/${job.desiredMaxAttempts}`);
  if (job.restartPolicy) chips.push(`policy:${job.restartPolicy}`);
  if (chips.length === 0) return "";
  return chips.join(" · ");
}


/**
 * The verb a row handler sees: Shift+letter is the upper-case verb (R =
 * resume, T = traces). Some key parsers report name "r" + shift, so
 * Shift+R ran `r` (run) on a paused team (TUI audit r2 #2/#12).
 */
export function verbKeyName(event: { name?: string; shift?: boolean }): string {
  const name = event.name ?? "";
  return event.shift && /^[a-z]$/.test(name) ? name.toUpperCase() : name;
}

export function WorkRoute() {
  const [selected, setSelected] = createSignal(0);
  const [busy, setBusy] = createSignal(false);
  const rowWidth = useWorkRowWidth();

  /**
   * Active-project scope (2026-10-01 (11)+(12), maintainer-refined): the
   * pane shows ONLY the current project's work — STRICT: rows without a
   * matching project stamp are hidden too (legacy/foreign), and only the
   * opt-in `A` toggle ("all projects", only when requested) reveals them.
   * When no project is active there is nothing to scope by — all rows show.
   */
  const activeProjectId = createMemo(activeWorkProjectId);
  // One rule shared with the status-bar workflow badge (lib/work-scope.ts).
  const inScope = inWorkScope;

  onMount(() => {
    void umrsWorkflowActions.loadCatalog();
    void teamsActions.list();
    // ADR-092 slice 3: TeamService plan runs share the teams section.
    void teamsActions.listPlanRuns();
    void loopsActions.list();
    // F4 — asset rail: snapshot on first mount, patch lanes thereafter.
    // Capability-detected by design: missing assets.* bridge verbs resolve to
    // a silent no-op (never fabricated).
    import("../lib/assets-rail").then((mod) => void mod.pollAssetsOnce().catch(() => undefined));
  });

  const approvals = createMemo<WorkRow[]>(() => {
    const rows: WorkRow[] = [];
    const live = activeTab().toolApproval;
    if (live) {
      rows.push({
        section: "approvals",
        key: `tool:${live.id}`,
        glyph: "◉",
        label: `approve tool: ${live.tool}${live.agent ? ` · ${live.agent}` : ""}`,
        detail: live.neverAuto || live.fenceReason
          ? "inline card in chat — never auto-approved; answer there (y/n)"
          : "inline card in chat — answer there (y/n)",
      });
    }
    for (const gate of agentRuntime.approvalGates) {
      if (gate.status !== "pending") continue;
      rows.push({
        section: "approvals",
        key: `gate:${gate.id}`,
        glyph: "◉",
        label: `${gate.title}${gate.agentId ? ` · ${gate.agentId}` : ""}`,
        detail: gate.description ?? null,
      });
    }
    return rows;
  });

  const agents = createMemo<WorkRow[]>(() =>
    agentRuntime.agents
      .filter((a) => a.state !== "idle")
      .slice(0, 10)
      .map((a) => ({
        section: "agents" as const,
        key: `agent:${a.id}`,
        glyph: a.state === "running" ? "◉" : "◐",
        label: `${a.role} · ${a.state}${a.currentTask ? ` · ${a.currentTask}` : ""}`,
        detail: null,
      })),
  );

  const jobs = createMemo<WorkRow[]>(() =>
    workspace.jobs
      .filter((job) => inScope(job.projectId))
      .slice(0, 20)
      .map((job) => {
        const detail = jobRowDetail(job);
        return {
          section: "jobs" as const,
          key: job.id,
          glyph: statusIcon(job.status),
          label: jobRowLabel(job),
          detail: detail === "" ? null : detail,
        };
      }),
  );

  /**
   * Live team visibility (2026-09-27): while a session runs, the row shows
   * which agent works now (pushed team_event rings + the polled status);
   * detail carries the latest line. Absent push → plain static row, as before.
   */
  const teamSessions = createMemo<WorkRow[]>(() =>
    teamsStore.sessions
      .filter((session) => inScope(session.project_id))
      .slice(0, 10)
      .map((session) => {
        const ring = teamsStore.live[session.session_id] ?? [];
        const summary = summarizeRing(ring);
        const status = teamsStore.status[session.session_id];
        const iterText = status
          ? `${status.iteration}/${status.max_iterations}`
          : `${session.iteration}`;
        const active = summary?.activeRole ? ` · ${summary.activeRole} ⣾` : "";
        // ADR-092 slice 3: agents, when the bridge reports them (slice 4
        // retired the team's compute budget, which never moved).
        const chips = teamAgentChips(session.roles);
        const label = `${session.session_id.slice(0, 12)} · ${session.name} · ${session.status} · iter ${iterText}${chips ? ` · ${chips}` : ""}${active}${session.updated_at ? ` · ${age(session.updated_at)}` : ""}`;
        return {
          section: "teams" as const,
          key: session.session_id,
          glyph: teamStatusGlyph(session.status),
          label,
          detail: summary?.lastLine ?? null,
        };
      }),
  );

  /**
   * ADR-092 slice 3: TeamService plan runs (team.runs + run-keyed
   * team_event lines) — status, agents ok/null and tokens per run; the
   * detail line is the run's latest live fact (or its error).
   */
  const planRuns = createMemo<WorkRow[]>(() =>
    Object.values(teamsStore.planRuns)
      .filter((row) => inScope(row.project_id))
      .sort((a, b) => String(b.updated_at ?? "").localeCompare(String(a.updated_at ?? "")))
      .slice(0, 10)
      .map((row) => {
        const ring = teamsStore.planLive[row.run_id] ?? [];
        const last = ring[ring.length - 1];
        const detail = row.error ?? (last ? planEventLine(last) : null);
        const ago = row.updated_at ? ` · ${age(row.updated_at)}` : "";
        return {
          section: "teams" as const,
          key: `${PLAN_KEY}${row.run_id}`,
          glyph: teamStatusGlyph(row.status ?? "unknown"),
          label: `${planRowLabel(row)}${ago}`,
          detail,
        };
      }),
  );

  const teams = createMemo<WorkRow[]>(() => [...planRuns(), ...teamSessions()]);

  /**
   * T5-topic-5 (capability-detected absorption): work hosts endpoints too —
   * venue coordinates row (host:port + tier + backend) with a cold-start
   * chip derived from readyAt − createdAt exactly when both stamps exist.
   * Never fabricated, never reskinned.
   */
  const endpoints = createMemo<WorkRow[]>(() =>
    workspace.endpoints.slice(0, 12).map((ep) => {
      const chips: string[] = [];
      if (ep.coldStartMs !== undefined) chips.push(`cold-start ${(ep.coldStartMs / 1000).toFixed(1)}s`);
      if (ep.tier) chips.push(`[${ep.isPublic ? "public" : ep.tier}]`);
      if (ep.backend && ep.backend !== "unknown") chips.push(ep.backend);
      if (ep.host) chips.push(`${ep.host}:${ep.port}`);
      return {
        section: "endpoints" as const,
        key: `endpoint:${ep.id}`,
        glyph: endpointGlyph(ep),
        label: `${endpointLabel(ep)} · ${endpointStateText(ep)}`,
        detail: chips.join(" · "),
      };
    }),
  );

  /**
   * F4 — work hosts asset rows too (owner-scoped read-model via the
   * snapshot+patch rails; rows driven by the assets-rail hook, never fabricated).
   */
  const assets = createMemo<WorkRow[]>(() =>
    workspace.assets
      .filter((a) => inScope(a.projectId))
      .slice(0, 12)
      .map((a) => {
        const chips: string[] = [];
        if (a.title) chips.push(a.title);
        if (a.status) chips.push(`[${a.status}]`);
        if (a.projectId) chips.push(a.projectId);
        if (a.updatedAt) chips.push(age(a.updatedAt));
        return {
          section: "assets" as const,
          key: `asset:${a.shapeKey}`,
          glyph: "▣",
          label: `${a.kind}/${a.id}`,
          detail: chips.join(" · "),
        };
      }),
  );

  const workflows = createMemo<WorkRow[]>(() => {
    const execs: WorkRow[] = Object.values(umrsWorkflowStore.activeExecutions)
      // 2026-10-01 (15): executions scope by their local trigger stamp —
      // foreign/unstamped runs hide until `A` (all projects) is pressed.
      .filter((exec) => inScope(exec._scopeProjectId))
      .slice(0, 8)
      .map((exec) => ({
        section: "work" as const,
        key: `exec:${exec.execution_id}`,
        glyph: statusIcon(exec.status),
        label: `${exec.execution_id.slice(0, 12)} · ${exec.workflow_name ?? "workflow"} · ${exec.status}`,
        detail: executionRowDetail(exec),
      }));
    const catalog: WorkRow[] = umrsWorkflowStore.catalog.slice(0, 12).map((wf) => ({
      section: "work" as const,
      key: `wf:${wf.workflow_name ?? wf.name ?? "?"}`,
      glyph: "▸",
      label: `trigger: ${wf.workflow_name ?? wf.name ?? "?"}`,
      detail: wf.description ?? null,
    }));
    return [...execs, ...catalog];
  });

  /** Loops section (batch 6, 2026-09-27): caller-scoped research loops as
   * live rows — phase + iteration + goal clip; non-terminal polls ~8 s. */
  const loops = createMemo<WorkRow[]>(() =>
    loopsStore.rows
      .filter((loop) => inScope(loop.project_id))
      .slice(0, 10)
      .map((loop) => {
        const live = loopIsLive(loop.status);
        const goalClip = loop.research_goal.replace(/\s+/g, " ").slice(0, 40);
        const tracksChip = loop.max_tracks && loop.max_tracks > 1 ? ` · t${loop.max_tracks}` : "";
        return {
          section: "loops" as const,
          key: `loop:${loop.loop_id}`,
          glyph: statusIcon(live ? "running" : loop.status),
          label: `${loop.loop_id.slice(0, 17)} · ${loop.status}${loop.phase ? `/${loop.phase}` : ""} · iter ${loop.current_iteration}/${loop.max_iterations}${tracksChip}`,
          detail: goalClip || null,
        };
      }),
  );

  /** Sectioned flat list: header rows interleaved. */
  const rows = createMemo(() => {
    const out: Array<{ kind: "header"; text: string } | { kind: "row"; row: WorkRow; index: number }> = [];
    let index = 0;
    const push = (title: string, list: WorkRow[]) => {
      if (list.length === 0) return;
      out.push({ kind: "header", text: `${title} (${list.length})` });
      for (const row of list) out.push({ kind: "row", row, index: index++ });
    };
    push("approvals", approvals());
    push("agents", agents());
    push("jobs", jobs());
    push("teams", teams());
    push("endpoints", endpoints());
    push("assets", assets());
    push("workflows", workflows());
    push("loops", loops());
    return { list: out, count: index };
  });

  const flat = createMemo<WorkRow[]>(() => [
    ...approvals(),
    ...agents(),
    ...jobs(),
    ...teams(),
    ...endpoints(),
    ...assets(),
    ...workflows(),
    ...loops(),
  ]);
  const safeSelected = createMemo(() => Math.min(selected(), Math.max(0, flat().length - 1)));

  const announce = (text: string) => actions.sendSystemNotice(text);

  const primaryApprovals = (row: WorkRow) => {
    announce(
      row.key.startsWith("tool:")
        ? "tool approval is the inline chat card — y approve · n reject (scroll up if out of view)"
        : `${row.label} — runtime approval gates are resolved in the research ops view`,
    );
    return true;
  };

  const primaryJobs = (row: WorkRow) => {
    actions.openListOverlay("jobgraph", row.key);
    return true;
  };

  const primaryWork = async (row: WorkRow) => {
    if (!row.key.startsWith("exec:")) {
      // 2026-10-01: Enter on a catalog row opens the DEFINITION card
      // (meta + step graph + registered code); `t` still triggers.
      const name = row.key.slice(3);
      actions.setWorkflowDefId(name);
      void umrsWorkflowActions.fetchDefinition(name).catch(() => undefined);
      return true;
    }
    // Mission overlay (2026-09-27): steps + cost, live via the store poll,
    // and the live DAG (2026-10-20) — one opener shared with
    // `/workflow graph` and the Alt+W badge jump (2026-10-24).
    openExecutionGraph(row.key.slice(5));
    return true;
  };

  const primaryTeams = async (row: WorkRow) => {
    if (row.key.startsWith(PLAN_KEY)) {
      await teamsActions.planResult(row.key.slice(PLAN_KEY.length));
      return true;
    }
    actions.setTeamDetailId(row.key);
    await teamsActions.status(row.key);
    return true;
  };

  // T5 topic-5: Enter on an endpoints row opens the detail overlay (never a
  // fabricated verb chain — rows map to an overlay id byte-identical).
  const primaryEndpoints = (row: WorkRow) => {
    actions.setEndpointDetailId(row.key.slice(9)); // strip the `endpoint:` prefix
    return true;
  };

  // T5 topic-6: Enter on an agents row opens the agent detail overlay.
  const primaryAgents = (row: WorkRow) => {
    actions.setAgentDetailId(row.key.slice(6)); // strip the `agent:` prefix
    return true;
  };

  /** Batch 6: Enter on a loops row opens the auto-following detail card. */
  const primaryLoops = async (row: WorkRow) => {
    await loopsActions.detail(row.key.slice(5)); // strip the `loop:` prefix
    return true;
  };

  const primaryHandlers: Record<string, (row: WorkRow) => Promise<boolean> | boolean> = {
    approvals: primaryApprovals,
    jobs: primaryJobs,
    work: primaryWork,
    teams: primaryTeams,
    endpoints: primaryEndpoints,
    agents: primaryAgents,
    loops: primaryLoops,
  };

  const runPrimary = async (row: WorkRow | undefined) => {
    if (!row || busy()) return;
    setBusy(true);
    try {
      const handler = primaryHandlers[row.section as keyof typeof primaryHandlers];
      if (handler) await handler(row);
      else announce(row.label);
    } finally {
      setBusy(false);
    }
  };

  

  const handleVerbJob = async (row: WorkRow, name: string): Promise<boolean> => {
    if (busy() || (name !== "r" && name !== "c" && name !== "t")) return false;
    if (name === "t") {
      setBusy(true);
      try {
        await showJobTraces(row.key);
        return true;
      } finally {
        setBusy(false);
      }
    }
    setBusy(true);
    try {
      await (name === "r" ? actions.retryJob(row.key) : actions.cancelJob(row.key));
      return true;
    } finally {
      setBusy(false);
    }
  };

  /** `T` on a job row (2026-09-27): execution traces into a keyed notice.
   * Empty answers stay honest (a job with no traces says so). */
  const showJobTraces = async (jobId: string): Promise<void> => {
    if (isOfflineBackend()) {
      announce("traces: requires a live backend");
      return;
    }
    try {
      const bridge = getCompanionBridge();
      const payload = (await bridge.request("traces.job", { job_id: jobId }, 15_000)) as {
        traces?: Array<Record<string, unknown>>;
        count?: number;
      };
      const rows = Array.isArray(payload.traces) ? payload.traces : [];
      const key = `traces:${jobId}`;
      if (rows.length === 0) {
        announce(`traces ${jobId}: none — job carries no execution trace rows`);
        return;
      }
      const lines = rows.slice(0, 20).map((tr) => {
        const step = (tr.step_name ?? tr.step ?? tr.name ?? "?") as string;
        const status = (tr.status ?? "?") as string;
        const dur = tr.duration_ms !== undefined ? ` ${Math.round(Number(tr.duration_ms) / 1000)}s` : "";
        return `${statusIcon(status)} ${step} · ${status}${dur}`;
      });
      actions.upsertSystemNotice(key, `traces ${jobId} (${payload.count ?? rows.length})`);
      actions.setNoticeDetails(key, lines);
    } catch (err) {
      announce(`traces ${jobId} failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleVerbWork = async (row: WorkRow, name: string): Promise<boolean> => {
    if (busy()) return false;
    if (name === "T" && row.key.startsWith("exec:")) {
      setBusy(true);
      try {
        await showWorkflowTraces(row.key.slice(5));
        return true;
      } finally {
        setBusy(false);
      }
    }
    if (name !== "t" || !row.key.startsWith("wf:")) return false;
    setBusy(true);
    try {
      const workflowName = row.key.slice(3);
      // 2026-10-02: a workflow that needs values gets its trigger line in the
      // composer (the definition card's `r`) instead of a run without them;
      // one that needs none — or whose package is unavailable — runs now.
      const detail =
        umrsWorkflowStore.definitions[workflowName] ?? (await umrsWorkflowActions.fetchDefinition(workflowName));
      const trigger = detail ? cardTriggerCommand(detail) : null;
      if (trigger && trigger.required > 0) {
        draftWorkflowTrigger(trigger.command, trigger.required);
        return true;
      }
      const exec = await umrsWorkflowActions.run(workflowName);
      announce(`triggered ${workflowName} → ${exec.execution_id.slice(0, 12)} (${exec.status})`);
      return true;
    } finally {
      setBusy(false);
    }
  };

  /** `T` on a workflow-execution row (batch 4, 2026-09-27): execution traces
   * into a keyed notice — mirrors the job-row `t` verb. */
  const showWorkflowTraces = async (executionId: string): Promise<void> => {
    if (isOfflineBackend()) {
      announce("traces: requires a live backend");
      return;
    }
    try {
      const bridge = getCompanionBridge();
      const payload = (await bridge.request("traces.execution", { execution_id: executionId }, 15_000)) as {
        traces?: Array<Record<string, unknown>>;
        count?: number;
      };
      const rows = Array.isArray(payload.traces) ? payload.traces : [];
      const short = executionId.slice(0, 12);
      const key = `traces:exec:${short}`;
      if (rows.length === 0) {
        announce(`traces ${short}: none — execution carries no step trace rows`);
        return;
      }
      const lines = rows.slice(0, 20).map((tr) => {
        const step = (tr.step_name ?? tr.step ?? tr.name ?? "?") as string;
        const status = (tr.status ?? "?") as string;
        const dur = tr.duration_ms !== undefined ? ` ${Math.round(Number(tr.duration_ms) / 1000)}s` : "";
        return `${statusIcon(status)} ${step} · ${status}${dur}`;
      });
      actions.upsertSystemNotice(key, `traces ${short} (${payload.count ?? rows.length})`);
      actions.setNoticeDetails(key, lines);
    } catch (err) {
      announce(`traces ${executionId.slice(0, 12)} failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const _teamsVerbRun = async (sid: string) => { await teamsActions.run(sid); return true; };
  const _teamsVerbPause = async (sid: string) => { await teamsActions.pause(sid); return true; };
  const _teamsVerbResume = async (sid: string) => { await teamsActions.resume(sid); return true; };
  const _teamsVerbCancel = async (sid: string) => { await teamsActions.cancel(sid); return true; };
  const _teamsVerbLogs = async (sid: string) => { await teamsActions.logs(sid, 12); return true; };
  const _teamsVerbReport = async (sid: string) => { await teamsActions.report(sid); return true; };

  const _teamsVerbPrimary = async (sid: string) => {
    // T5 topic-1: Enter on a teams row opens the detail overlay (not a
    // one-line answer — a ridi-time card with rows + verbs + Esc contract).
    actions.setTeamDetailId(sid);
    const status = await teamsActions.status(sid);
    return !!status;
  };

  const teamsVerbHandlers: Record<string, (sid: string) => Promise<boolean>> = {
    r: _teamsVerbRun,
    p: _teamsVerbPause,
    R: _teamsVerbResume,
    c: _teamsVerbCancel,
    l: _teamsVerbLogs,
    o: _teamsVerbReport,
  };

  /** Plan rows: Enter → the run's result (per-agent rows); c → cancel. */
  const handlePlanVerb = async (row: WorkRow, name: string): Promise<boolean> => {
    const runId = row.key.slice(PLAN_KEY.length);
    const live = !planRunIsTerminal(teamsStore.planRuns[runId]?.status);
    const verb =
      name === "c" && live
        ? () => teamsActions.cancelPlan(runId)
        : name === "return" || name === "enter"
          ? () => teamsActions.planResult(runId)
          : undefined;
    if (!verb) return false;
    setBusy(true);
    try {
      await verb();
      return true;
    } finally {
      setBusy(false);
    }
  };

  const handleTeamsVerb = async (row: WorkRow, name: string): Promise<boolean> => {
    if (busy()) return false;
    if (row.key.startsWith(PLAN_KEY)) return handlePlanVerb(row, name);
    const sid = row.key;
    const verb = teamsVerbHandlers[name];
    if (verb) {
      setBusy(true);
      try {
        return await verb(sid);
      } finally {
        setBusy(false);
      }
    }
    const action = name === "return" || name === "enter" ? _teamsVerbPrimary : undefined;
    if (!action) return false;
    setBusy(true);
    try {
      return await action(sid);
    } finally {
      setBusy(false);
    }
  };

  /** Batch 6: `x` on a loops row → stop the loop (owner-scoped).
   * (Loop rows' Enter is primaryLoops; verbs live here like the other families.) */
  const handleVerbLoop = async (row: WorkRow, name: string): Promise<boolean> => {
    if (busy() || name !== "x") return false;
    setBusy(true);
    try {
      await loopsActions.stop(row.key.slice(5));
      return true;
    } finally {
      setBusy(false);
    }
  };

  const sectionHandlers: Record<string, (row: WorkRow, name: string) => Promise<boolean>> = {
    teams: handleTeamsVerb,
    jobs: handleVerbJob,
    work: handleVerbWork,
    loops: handleVerbLoop,
  };

  const navDown = async () => {
    setSelected(Math.min(safeSelected() + 1, Math.max(0, flat().length - 1)));
    return true;
  };
  const navUp = async () => {
    setSelected(Math.max(safeSelected() - 1, 0));
    return true;
  };
  const navEnter = async () => {
    await runPrimary(flat()[safeSelected()]);
    return true;
  };
  const keyNavHandlers: Record<string, () => Promise<boolean>> = {
    down: navDown,
    up: navUp,
    return: navEnter,
    enter: navEnter,
  };

  const handleKey = async (event: KeyEvent) => {
    const name = event.name ?? "";
    // 2026-10-01 (15): `A` — opt-in all-projects scope, toggles back.
    // (key parsers disagree: some report name "A", others name "a" + shift)
    if (name === "A" || (name === "a" && Boolean(event.shift))) {
      actions.toggleWorkScopeAll();
      announce(
        activeTab().workScopeAll
          ? "work scope: ALL projects (A toggles back to the active project)"
          : `work scope: active project (${activeProjectId() ?? "none"})`,
      );
      event.preventDefault();
      return;
    }
    const nav = keyNavHandlers[name];
    const handled = nav ? await nav() : await sectionHandlers[
      (flat()[safeSelected()]?.section ?? "") as keyof typeof sectionHandlers
    ]?.(flat()[safeSelected()] as WorkRow, verbKeyName(event));
    if (handled) event.preventDefault();
  };

  return (
    <box flexDirection="column" width="100%" height="100%" overflow="hidden" padding={1}>
      <box height={1} width="100%" overflow="hidden" marginBottom={1}>
        <text fg={palette.accent}>
          {truncateSmart(
            `work — agentic control (${activeTab().workScopeAll ? "ALL projects" : `project: ${activeProjectId() ?? "—"}`})`,
            rowWidth(),
          )}
        </text>
      </box>
      <Show
        when={flat().length > 0}
        fallback={
          <box flexDirection="column" width="100%">
            <box height={1}><text fg={palette.fgDim}>No active work.</text></box>
            <box height={1}><text fg={palette.fgDim}>· local agent runs appear here while a turn executes</text></box>
            <box height={1}><text fg={palette.fgDim}>· remote jobs appear via the backend poll (8s)</text></box>
            <box height={1}><text fg={palette.fgDim}>· trigger a workflow: /workflow run &lt;name&gt;</text></box>
            <box height={1}><text fg={palette.fgDim}>· A: show all projects (this pane scopes to the active project)</text></box>
          </box>
        }
      >
        <scrollbox flexGrow={1} scrollY overflow="hidden">
          <For each={rows().list}>
            {(entry) => {
              if (entry.kind === "header") {
                return (
                  <box height={1} width="100%" marginTop={1}>
                    <text fg={palette.accent2}>{entry.text}</text>
                  </box>
                );
              }
              const isSel = () => entry.index === safeSelected();
              return (
                <box
                  height={entry.row.detail ? 2 : 1}
                  width="100%"
                  flexDirection="column"
                  backgroundColor={isSel() ? palette.bgActive : undefined}
                  onMouseDown={() => void runPrimary(entry.row)}
                >
                  <box height={1} width="100%" overflow="hidden">
                    <text fg={isSel() ? palette.fgBright : palette.fg}>
                      {truncateSmart(
                        `${isSel() ? "▸" : " "} ${entry.row.glyph} ${entry.row.label}`,
                        rowWidth(),
                      )}
                    </text>
                  </box>
                  <Show when={entry.row.detail}>
                    <box height={1} width="100%" overflow="hidden">
                      <text fg={palette.fgDim}>{truncateSmart(`    ${entry.row.detail}`, rowWidth())}</text>
                    </box>
                  </Show>
                </box>
              );
            }}
          </For>
        </scrollbox>
      </Show>
      <box height={1} width="100%" overflow="hidden" marginTop={1}>
        <text fg={palette.fgDim}>
          {`↑↓ select · Enter detail · jobs: r retry · c cancel · teams/plans: c cancel · workflows: t trigger · A all-projects · ${busy() ? "working…" : "ready"}`}
        </text>
      </box>
      {/* The definition card holds the keys while open. KeyTrap takes focus
          when it mounts, so remounting it on close hands the keys back. */}
      <Show when={workspace.workflowDefId == null}>
        <KeyTrap paneScoped focused={true} onKeyDown={(e) => void handleKey(e)} />
      </Show>
    </box>
  );
}
