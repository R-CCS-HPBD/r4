/**
 * ADR-092 slice 3 ("surfaces"): team plans in the console.
 *
 * - plan-live: run-keyed team_event lines → the Work pane's plan row and
 *   the agent_result handler's per-agent result lines (✓ / ✖ / ⊘);
 * - the teams store routes run-keyed lines to the plan lane and leaves
 *   session-keyed teamup lines on theirs;
 * - `/teamup on plan:<template>` (wave-intercepts) checks the template,
 *   arms it, and the next chat message runs `team.plan_run` with it as goal;
 * - a never-auto classification is never remembered or answered by a UI rule;
 *   the local autopilot resolver may auto-answer it, and cards name their agent;
 * - the Work pane lists plan runs with status, agents and cost; Enter reads
 *   the result, `c` cancels.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setTeamsState, teamsActions, teamsStore } from "../src/stores/teams";
import {
  agentResultLine,
  agentResultLines,
  applyPlanEvent,
  isPlanEvent,
  planEventLine,
  planRowLabel,
  teamAgentChips,
  type PlanEventLine,
} from "../src/lib/plan-live";
import { resetApprovalMirrorsForTests, handleApprovalRequest, respondToolApproval } from "../src/bridge/approval-bridge";
import { agentActions } from "../src/stores/agent-runtime";

type Call = { method: string; params: Record<string, unknown>; timeout?: number };

function fakeBridge(replies: Record<string, unknown> = {}) {
  const calls: Call[] = [];
  const bridge = {
    calls,
    request: async (method: string, params: Record<string, unknown>, timeout?: number) => {
      calls.push({ method, params, timeout });
      if (method in replies) return replies[method];
      return {};
    },
    respondApproval: (id: unknown, decision: string) => {
      calls.push({ method: "respondApproval", params: { id, decision } });
    },
    on: () => undefined,
  };
  return bridge;
}

const TEMPLATES = {
  templates: [
    { name: "adversarial-review", description: "review", params: { target: { required: true } } },
    { name: "research-round", description: "round", params: { objective: { required: true } } },
  ],
};

function line(kind: string, extra: Partial<PlanEventLine> = {}): PlanEventLine {
  return { run_id: "run-1", plan: "mini", kind, agent: "", ...extra };
}

function resetTeams(): void {
  setTeamsState({
    sessions: [],
    status: {},
    live: {},
    loading: false,
    error: null,
    planRuns: {},
    planLive: {},
  });
}

beforeEach(() => {
  resetWorkspace();
  resetTeams();
  resetApprovalMirrorsForTests();
  agentActions.resetAgentRuntime();
  // A project no local permission rule names: the machine's own
  // /settings rules must not answer these cards.
  setWorkspace("project", { id: "proj-a92-slice3", name: "t", description: "", status: "active", goalIds: [] });
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 60));
  setCompanionBridgeForTests(null);
  resetWorkspace();
  resetTeams();
  agentActions.resetAgentRuntime();
});

// ── plan-live (pure) ─────────────────────────────────────────────────────────

describe("plan-live", () => {
  it("tells run-keyed plan lines from session-keyed teamup lines", () => {
    expect(isPlanEvent(line("agent_result"))).toBe(true);
    expect(isPlanEvent({ session_id: "s", kind: "agent_message", iteration: 1 })).toBe(false);
    expect(isPlanEvent({ run_id: "", kind: "x" })).toBe(false);
    expect(isPlanEvent(null)).toBe(false);
  });

  it("folds a run's events into its row: status, agents ok/null, tokens, phase", () => {
    let row = applyPlanEvent(undefined, line("plan_started"));
    row = applyPlanEvent(row, line("phase", { phase: "check" }));
    row = applyPlanEvent(row, line("agent_started", { agent: "a#0" }));
    row = applyPlanEvent(row, line("agent_result", { agent: "a#0", ok: true, tokens: 1200 }));
    row = applyPlanEvent(row, line("agent_started", { agent: "a#1" }));
    row = applyPlanEvent(row, line("agent_failed", { agent: "a#1", reason: "schema_invalid", tokens: 300 }));
    expect(row.status).toBe("running");
    expect(row.agents).toEqual({ started: 2, ok: 1, null: 1 });
    expect(row.cost?.tokens).toBe(1500);
    expect(planRowLabel(row)).toBe("plan mini · running · agents 1✓ 1✖ · 1.5k tok · @check");
    row = applyPlanEvent(row, line("plan_finished", { status: "partial", spent_tokens: 1800 }));
    expect(row.status).toBe("partial");
    expect(row.cost?.tokens).toBe(1800);
    expect(row.live).toBe(false);
  });

  it("renders each finished agent as one result line (✓ answer · ✖ reason · ⊘ cancelled)", () => {
    expect(agentResultLine(line("agent_result", { agent: "scan[0]#1", tokens: 2500, preview: '{"findings":\n["x"]}' })))
      .toBe('✓ scan[0]#1 · 2.5k tok — {"findings": ["x"]}');
    expect(agentResultLine(line("agent_failed", { agent: "v#2", reason: "tool_refused:shell_exec", refused_tools: ["shell_exec"] })))
      .toBe("✖ v#2 · tool_refused:shell_exec · refused shell_exec");
    expect(agentResultLine(line("agent_cancelled", { agent: "v#3", reason: "cancelled" }))).toBe("⊘ v#3 · cancelled");
  });

  it("keeps the newest result per agent, in the order agents finished", () => {
    const ring = [
      line("agent_failed", { agent: "a", reason: "cancelled" }),
      line("agent_result", { agent: "b", preview: "B" }),
      line("agent_round", { agent: "a", round: 1 }),
      line("agent_result", { agent: "a", preview: "A" }),
    ];
    expect(agentResultLines(ring)).toEqual(["✓ a — A", "✓ b — B"]);
  });

  it("shows automatic gate provenance and its autopilot notes", () => {
    expect(planEventLine(line("gate_answered", { gate: "review", approved: true, decided_by: "autopilot" })))
      .toBe("gate review → approved · autopilot");
    expect(planEventLine(line("plan_log", { text: "[autopilot] never_auto ask auto-answered" })))
      .toBe("[autopilot] never_auto ask auto-answered");
  });

  it("team rows show agents only when reported (slice 4: no budget chip)", () => {
    expect(teamAgentChips(["pm", "se"])).toBe("2 agents");
    expect(teamAgentChips(undefined)).toBe("");
  });
});

// ── the teams store: the plan lane and the agent_result handler ─────────────

describe("teams store plan lane", () => {
  it("routes run-keyed lines to the plan lane and posts per-agent results", () => {
    teamsActions.ingestEvent(line("plan_started"));
    teamsActions.ingestEvent(line("agent_started", { agent: "gatherer" }));
    teamsActions.ingestEvent(line("agent_result", { agent: "gatherer", preview: "facts", tokens: 10 }));
    teamsActions.ingestEvent(line("agent_failed", { agent: "checker#0", reason: "schema_invalid: x" }));

    expect(teamsStore.planRuns["run-1"]?.agents).toEqual({ started: 1, ok: 1, null: 1 });
    expect(teamsStore.planLive["run-1"]?.length).toBe(4);
    expect(Object.keys(teamsStore.live)).toEqual([]); // the teamup lane is untouched
    const details = workspace.noticeDetails["plan:run-1"] ?? [];
    expect(details).toEqual(["✓ gatherer · 10 tok — facts", "✖ checker#0 · schema_invalid: x"]);
    const notice = workspace.chatMessages.find((m) => m.content.startsWith("plan mini"));
    expect(notice?.content).toContain("1 ok · 1 null");
  });

  it("a finished run lands as the run's final receipt", () => {
    teamsActions.ingestEvent(line("agent_result", { agent: "a", preview: "x" }));
    teamsActions.ingestEvent(line("plan_finished", { status: "cancelled", spent_tokens: 5 }));
    expect(teamsStore.planRuns["run-1"]?.status).toBe("cancelled");
    const notice = workspace.chatMessages.find((m) => m.content.startsWith("⏺ plan mini"));
    expect(notice?.content).toContain("finished cancelled");
  });

  it("session-keyed teamup lines keep their lane", () => {
    teamsActions.ingestEvent({ session_id: "s-1", kind: "iteration_started", iteration: 1 } as never);
    expect(teamsStore.live["s-1"]?.length).toBe(1);
    expect(Object.keys(teamsStore.planRuns)).toEqual([]);
  });

  it("team.runs fills the plan rows; teams stay on teamup.list", async () => {
    const bridge = fakeBridge({
      "team.runs": {
        runs: [
          { kind: "plan", run_id: "p-1", name: "mini", status: "success", agents: { ok: 3, null: 0 } },
          { kind: "team", run_id: "t-1", name: "crew", status: "paused" },
        ],
      },
    });
    setCompanionBridgeForTests(bridge as never);
    await teamsActions.listPlanRuns();
    expect(Object.keys(teamsStore.planRuns)).toEqual(["p-1"]);
  });
});

// ── /teamup on plan:<template> ───────────────────────────────────────────────

describe("/teamup on plan:<template>", () => {
  it("checks the template, arms it, and the next message runs team.plan_run", async () => {
    const bridge = fakeBridge({
      "team.plan_templates": TEMPLATES,
      "team.plan_run": { ok: true, run_id: "run-9", plan: "adversarial-review", status: "running" },
    });
    setCompanionBridgeForTests(bridge as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    const result = await handleWaveIntercepts({ name: "teamup", args: "on plan:adversarial-review focus=races" });
    expect(result?.kind).toBe("handled");
    expect(workspace.teamupArmed).toBe(true);
    expect(workspace.teamupArmedPlan).toEqual({ template: "adversarial-review", args: { focus: "races" } });

    actions.sendChatMessage("src/scheduler/queue.py");
    expect(workspace.teamupArmed).toBe(false); // one-shot
    expect(workspace.teamupArmedPlan).toBeNull();
    expect(workspace.chatStreaming).toBe(false); // not a chat turn
    await new Promise((r) => setTimeout(r, 40));
    const run = bridge.calls.find((c) => c.method === "team.plan_run");
    expect(run?.params).toEqual({
      template: "adversarial-review",
      goal: "src/scheduler/queue.py",
      args: { focus: "races" },
    });
    expect(run?.timeout ?? 0).toBeGreaterThan(60_000); // the human answers the card first
    expect(bridge.calls.some((c) => c.method === "teamup.start")).toBe(false);
    expect(teamsStore.planRuns["run-9"]?.status).toBe("running");
  });

  it("an unknown template is refused by name and nothing is armed", async () => {
    setCompanionBridgeForTests(fakeBridge({ "team.plan_templates": TEMPLATES }) as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on plan:nope" });
    expect(workspace.teamupArmed).toBe(false);
    expect(workspace.chatMessages.at(-1)?.content).toContain('no template "nope"');
  });

  it("a stray word after the template is refused (the goal is the next message)", async () => {
    setCompanionBridgeForTests(fakeBridge({ "team.plan_templates": TEMPLATES }) as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on plan:research-round why" });
    expect(workspace.teamupArmed).toBe(false);
  });

  it("/teamup off disarms a plan; arming a crew replaces it", async () => {
    setCompanionBridgeForTests(fakeBridge({ "team.plan_templates": TEMPLATES }) as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on plan:research-round" });
    expect(workspace.teamupArmedPlan?.template).toBe("research-round");
    await handleWaveIntercepts({ name: "teamup", args: "on hpc" });
    expect(workspace.teamupArmedPlan).toBeNull();
    expect(workspace.teamupArmedCrew).toBe("hpc");
    await handleWaveIntercepts({ name: "teamup", args: "on plan:research-round" });
    await handleWaveIntercepts({ name: "teamup", args: "off" });
    expect(workspace.teamupArmed).toBe(false);
    expect(workspace.teamupArmedPlan).toBeNull();
  });

  it("a card the human rejects says nothing ran", async () => {
    setCompanionBridgeForTests(
      fakeBridge({ "team.plan_run": { ok: false, status: "rejected", error: "not approved" } }) as never,
    );
    const runId = await teamsActions.startPlan("research-round", "why");
    expect(runId).toBeNull();
    expect(workspace.chatMessages.at(-1)?.content).toContain("not approved (nothing ran)");
  });
});

// ── approval cards ───────────────────────────────────────────────────────────

describe("approval cards (MD-5, agent label)", () => {
  it("a never-auto card opens every time, even after a once", () => {
    const bridge = fakeBridge();
    setCompanionBridgeForTests(bridge as never);
    handleApprovalRequest({ id: 1, tool: "team_plan_run", risk_tier: "never_auto" });
    expect(workspace.toolApproval?.neverAuto).toBe(true);
    expect(workspace.toolApproval?.options).toEqual(["once", "reject"]);
    respondToolApproval("once");
    handleApprovalRequest({ id: 2, tool: "team_plan_run", risk_tier: "never_auto" });
    expect(workspace.toolApproval?.id).toBe(2); // not auto-answered
    expect(bridge.calls.filter((c) => c.method === "respondApproval").length).toBe(1);
  });

  it("the card names the agent that asks", () => {
    handleApprovalRequest({ id: 3, tool: "shell_exec", agent: "verify[2]#1", session_approval_allowed: true });
    expect(workspace.toolApproval?.agent).toBe("verify[2]#1");
    actions.closeToolApproval();
    handleApprovalRequest({ id: 4, tool: "file_write", agent: "", session_approval_allowed: true });
    expect(workspace.toolApproval?.agent).toBeUndefined();
  });

  it("the overlay shows the agent and the never-auto line", async () => {
    const { ToolApprovalOverlay } = await import("../src/components/tool-approval-overlay");
    actions.openToolApproval({
      id: 5,
      tool: "team_plan_run",
      description: "Run team plan",
      sessionApprovalAllowed: false,
      agent: "gate review/tie",
      neverAuto: true,
    });
    const setup = await testRender(() => <ToolApprovalOverlay />, { width: 100, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("gate review/tie");
    expect(frame).toContain("never auto-approved");
    expect(frame).not.toContain("a always this session");
    setup.renderer.destroy();
    actions.closeToolApproval();
  });
});

// ── the Work pane ────────────────────────────────────────────────────────────

describe("Work pane plan rows", () => {
  it("lists plan runs with status, agents and cost; Enter reads the result; c cancels", async () => {
    const { WorkRoute } = await import("../src/routes/work");
    const bridge = fakeBridge({
      "team.runs": {
        runs: [
          {
            kind: "plan",
            run_id: "run-7",
            name: "review-x",
            status: "running",
            agents: { started: 4, ok: 3, null: 1 },
            cost: { tokens: 42_000 },
            project_id: "proj-a92-slice3",
            live: true,
          },
        ],
      },
      "team.plan_result": { ok: true, run_id: "run-7", plan: "review-x", status: "running" },
      "team.plan_cancel": { ok: true, run_id: "run-7", status: "cancelled" },
      "teamup.list": { sessions: [], count: 0 },
    });
    setCompanionBridgeForTests(bridge as never);
    setWorkspace("paneFocus", true);
    const setup = await testRender(() => <WorkRoute />, { width: 140, height: 30 });
    await new Promise((r) => setTimeout(r, 40));
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("teams (1)");
    expect(frame).toContain("plan review-x · running · agents 3✓ 1✖ · 42.0k tok");

    setup.mockInput.pressEnter();
    await new Promise((r) => setTimeout(r, 40));
    expect(bridge.calls.some((c) => c.method === "team.plan_result" && c.params.run_id === "run-7")).toBe(true);

    setup.mockInput.pressKey("c");
    await new Promise((r) => setTimeout(r, 40));
    const cancel = bridge.calls.find((c) => c.method === "team.plan_cancel");
    expect(cancel?.params.run_id).toBe("run-7");
    setup.renderer.destroy();
  });

  it("team rows carry agents when the bridge reports them", async () => {
    const { WorkRoute } = await import("../src/routes/work");
    const session = {
      session_id: "sess-crew-001",
      name: "crew",
      status: "paused",
      iteration: 2,
      project_id: "proj-a92-slice3",
      roles: ["pm", "se", "pg"],
    };
    setCompanionBridgeForTests(
      fakeBridge({ "teamup.list": { sessions: [session], count: 1 }, "team.runs": { runs: [] } }) as never,
    );
    setTeamsState("sessions", [session]);
    const setup = await testRender(() => <WorkRoute />, { width: 160, height: 30 });
    await new Promise((r) => setTimeout(r, 40));
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("3 agents");
    expect(frame).not.toContain("budget");
    setup.renderer.destroy();
  });
});
