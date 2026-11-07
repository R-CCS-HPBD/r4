/**
 * Research live-wiring: readiness, workflow graph, explanations, poll → mission.
 */

import { describe, expect, it, beforeEach, afterEach } from "bun:test";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { knowledgeActions, knowledgeStore } from "../src/stores/knowledge";
import { actions, workspace } from "../src/stores";
import { applyProjectKnowledgePoll } from "../src/hooks/useKnowledge";
import {
  applyReadinessAssessment,
  isReadinessGate,
  parseReadinessResult,
  projectIdFromReadinessGate,
} from "../src/lib/readiness-sync";
import { applyRc1JobToWorkflowGraph } from "../src/lib/rc1-workflow-graph";
import {
  handleProjectJobEvent,
  resetProjectJobEventsForTests,
} from "../src/commands/project-job-events";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";
import { isDemoVisuals } from "../src/lib/console-mode";
import { handleResearchGraphShortcut } from "../src/lib/space-shortcuts";
import type { ProjectKnowledgePollResult } from "../src/adapters/types";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  knowledgeActions.reset();
  actions.resetWorkspace();
  resetProjectJobEventsForTests();
  setDemoMode(false);
  setDemoVisuals(false);
});

afterEach(() => {
  setDemoMode(false);
  setDemoVisuals(false);
  resetProjectJobEventsForTests();
});

describe("readiness sync", () => {
  it("parses readiness RPC payload", () => {
    const a = parseReadinessResult({
      ready: false,
      readinessLevel: "low",
      readinessChecks: [{ check: "plan", passed: false, details: "missing" }],
      blockers: ["Need plan"],
    });
    expect(a.ready).toBe(false);
    expect(a.blockers).toEqual(["Need plan"]);
  });

  it("maps Companion can_run / readiness_level / user_approved", () => {
    const a = parseReadinessResult({
      can_run: true,
      readiness_level: "medium",
      user_approved: false,
      blockers: [],
      readiness_checks: [{ name: "coverage", ok: true, message: "ok" }],
    });
    expect(a.ready).toBe(true);
    expect(a.canRun).toBe(true);
    expect(a.userApproved).toBe(false);
    expect(a.readinessLevel).toBe("medium");
    expect(a.readinessChecks[0]?.check).toBe("coverage");
  });

  it("creates approval gates from blockers", () => {
    applyReadinessAssessment(
      {
        ready: false,
        readinessLevel: "low",
        readinessChecks: [],
        blockers: ["Missing artifacts"],
      },
      "demo-proj",
    );
    expect(knowledgeStore.readiness?.ready).toBe(false);
    const pending = agentActions.getPendingApprovals();
    expect(pending.length).toBeGreaterThan(0);
    expect(isReadinessGate(pending[0]!.objectId)).toBe(true);
    expect(projectIdFromReadinessGate(pending[0]!.objectId)).toBe("demo-proj");
  });
});

describe("RC1 workflow graph", () => {
  it("builds pipeline nodes from job phases", () => {
    applyRc1JobToWorkflowGraph("import", "started", "job-1");
    expect(agentRuntime.workflowGraph.nodes.some((n) => n.id === "rc1-import")).toBe(true);
    const node = agentRuntime.workflowGraph.nodes.find((n) => n.id === "rc1-import");
    expect(node?.status).toBe("running");

    applyRc1JobToWorkflowGraph("import", "done", "job-1");
    expect(
      agentRuntime.workflowGraph.nodes.find((n) => n.id === "rc1-import")?.status,
    ).toBe("completed");
  });

  it("handleProjectJobEvent updates graph", () => {
    handleProjectJobEvent({
      jobId: "j1",
      operation: "plan",
      phase: "started",
      message: "planning",
    });
    expect(agentRuntime.workflowGraph.nodes.some((n) => n.id === "rc1-plan")).toBe(true);
  });
});

describe("poll → mission control", () => {
  it("maps plan hypotheses into missionControl", () => {
    const result: ProjectKnowledgePollResult = {
      knowledge: [],
      tasks: [],
      ingestion: null,
      coverage: null,
      readiness: null,
      plan: {
        planId: "p1",
        projectId: "proj",
        researchGoal: "Find catalysts",
        objectives: [],
        hypotheses: [{ id: "h1", statement: "H1 works", confidence: 0.7, status: "proposed" }],
        experiments: [],
        status: "proposed",
        createdAt: new Date().toISOString(),
      },
      loop: null,
      sessions: null,
    };
    applyProjectKnowledgePoll(result);
    expect(agentRuntime.missionControl.researchGoal).toBe("Find catalysts");
    expect(agentRuntime.missionControl.hypotheses).toHaveLength(1);
    expect(agentRuntime.missionControl.hypotheses[0]!.confidence).toBe(0.7);
  });
});

describe("explanations from bridge helpers", () => {
  it("addExplanation populates explain list", () => {
    agentActions.addExplanation({
      decisionId: "d1",
      decisionType: "tool_result",
      target: "lab_catalog_search",
      reasoningChain: ["ran search"],
      graphPaths: [],
      supportingEvidence: [],
      confidenceCalculation: { prior: 0.5, likelihood: 0.5, posterior: 0.5 },
      alternatives: [],
      rejected: [],
      timestamp: new Date().toISOString(),
      agentId: "companion",
    });
    expect(agentRuntime.explanations).toHaveLength(1);
    expect(agentRuntime.explanations[0]!.target).toBe("lab_catalog_search");
  });
});

describe("demo gate alignment", () => {
  it("isDemoVisuals is independent of offline-only isDemoMode", () => {
    setDemoVisuals(true);
    setDemoMode(false);
    expect(isDemoVisuals()).toBe(true);
  });
});

describe("context selection prefixes", () => {
  it("selectObject accepts wf and agent prefixes", () => {
    actions.selectObject("wf:rc1-run");
    expect(workspace.selectedObjectId).toBe("wf:rc1-run");
    actions.selectObject("agent:abc");
    expect(workspace.selectedObjectId).toBe("agent:abc");
  });
});

describe("audit fix pass", () => {
  it("WORKFLOW_EMPTY names RC1 pipeline not UMRS-only", async () => {
    const { WORKFLOW_EMPTY } = await import("../src/lib/empty-hints");
    expect(WORKFLOW_EMPTY).toMatch(/RC1/i);
    expect(WORKFLOW_EMPTY).not.toMatch(/^No UMRS/);
  });

  it("graphCursorIndex follows selectedObjectId only", async () => {
    const { graphCursorIndex } = await import("../src/components/knowledge-graph-viewer");
    const nodes = [
      { id: "a", label: "A", type: "task" as const, status: "pending" },
      { id: "b", label: "B", type: "task" as const, status: "pending" },
      { id: "c", label: "C", type: "task" as const, status: "pending" },
    ];
    expect(graphCursorIndex(nodes, "b")).toBe(1);
    expect(graphCursorIndex(nodes, "missing")).toBe(0);
    expect(graphCursorIndex(nodes, null)).toBe(0);
  });

  it("selectObject on graph node updates selectedObjectId for Context", () => {
    actions.setTasks([
      { id: "task-exp", goalId: "g1", title: "Experiment for: There is a sig", status: "pending" },
      { id: "task-obj", goalId: "g1", title: "Develop a thorough understanding", status: "pending" },
    ]);
    actions.selectObject("task-exp");
    expect(workspace.selectedObjectId).toBe("task-exp");
    const task = workspace.tasks.find((t) => t.id === workspace.selectedObjectId);
    expect(task?.title).toContain("Experiment for");
  });

  it("resolveContextSelection resolves catalog knowledge by id", async () => {
    const { resolveContextSelection } = await import("../src/layout/context-panel");
    actions.setKnowledge([
      { id: "dataset-MANIFEST-A77F64", name: "MANIFEST.json", status: "ready", documentCount: 1 },
    ]);
    const sel = resolveContextSelection("dataset-MANIFEST-A77F64");
    expect(sel.kind).toBe("knowledge");
    if (sel.kind === "knowledge") {
      expect(sel.data.name).toBe("MANIFEST.json");
    }
  });

  it("entering Knowledge space drops leftover endpoint selection", () => {
    actions.setKnowledge([
      { id: "dataset-batch-3", name: "batch-3-yields.csv", status: "ready", documentCount: 1 },
    ]);
    actions.selectObject("ep-public");
    actions.setSpace("knowledge");
    expect(workspace.selectedObjectId).toBe("dataset-batch-3");
  });

  it("resolveContextSelection resolves catalog knowledge by filename slug", async () => {
    const { resolveContextSelection } = await import("../src/layout/context-panel");
    actions.setKnowledge([
      { id: "text-README-81A6E4", name: "README.md", status: "ready", documentCount: 1 },
    ]);
    const byName = resolveContextSelection("README.md");
    expect(byName.kind).toBe("knowledge");
    const bySlug = resolveContextSelection("text-README-81A6E4");
    expect(bySlug.kind).toBe("knowledge");
    if (bySlug.kind === "knowledge") {
      expect(bySlug.data.name).toBe("README.md");
    }
  });

  it("resolveContextSelection resolves hypotheses", async () => {
    const { resolveContextSelection } = await import("../src/layout/context-panel");
    agentActions.updateMissionControl({
      hypotheses: [{ id: "hyp-1", statement: "Catalyst X works", confidence: 0.7 }],
    });
    const sel = resolveContextSelection("hyp-1");
    expect(sel.kind).toBe("hypothesis");
    if (sel.kind === "hypothesis") {
      expect(sel.data.statement).toContain("Catalyst");
    }
  });

  it("formatGraphRowLabel keeps ellipsis instead of hard-cropping", async () => {
    const { formatGraphRowLabel } = await import("../src/components/knowledge-graph-viewer");
    const line = formatGraphRowLabel(
      {
        label: "Develop a thorough understanding of: Investigate ML models for materials",
        type: "task",
      },
      "T",
      40,
    );
    expect(line.endsWith("…")).toBe(true);
    expect(line.length).toBe(40);
    expect(line.startsWith("T Develop")).toBe(true);
  });

  it("research graph Enter asks about selected task", () => {
    actions.resetWorkspace();
    actions.setSpace("research");
    actions.setResearchFocus("graph");
    actions.setTasks([
      { id: "t-ask", goalId: "g1", title: "Investigate catalysts", status: "pending" },
    ]);
    actions.selectObject("t-ask");
    expect(handleResearchGraphShortcut("enter")).toBe(true);
    expect(workspace.paneView).toBe("dashboard");
    expect(workspace.pendingChatDraft).toContain("Investigate catalysts");
  });

  it("research graph k opens Knowledge", () => {
    actions.resetWorkspace();
    actions.setSpace("research");
    actions.setResearchFocus("graph");
    actions.setTasks([{ id: "t2", goalId: "g1", title: "Task two", status: "pending" }]);
    actions.selectObject("t2");
    expect(handleResearchGraphShortcut("k")).toBe(true);
    expect(workspace.paneView).toBe("knowledge");
  });

  it("Tab cycles research focus panes", async () => {
    const { cycleResearchFocus } = await import("../src/lib/research-keyboard");
    actions.resetWorkspace();
    actions.setSpace("research");
    expect(workspace.researchFocus).toBe("graph");
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("agents");
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("verify");
    expect(workspace.verifyInputFocused).toBe(true);
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("approvals");
    expect(workspace.verifyInputFocused).toBe(false);
    expect(workspace.researchBottomTab).toBe("ops");
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("strategy");
    expect(workspace.researchBottomTab).toBe("strategy");
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("explain");
    expect(workspace.researchBottomTab).toBe("explain");
    // Wrap: Explain → Graph must restore Ops so graph KeyTrap works again
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("graph");
    expect(workspace.researchBottomTab).toBe("ops");
  });

  it("setResearchBottomTab via ops/strategy/explain maps researchFocus like g,N", () => {
    actions.resetWorkspace();
    actions.setSpace("research");
    actions.setResearchBottomTab("explain");
    actions.setResearchFocus("explain");
    expect(workspace.researchFocus).toBe("explain");
    actions.setResearchBottomTab("strategy");
    actions.setResearchFocus("strategy");
    expect(workspace.researchBottomTab).toBe("strategy");
    actions.setResearchBottomTab("ops");
    actions.setResearchFocus("approvals");
    expect(workspace.researchFocus).toBe("approvals");
  });

  it("Tab does not cycle panes while Start actions menu is open", async () => {
    const { cycleResearchFocus } = await import("../src/lib/research-keyboard");
    actions.resetWorkspace();
    actions.setChatDraft(""); // clear seeded draft from the graph-ask test above
    actions.setSpace("research");
    actions.setResearchFocus("graph");
    actions.openStartActionsMenu();
    expect(cycleResearchFocus(1)).toBe(false);
    expect(workspace.researchFocus).toBe("graph");
    actions.closeStartActionsMenu();
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("agents");
  });

  it("y/n approvals work without card mouse focus", async () => {
    const { handleOpsApprovalKey } = await import("../src/lib/research-keyboard");
    const { agentActions, agentRuntime } = await import("../src/stores/agent-runtime");
    actions.resetWorkspace();
    actions.setSpace("research");
    actions.setResearchBottomTab("ops");
    agentActions.requestApproval(
      "experiment_execution",
      "Run batch",
      "test",
      undefined,
      "obj-1",
    );
    const gate = agentRuntime.approvalGates.find((g) => g.status === "pending");
    expect(gate).toBeDefined();
    expect(handleOpsApprovalKey("y")).toBe(true);
    expect(agentRuntime.approvalGates.find((g) => g.id === gate!.id)?.status).toBe("approved");
  });

  it("strategy demo keys work without panel focus", async () => {
    const { handleStrategyDemoKey } = await import("../src/lib/research-keyboard");
    const { setDemoVisuals } = await import("../src/lib/console-mode");
    const { strategyStore } = await import("../src/stores/research-strategy");
    actions.resetWorkspace();
    actions.setSpace("research");
    actions.setResearchBottomTab("strategy");
    setDemoVisuals(true);
    expect(handleStrategyDemoKey("e")).toBe(true);
    expect(strategyStore.currentAssessment).toBeTruthy();
    setDemoVisuals(false);
  });
});

describe("/project run (UMRS loop start) result → loop store", () => {
  it("maps a LoopState result and surfaces findings count", async () => {
    const { mapRunResultToLoop, applyRunJobToLoopStore } = await import(
      "../src/lib/loop-run-result"
    );
    const loop = mapRunResultToLoop(
      {
        loop_id: "loop-1",
        status: "running",
        phase: "planning",
        current_iteration: 2,
        max_iterations: 10,
        total_findings: 1,
        metadata: { hosting: "job", workflow_name: "demo08_yield_eval" },
      },
      "demo08",
    );
    expect(loop.loopId).toBe("loop-1");
    expect(loop.simulated).toBe(false);
    expect(loop.status).toBe("running");
    expect(loop.totalFindings).toBe(1);
    expect(loop.currentIteration).toBe(2);
    expect(loop.maxIterations).toBe(10);

    actions.resetWorkspace();
    actions.setProject({
      id: "demo08",
      name: "Demo",
      description: "goal",
      status: "active",
      goalIds: [],
    });
    applyRunJobToLoopStore("started", undefined, "demo08");
    expect(knowledgeStore.loop?.status).toBe("running");
    applyRunJobToLoopStore(
      "done",
      { loop_id: "loop-2", status: "running", current_iteration: 0, max_iterations: 4, total_findings: 0 },
      "demo08",
    );
    expect(knowledgeStore.loop?.loopId).toBe("loop-2");
    expect(knowledgeStore.loop?.status).toBe("running");
    expect(knowledgeStore.loop?.simulated).toBe(false);
    expect(knowledgeStore.loop?.totalFindings).toBe(0);
  });
});
