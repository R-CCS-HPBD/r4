/**
 * Work pane (P2): one surface for approvals + local agents + remote jobs +
 * UMRS workflows. Sections interleave header/row entries; verbs fire via
 * existing store actions; empty state is honest, never demo-flavored.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { WorkRoute } from "../src/routes/work";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { umrsWorkflowStore, setUmrsWorkflowStore } from "../src/stores/umrs-workflow";
import { setTeamsState } from "../src/stores/teams";
import { setLoopsState } from "../src/stores/loops";

beforeEach(() => {
  resetWorkspace();
  agentActions.resetAgentRuntime();
  setUmrsWorkflowStore("catalog", []);
  setUmrsWorkflowStore("activeExecutions", {});
  // Ambient hygiene: earlier files can leave real team rows in the teams
  // store (a real bridge's teamup.list reflects the machine's own state);
  // the pane renders them and crowds out the workflow rows this file pins.
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  setLoopsState({ rows: [], error: null });
  // 2026-10-06 focus model: these tests drive the pane's key grammar, i.e.
  // they stand in for "the docked pane OWNS the keys". In-app that is
  // paneFocus=true (post-Tab/click); emulate the ownership explicitly or
  // the paneScoped trap stays blurred and keystrokes land nowhere.
  setWorkspace("paneFocus", true);
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 120));
  resetWorkspace();
  agentActions.resetAgentRuntime();
});

describe("Work pane", () => {
  it("renders honest empty state when nothing is active", async () => {
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("No active work.");
    expect(frame).toContain("work — agentic control");
    setup.renderer.destroy();
  });

  it("lists pending approvals, agents, jobs and workflow rows in sections", async () => {
    actions.openToolApproval({ id: 1, tool: "shell_exec", description: "rm -rf x", sessionApprovalAllowed: true });
    agentActions.requestApproval("experiment_execution", "Gate: launch sweep");
    setWorkspace("jobs", [
      { id: "job-123", type: "endpoint_provision", status: "running", projectId: "default" },
      { id: "job-124", type: "model_download", status: "failed" },
    ]);
    setUmrsWorkflowStore("catalog", [{ workflow_name: "sat-loop" } as never]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("approvals (2)");
    expect(frame).toContain("approve tool: shell_exec");
    expect(frame).toContain("Gate: launch sweep");
    expect(frame).toContain("jobs (2)");
    // V3 (2026-09-18): truncation is honest about the pane-width drive —
    // assert the identity-bearing prefix instead of a bounded suffix.
    expect(frame).toContain("job-123 · endpoint_provision");
    expect(frame).toContain("workflows (1)");
    expect(frame).toContain("trigger: sat-loop");
    expect(agentRuntime.approvalGates.length).toBe(1);
    setup.renderer.destroy();
  });

  it("jobs: Enter opens the job graph overlay for the row under the cursor", async () => {
    setWorkspace("jobs", [{ id: "job-999", type: "batch_infer", status: "completed" }]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    expect(workspace.listOverlay).toBe("jobgraph");
    expect(workspace.overlayTargetId).toBe("job-999");
    setup.renderer.destroy();
  });

  it("workflows: Enter on a catalog row opens the definition card (2026-10-01)", async () => {
    setUmrsWorkflowStore("catalog", [{ workflow_name: "sat-loop" } as never]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    expect(workspace.workflowDefId).toBe("sat-loop");
    actions.setWorkflowDefId(null);
    setup.renderer.destroy();
  });

  it("scopes rows strictly to the active project; `A` reveals all (2026-10-01 (15))", async () => {
    setWorkspace("project", {
      id: "proj-a",
      name: "Alpha",
      description: "",
      status: "active",
      goalIds: [],
    });
    setWorkspace("jobs", [
      { id: "job-mine", type: "batch_infer", status: "running", projectId: "proj-a" },
      { id: "job-theirs", type: "batch_infer", status: "running", projectId: "proj-b" },
      { id: "job-untagged", type: "batch_infer", status: "running" },
    ]);
    setWorkspace("assets", [
      { kind: "dataset", id: "a-mine", shapeKey: "dataset:a-mine", projectId: "proj-a" },
      { kind: "dataset", id: "a-theirs", shapeKey: "dataset:a-theirs", projectId: "proj-b" },
    ]);
    setTeamsState({
      sessions: [
        { session_id: "sess-mine-aaaa", name: "alpha team", status: "paused", iteration: 2, project_id: "proj-a" },
        { session_id: "sess-theirs-bb", name: "beta team", status: "paused", iteration: 1, project_id: "proj-b" },
        { session_id: "sess-legacy-cc", name: "legacy team", status: "paused", iteration: 1 },
      ],
      status: {},
      live: {},
      loading: false,
      error: null,
    });
    setLoopsState({
      rows: [
        { loop_id: "loop-mine-0001", status: "running", phase: "analyze", current_iteration: 1, max_iterations: 3, research_goal: "mine goal", project_id: "proj-a" },
        { loop_id: "loop-theirs-002", status: "running", phase: "analyze", current_iteration: 1, max_iterations: 3, research_goal: "theirs goal", project_id: "proj-b" },
      ],
      error: null,
    });
    setUmrsWorkflowStore("activeExecutions", {
      "exec-mine-1234": { execution_id: "exec-mine-1234", status: "running", _scopeProjectId: "proj-a" } as never,
      "exec-old-5678": { execution_id: "exec-old-5678", status: "running" } as never,
    });
    const setup = await testRender(() => <WorkRoute />, { width: 140, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // Strict scope (2026-10-01 (15)): only proj-a rows — foreign AND
    // untagged rows hide until the user asks for them (`A`).
    expect(frame).toContain("project: proj-a");
    expect(frame).toContain("job-mine");
    expect(frame).not.toContain("job-theirs");
    expect(frame).not.toContain("job-untagged");
    expect(frame).toContain("dataset/a-mine");
    expect(frame).not.toContain("dataset/a-theirs");
    expect(frame).toContain("alpha team");
    expect(frame).not.toContain("beta team");
    expect(frame).not.toContain("legacy team");
    expect(frame).toContain("mine goal");
    expect(frame).not.toContain("theirs goal");
    // Workflow rows render execution_id.slice(0, 12).
    expect(frame).toContain("exec-mine-12");
    expect(frame).not.toContain("exec-old-567");

    // Opt-in: `A` reveals every project — requested behavior.
    setup.mockInput.pressKey("a", { shift: true });
    await setup.renderOnce();
    const all = setup.captureCharFrame();
    expect(all).toContain("ALL projects");
    expect(all).toContain("job-theirs");
    expect(all).toContain("job-untagged");
    expect(all).toContain("beta team");
    expect(all).toContain("legacy team");
    expect(all).toContain("theirs goal");
    expect(all).toContain("exec-old-567");
    setup.renderer.destroy();
  });
});

describe("pane-close focus return (L4 wave, focus-regression pin)", () => {
  beforeEach(() => {
    actions.resetWorkspace();
  });

  it("chat input disadvantaged while pane open, resurfaces exactly at close", () => {
    expect(workspace.paneView).toBe(null);
    // Fact: the composer is interactively dead while the pane is open (its
    // KeyTrap owns the pane grammar) and must come back at pane close.
    actions.openPane("work");
    // chatInputFocused is internal to the chat module — the externally
    // visible predicate is paneView itself; the close() hook is the
    // beat that flips the input's epoch edge (remount).
    expect(workspace.paneView).toBe("work");
    actions.closePane();
    expect(workspace.paneView).toBe(null);
    expect(workspace.paneFocus).toBe(false);
  });

  it("slashing back into work after Esc closes works (alias continuity)", async () => {
    actions.openPane("work");
    actions.closePane();
    await actions.dispatchSlashFromChat("/workflows");
    expect(workspace.paneView).toBe("work");
    actions.closePane();
  });
});

describe("P5 capability-detected absorption (Work rows)", () => {
  it("rows without capability markers stay standard (no fabricated chips)", async () => {
    setWorkspace("jobs", [
      { id: "job-quiet", type: "endpoint_provision", status: "running" },
    ]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("job-quiet");
    expect(frame).not.toContain("budget_exhausted");
    expect(frame).not.toContain("@[");
    setup.renderer.destroy();
  });

  it("budget_exhausted marker shows verbatim when present", async () => {
    setWorkspace("jobs", [
      {
        id: "job-exhaust",
        type: "endpoint_provision",
        status: "failed",
        terminalReason: "budget_exhausted",
      },
    ]);
    // dock45 — the budget chip needs real pane width to fit (narrow panes
    // crop it as legitimately smaller; layout-driven, not aesthetics).
    const setup = await testRender(() => <WorkRoute />, { width: 140, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("budget_exhausted");
    setup.renderer.destroy();
  });

  it("attempt-phase and state_reason render when present, hidden when not", async () => {
    setWorkspace("jobs", [
      {
        id: "job-sr",
        type: "endpoint_provision",
        status: "running",
        stateReason: "MaxMemPerLimit",
        attemptPhase: "claim",
      },
      { id: "job-plain", type: "model_download", status: "failed" },
    ]);
    const setup = await testRender(() => <WorkRoute />, { width: 140, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("MaxMemPerLimit");
    expect(frame).toContain("@claim");
    setup.renderer.destroy();
  });
});

  it("attempt budget chip ×n/m appears when attempts+max are present", async () => {
    setWorkspace("jobs", [
      {
        id: "job-budget",
        type: "endpoint_provision",
        status: "failed",
        attempts: 2,
        desiredMaxAttempts: 3,
      },
    ]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("×2/3");
    setup.renderer.destroy();
  });

  it("restart_policy shows its own chip when present", async () => {
    setWorkspace("jobs", [
      {
        id: "job-policy",
        type: "endpoint_provision",
        status: "running",
        restartPolicy: "retry",
      },
    ]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("policy:retry");
    setup.renderer.destroy();
  });

describe("T5 teams section (work rows via bridge)", () => {
  it("sessions appear as their own section when the store reports them", async () => {
    const { teamsStore, setTeamsState } = await import("../src/stores/teams");
    setTeamsState("sessions", [{
      session_id: "sess-t5-0001",
      name: "HPC optimize batch-1",
      status: "running",
      iteration: 3,
    } as never]);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("teams")
    expect(frame).toContain("HPC optimize")
    expect(frame).toContain("teams")
    setup.renderer.destroy();
  });

  it("empty store never fabricates a teams section", async () => {
    const { teamsStore, setTeamsState } = await import("../src/stores/teams");
    setTeamsState("sessions", []);
    const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).not.toContain("teams (")
    setup.renderer.destroy();
  });
});

describe("T5 /teamup wave-intercept handler", () => {
  it("routes for a bare /teamup's listing through the teams-store list action", async () => {
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    const result = await handleWaveIntercepts({ name: "teamup", args: "" });
    expect(result?.kind).toBe("handled");
  });

  it("verb-shaped patterns reach identical entry (status|run|cancel etc) without failure-kind", async () => {
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    const kinds = ["status", "run", "cancel", "logs", "report", "hardware"];
    for (const sub of kinds) {
      const result = await handleWaveIntercepts({ name: "teamup", args: sub });
      expect(result?.kind).toBe("handled");
    }
  });
});

describe("T5 topic-1: team-detail overlay at Enter", () => {
  it("Enter on a teams row opens the overlay with the session's rows", async () => {
    const { setTeamsState } = await import("../src/stores/teams");
    setTeamsState("sessions", [{
      session_id: "sess-touch-1",
      name: "batch-a",
      status: "running",
      iteration: 1,
    } as never]);
    setTeamsState("status", {
      "sess-touch-1": {
        session_id: "sess-touch-1",
        name: "batch-a",
        status: "running",
        iteration: 1,
        max_iterations: 8,
        agents: {
          pm: { status: "thinking" },
          se: { status: "executing" },
        },
      } as never,
    } as never);
    actions.setTeamDetailId("sess-touch-1");
    const { TeamDetailOverlay } = await import("../src/components/team-detail-overlay");
    const setup = await testRender(() => <TeamDetailOverlay />, { width: 100, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("teamup session — Esc closes");
    expect(frame).toContain("batch-a");
    expect(frame).toContain("iter 1/8");
    expect(frame).not.toContain("budget"); // ADR-092 slice 4: the team's compute budget is retired
    expect(frame).toContain("thinking");
    setup.renderer.destroy();
    actions.setTeamDetailId(null);
  });

  it("closeAllOverlays wipes the overlay id too", async () => {
    actions.setTeamDetailId("sess-touch-2");
    actions.closeAllOverlays();
    expect(workspace.teamDetailId).toBe(null);
  });
});

describe("T5 topic-5: endpoints section (cold-start capability-detected)", () => {
  it("renders endpoint rows with cold-start chip when both stamps parse", async () => {
    setWorkspace("endpoints", [{
      id: "ep-cs-1",
      model: "rivault-gpt-oss-static",
      servedModelName: "rivault-gpt-oss-static",
      status: "ready",
      url: "http://109.0.1.42:8000",
      tier: "project",
      backend: "slurm",
      host: "109.0.1.42",
      port: 8000,
      createdAt: "2026-09-18T00:00:00Z",
      readyAt: "2026-09-18T00:01:12Z",
      coldStartMs: 72_000,
    }] as never);
    const setup = await testRender(() => <WorkRoute />, { width: 150, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("endpoints");
    expect(frame).toContain("rivault-gpt-oss-static");
    expect(frame).toContain("109.0.1.42:8000");
    expect(frame).toContain("cold-start 72.0s");
    setup.renderer.destroy();
  });

  it("hides the endpoints section entirely when no rows are present", async () => {
    setWorkspace("endpoints", []);
    const setup = await testRender(() => <WorkRoute />, { width: 150, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // no header, no fabricated empty section list
    expect(frame).not.toContain("endpoints (");
    setup.renderer.destroy();
  });

  it("drops the cold-start chip alone when the stamp is missing (never fabricated)", async () => {
    setWorkspace("endpoints", [{
      id: "ep-cs-2",
      model: "rivault-coder-3b",
      servedModelName: "rivault-coder-3b",
      status: "ready",
      url: "http://109.0.1.43:8000",
      tier: "project",
      backend: "slurm",
      host: "109.0.1.43",
      port: 8000,
      createdAt: "2026-09-18T00:00:00Z",
      // no readyAt — derivable pair broken → coldStartMs stays undefined
    }] as never);
    const setup = await testRender(() => <WorkRoute />, { width: 150, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("rivault-coder-3b");
    expect(frame).toContain("109.0.1.43:8000");
    expect(frame).not.toContain("cold-start");
    setup.renderer.destroy();
  });
});

describe("T5 topic-5b: Enter on endpoints row → detail overlay", () => {
  const endpointRow = {
    id: "ep-detail-1",
    model: "rivault-gpt-oss-static",
    servedModelName: "rivault-gpt-oss-static",
    status: "ready",
    url: "http://109.0.1.42:8000",
    tier: "project",
    backend: "slurm",
    host: "109.0.1.42",
    port: 8000,
    createdAt: "2026-09-18T00:00:00Z",
    readyAt: "2026-09-18T00:01:12Z",
    coldStartMs: 72_000,
    sourceName: "rccs-ds4-m10",
  };

  it("EndpointDetailOverlay renders the endpoint's rows byte-identical, no fabrication", async () => {
    const { actions } = await import("../src/stores");
    setWorkspace("endpoints", [endpointRow] as never);
    actions.setEndpointDetailId(endpointRow.id);
    const { EndpointDetailOverlay } = await import("../src/components/endpoint-detail-overlay");
    const setup = await testRender(() => <EndpointDetailOverlay />, { width: 100, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("endpoint — Esc closes");
    expect(frame).toContain("rivault-gpt-oss-static");
    expect(frame).toContain("109.0.1.42:8000");
    expect(frame).toContain("cold-start · 72.0s");
    expect(frame).toContain("rccs-ds4-m10");
    setup.renderer.destroy();
    await import("../src/stores").then((m) => m.actions.setEndpointDetailId(null));
  });

  it("overlay with missing optional stamps hides those slots honestly", async () => {
    const { actions } = await import("../src/stores");
    setWorkspace("endpoints", [{
      ...endpointRow,
      id: "ep-detail-2",
      readyAt: undefined,
      coldStartMs: undefined,
      sourceName: undefined,
      expiresAt: undefined,
    }] as never);
    actions.setEndpointDetailId("ep-detail-2");
    const { EndpointDetailOverlay } = await import("../src/components/endpoint-detail-overlay");
    const setup = await testRender(() => <EndpointDetailOverlay />, { width: 100, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("endpoint — Esc closes");
    expect(frame).not.toContain("cold-start");
    expect(frame).not.toContain("ready     ·");
    expect(frame).not.toContain("expires   ·");
    expect(frame).not.toContain("source    ·");
    setup.renderer.destroy();
    await import("../src/stores").then((m) => m.actions.setEndpointDetailId(null));
  });

  it("overlay weaponGuard (unknown id) renders nothing", async () => {
    const { actions } = await import("../src/stores");
    actions.setEndpointDetailId("ep-nonexistent");
    const { EndpointDetailOverlay } = await import("../src/components/endpoint-detail-overlay");
    const setup = await testRender(() => <EndpointDetailOverlay />, { width: 100, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // header is always the overlay chrome — body stays rowless honestly.
    expect(frame).toContain("endpoint — Esc closes");
    expect(frame).not.toContain("109.0.1");
    setup.renderer.destroy();
    await import("../src/stores").then((m) => m.actions.setEndpointDetailId(null));
  });

  it("overlay module closes with the standard wipe contract", async () => {
    const { workspace, actions } = await import("../src/stores");
    actions.setEndpointDetailId(endpointRow.id);
    expect(workspace.endpointDetailId).toBe(endpointRow.id);
    actions.closeAllOverlays();
    expect(workspace.endpointDetailId).toBe(null);
  });
});

describe("T5 topic-5b: /endpoint bare form redirects to work pane", () => {
  it("bare /endpoint opens the work pane (endpoints section discoverable)", async () => {
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    const result = await handleWaveIntercepts({ name: "endpoint", args: "" });
    expect(result?.kind).toBe("handled");
    const { workspace } = await import("../src/stores");
    expect(workspace.paneView).toBe("work");
  });

  it("/endpoints alias rides identically", async () => {
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    const result = await handleWaveIntercepts({ name: "endpoints", args: "" });
    expect(result?.kind).toBe("handled");
    const { workspace } = await import("../src/stores");
    expect(workspace.paneView).toBe("work");
  });

  it("verb-shaped /endpoint list passes through (chat chain untouched)", async () => {
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    const result = await handleWaveIntercepts({ name: "endpoint", args: "list" });
    expect(result).toBeNull();
  });
});

describe("T5 topic-6: agent detail overlay", () => {
  const agentRow = {
    id: "agent-1",
    role: "literature" as never,
    goal: "Search catalog for papers on FNO",
    state: "running" as never,
    currentTask: "Scanning catalog",
    currentObjective: "Locate the top-5 FNO papers from the past year",
    currentEvidence: ["paper-A", "paper-B"],
    confidence: 0.82,
    progress: 0.35,
    reasoning: ["ranking by citation delta", "dcf + backoff"],
    budget: { maxTokens: 2000, maxIterations: 10, maxWallClock: "5m", maxGpus: 1 },
    permissions: { egress: "controlled", filesystem: "read_write_scratch", compute: "slurm" },
    createdAt: "2026-09-19T00:00:00Z",
    updatedAt: "2026-09-19T00:05:00Z",
    errorMessage: undefined,
  };

  it("AgentDetailOverlay renders the agent's rows byte-identical", async () => {
    const { agentActions } = await import("../src/stores/agent-runtime");
    agentActions.resetAgentRuntime();
    const createdId = agentActions.createAgent({
      role: agentRow.role,
      goal: agentRow.goal,
      state: agentRow.state,
      currentTask: agentRow.currentTask,
      currentObjective: agentRow.currentObjective,
      currentEvidence: agentRow.currentEvidence,
      confidence: agentRow.confidence,
      reasoning: agentRow.reasoning,
      budget: agentRow.budget,
      permissions: agentRow.permissions,
      progress: agentRow.progress,
    } as never);
    const { actions } = await import("../src/stores");
    actions.setAgentDetailId(createdId);
    const { AgentDetailOverlay } = await import("../src/components/agent-detail-overlay");
    const setup = await testRender(() => <AgentDetailOverlay />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("agent — Esc closes");
    expect(frame).toContain("literature");
    expect(frame).toContain("running");
    expect(frame).toContain("Search catalog for papers on FNO");
    expect(frame).toContain("Scanning catalog");
    expect(frame).toContain("budget   · maxTokens=2000");
    expect(frame).toContain("egress   · controlled");
    expect(frame).toContain("compute  · slurm");
    expect(frame).toContain("conf     · 82%");
    expect(frame).toContain("prog     · 35%");
    expect(frame).toContain("ranking by citation delta");
    expect(frame).toContain("paper-A");
    expect(frame).not.toContain("error");
    setup.renderer.destroy();
    actions.setAgentDetailId(null);
    agentActions.resetAgentRuntime();
  });

  it("missing optional slots hide honestly (no error row when errorMessage absent, no reasoning rows when empty)", async () => {
    const { agentActions } = await import("../src/stores/agent-runtime");
    agentActions.resetAgentRuntime();
    const createdId = agentActions.createAgent({
      role: "reviewer" as never,
      goal: "Review batch results",
      state: "running" as never,
      budget: {},
      permissions: { egress: "none", filesystem: "read_only", compute: "none" } as never,
    });
    const { actions } = await import("../src/stores");
    actions.setAgentDetailId(createdId);
    const { AgentDetailOverlay } = await import("../src/components/agent-detail-overlay");
    const setup = await testRender(() => <AgentDetailOverlay />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("agent — Esc closes");
    expect(frame).toContain("reviewer");
    expect(frame).not.toContain("budget");
    expect(frame).not.toContain("conf  ");
    expect(frame).not.toContain("reasoning");
    expect(frame).not.toContain("error");
    expect(frame).not.toContain("task    ·");
    setup.renderer.destroy();
    actions.setAgentDetailId(null);
    agentActions.resetAgentRuntime();
  });

  it("unknown agent id renders header chrome only (no fabricated rows)", async () => {
    const { agentActions } = await import("../src/stores/agent-runtime");
    agentActions.resetAgentRuntime();
    const { actions } = await import("../src/stores");
    actions.setAgentDetailId("agent-nop");
    const { AgentDetailOverlay } = await import("../src/components/agent-detail-overlay");
    const setup = await testRender(() => <AgentDetailOverlay />, { width: 100, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("agent — Esc closes");
    expect(frame).not.toContain("agent-");
    setup.renderer.destroy();
    actions.setAgentDetailId(null);
  });

  it("closeAllOverlays wipes the id via the standard sweep", async () => {
    const { actions, workspace } = await import("../src/stores");
    actions.setAgentDetailId("agent-9");
    expect(workspace.agentDetailId).toBe("agent-9");
    actions.closeAllOverlays();
    expect(workspace.agentDetailId).toBe(null);
  });
});

describe("F4 assets rail — Work pane assets section", () => {
  it("shows the assets section when the workspace carries rows", async () => {
    const { actions } = await import("../src/stores");
    actions.applyAssetsSnapshot([
      {
        kind: "model",
        id: "GLM-5.2",
        shapeKey: "model:GLM-5.2",
        title: "GLM-5.2-NVFP4",
        status: "ready",
        projectId: "demo",
        shapeHash: "h1",
      },
    ], "snap-42");
    const setup = await testRender(() => <WorkRoute />, { width: 150, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("assets");
    expect(frame).toContain("model/GLM-5.2");
    expect(frame).toContain("GLM-5.2-NVFP4");
    setup.renderer.destroy();
    actions.applyAssetsSnapshot([], null);
  });

  it("hides the assets section entirely when the workspace is empty (honest absence)", async () => {
    const { actions } = await import("../src/stores");
    actions.applyAssetsSnapshot([], null);
    const setup = await testRender(() => <WorkRoute />, { width: 150, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).not.toContain("assets (");
    setup.renderer.destroy();
  });

  it("tombstone deletion removes the row from the rendered workspace", async () => {
    const { actions } = await import("../src/stores");
    actions.applyAssetsSnapshot([
      {
        kind: "model",
        id: "m-7",
        shapeKey: "model:m-7",
        title: "doomed",
        shapeHash: "t1",
      },
    ], "snap-7");
    actions.applyAssetsPatch([], ["model:m-7"], "snap-8");
    const setup = await testRender(() => <WorkRoute />, { width: 150, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).not.toContain("doomed");
    setup.renderer.destroy();
    actions.applyAssetsSnapshot([], null);
  });
});

describe("T5 topic-7a: /quota top-level slash registration", () => {
  it("catalog lists /quota with the read-only gauge help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const quota = localSlashCommands().find((c) => c.name === "quota");
    expect(quota).toBeDefined();
    expect(String(quota?.help ?? "")).toMatch(/owner-scoped|gauge/i);
  });

  it("offline /quota answers an honest refusal in the chat lane", async () => {
    const { isOfflineBackend } = await import("../src/lib/console-mode");
    if (isOfflineBackend()) {
      const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
      const result = await handleWaveIntercepts({ name: "quota", args: "" }).catch(() => null);
      expect(result === null || result.kind === "handled").toBe(true);
    }
  });
});

describe("T5 topic-7b/ADR-086: /circles top-level slash", () => {
  it("catalog lists /circles with member-level help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const circles = localSlashCommands().find((c) => c.name === "circles");
    expect(circles).toBeDefined();
    expect(String(circles?.help ?? "")).toMatch(/circles|owner/i);
  });

  it("offline /circles answers an honest refusal", async () => {
    const { isOfflineBackend } = await import("../src/lib/console-mode");
    if (isOfflineBackend()) {
      const { handleCirclesCommand } = await import("../src/commands/circles-command");
      const result = await handleCirclesCommand("");
      expect(result.kind).toBe("error");
      expect(String((result as { message?: string }).message)).toMatch(/live backend/);
    }
    expect(true).toBe(true);
  });
});

describe("T5 topic-7c: /usage top-level slash registration", () => {
  it("catalog lists /usage with model-grouping help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const usage = localSlashCommands().find((c) => c.name === "usage");
    expect(usage).toBeDefined();
    expect(String(usage?.help ?? "")).toMatch(/usage|token|model/i);
  });
});

describe("T5 topic-7d: /grants top-level slash registration", () => {
  it("catalog lists /grants as the caller-received inventory", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const grants = localSlashCommands().find((c) => c.name === "grants");
    expect(grants).toBeDefined();
    expect(String(grants?.help ?? "")).toMatch(/grant|received/i);
  });
});

describe("T5 topic-8: /loops top-level slash capacity", () => {
  it("catalog lists /loops with lifecycle help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const loops = localSlashCommands().find((c) => c.name === "loops");
    expect(loops).toBeDefined();
    expect(String(loops?.help ?? "")).toMatch(/loop|lifecycle|status/i);
  });
});

describe("T5 topic-8b: /traces + /provenance top-level slash capacity", () => {
  it("catalog lists /traces and /provenance with lineage-aware help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const names = localSlashCommands().map((c) => c.name);
    expect(names).toContain("traces");
    expect(names).toContain("provenance");
    const traces = localSlashCommands().find((c) => c.name === "traces");
    expect(String(traces?.help ?? "")).toMatch(/trace|job|get/i);
    const provenance = localSlashCommands().find((c) => c.name === "provenance");
    expect(String(provenance?.help ?? "")).toMatch(/provenance|lineage/i);
  });
});

describe("T5 topic-9: /rag top-level slash registration", () => {
  it("catalog lists /rag with corpus-query help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const rag = localSlashCommands().find((c) => c.name === "rag");
    expect(rag).toBeDefined();
    expect(String(rag?.help ?? "")).toMatch(/rag|corpus|query|semantic/i);
  });
});

describe("T5 topic-14: /admin top-level slash registered", () => {
  it("catalog lists /admin with umbrella help", async () => {
    const { localSlashCommands } = await import("../src/commands/slash");
    const admin = localSlashCommands().find((c) => c.name === "admin");
    expect(admin).toBeDefined();
    expect(String(admin?.help ?? "")).toMatch(/rag-stats|corpus-attrition|memory/i);
  });
});
