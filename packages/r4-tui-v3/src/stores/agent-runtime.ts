/**
 * Agent Runtime Store — orchestration-only state for scientific agents.
 *
 * This store manages ephemeral execution state. Persistent scientific
 * knowledge remains in UMRS.
 *
 * The store provides:
 * - Agent lifecycle (state machine transitions)
 * - Research queue (scientific intent items)
 * - Timeline (replayable scientific timeline)
 * - Approval gates (human-in-the-loop controls)
 * - Workflow graph (DAG of research steps)
 * - Mission control (aggregated project state)
 * - Explainability (decision traces)
 *
 * Architectural Invariants:
 * - UMRS owns ALL scientific knowledge
 * - R4 owns orchestration only
 * - Every agent must use the same reasoning engine (ScientificReasoningEngine)
 * - The Knowledge Graph remains the center
 */

import { createStore, produce } from "solid-js/store";
import { ulid } from "ulid";
import type {
  AgentRuntime,
  AgentState,
  ResearchQueueItem,
  ResearchIntent,
  TimelineEntry,
  TimelineEntryType,
  ApprovalGate,
  ApprovalType,
  ApprovalStatus,
  WorkflowGraph,
  WorkflowNode,
  WorkflowEdge,
  MissionControlState,
  DecisionExplanation,
  ScientificEvent,
  ScientificEventType,
  VerificationAnswer,
} from "../types";

// ── State Machine Definition ────────────────────────────────────────────

export const AGENT_STATE_TRANSITIONS: Record<AgentState, AgentState[]> = {
  idle: ["planning", "cancelled"],
  planning: ["waiting", "running", "failed", "cancelled"],
  waiting: ["running", "blocked", "cancelled"],
  running: ["review", "completed", "failed", "blocked", "cancelled"],
  blocked: ["waiting", "cancelled"],
  review: ["completed", "failed", "running", "cancelled"],
  completed: [],
  failed: ["planning", "cancelled"], // Allow retry
  cancelled: [],
};

// ── Store Interface ──────────────────────────────────────────────────────

export interface AgentRuntimeStore {
  // Agents
  agents: AgentRuntime[];
  // Research Queue
  researchQueue: ResearchQueueItem[];
  // Timeline
  timeline: TimelineEntry[];
  // Approval Gates
  approvalGates: ApprovalGate[];
  // Workflow Graph
  workflowGraph: WorkflowGraph;
  // Selected workflow node (for inspection)
  selectedWorkflowNodeId: string | null;
  // Mission Control
  missionControl: MissionControlState;
  // Explainability
  explanations: DecisionExplanation[];
  // Scientific Events (event log)
  events: ScientificEvent[];
}

// ── Initial State ────────────────────────────────────────────────────────

/** Full DAG used for offline demo seed only — not shown in live CLI mode. */
export const DEMO_WORKFLOW_GRAPH: WorkflowGraph = {
  nodes: [
    { id: "start", type: "knowledge", label: "Start", status: "completed" },
    { id: "ingest", type: "knowledge", label: "Ingest Artifacts", status: "pending" },
    { id: "extract", type: "knowledge", label: "Extract Knowledge", status: "pending" },
    { id: "hypothesis", type: "decision", label: "Generate Hypothesis", status: "pending" },
    { id: "experiment", type: "experiment", label: "Run Experiment", status: "pending" },
    { id: "analyze", type: "agent", label: "Analyze Results", status: "pending" },
    { id: "review", type: "review", label: "Review Findings", status: "pending" },
    { id: "publish", type: "publication", label: "Prepare Publication", status: "pending" },
  ],
  edges: [
    { from: "start", to: "ingest" },
    { from: "ingest", to: "extract" },
    { from: "extract", to: "hypothesis" },
    { from: "hypothesis", to: "experiment" },
    { from: "experiment", to: "analyze" },
    { from: "analyze", to: "review" },
    { from: "review", to: "publish" },
  ],
};

const emptyWorkflowGraph = (): WorkflowGraph => ({ nodes: [], edges: [] });

export function loadDemoWorkflowGraph(): void {
  setAgentRuntime("workflowGraph", {
    nodes: DEMO_WORKFLOW_GRAPH.nodes.map((n) => ({ ...n })),
    edges: DEMO_WORKFLOW_GRAPH.edges.map((e) => ({ ...e })),
  });
}

const initialWorkflowGraph = emptyWorkflowGraph();

const initialMissionControl: MissionControlState = {
  researchGoal: "",
  successCriteriaProgress: 0,
  activeAgents: [],
  runningExperiments: [],
  pendingApprovals: [],
  blockers: [],
  computeBudget: { remaining: 0, burnRate: 0, estimatedRunway: 0 },
  hypotheses: [],
};

const initialState: AgentRuntimeStore = {
  agents: [],
  researchQueue: [],
  timeline: [],
  approvalGates: [],
  // L4/V2: never hand the shared module objects to the live store —
  // every consumer mutates through a fresh clone; the constant stays the
  // pristine blueprint (pattern after the reset change above).
  workflowGraph: structuredClone(initialWorkflowGraph),
  selectedWorkflowNodeId: null,
  missionControl: structuredClone(initialMissionControl),
  explanations: [],
  events: [],
};

// ── Store Creation ───────────────────────────────────────────────────────

const [agentRuntime, setAgentRuntime] = createStore<AgentRuntimeStore>(initialState);

export { agentRuntime };

// ── Agent Runtime Actions ────────────────────────────────────────────────

export const agentActions = {
  // ── Agent Lifecycle ────────────────────────────────────────────────────

  createAgent(agent: Omit<AgentRuntime, "id" | "createdAt" | "updatedAt" | "state"> & { state?: AgentState }): string {
    const id = ulid();
    const now = new Date().toISOString();
    const newAgent: AgentRuntime = {
      id,
      role: agent.role,
      goal: agent.goal,
      state: agent.state ?? "idle",
      currentTask: agent.currentTask,
      currentObjective: agent.currentObjective,
      currentEvidence: agent.currentEvidence,
      confidence: agent.confidence,
      reasoning: agent.reasoning,
      budget: agent.budget,
      permissions: agent.permissions,
      progress: agent.progress,
      errorMessage: agent.errorMessage,
      createdAt: now,
      updatedAt: now,
    };
    setAgentRuntime("agents", (list) => [...list, newAgent]);
    agentActions.emitEvent("agent_started", { agentId: id, role: agent.role, goal: agent.goal });
    agentActions.addTimelineEntry("hypothesis_created", `Agent ${agent.role} started`, undefined, id);
    return id;
  },

  transitionAgent(agentId: string, newState: AgentState, updates?: Partial<AgentRuntime>): boolean {
    const agent = agentRuntime.agents.find((a) => a.id === agentId);
    if (!agent) return false;

    const allowed = AGENT_STATE_TRANSITIONS[agent.state];
    if (!allowed.includes(newState)) return false;

    setAgentRuntime("agents", (a) => a.id === agentId, (prev) => ({
      ...prev,
      state: newState,
      updatedAt: new Date().toISOString(),
      ...updates,
    }));

    // Emit appropriate event based on state transition
    const eventMap: Record<AgentState, ScientificEventType | null> = {
      idle: null,
      planning: "agent_started",
      waiting: null,
      running: "agent_progress",
      blocked: null,
      review: "agent_progress",
      completed: "agent_completed",
      failed: "agent_failed",
      cancelled: null,
    };
    const eventType = eventMap[newState];
    if (eventType) {
      agentActions.emitEvent(eventType, { agentId, newState, ...updates });
    }

    return true;
  },

  updateAgentProgress(agentId: string, progress: number, reasoning?: string): void {
    setAgentRuntime("agents", (a) => a.id === agentId, (prev) => ({
      ...prev,
      progress,
      reasoning: reasoning ? [...(prev.reasoning ?? []), reasoning] : prev.reasoning,
      updatedAt: new Date().toISOString(),
    }));
    agentActions.emitEvent("agent_progress", { agentId, progress, reasoning });
  },

  /** Bypass state machine — for live Companion/RC1 monitor patches. */
  patchAgent(agentId: string, updates: Partial<AgentRuntime>): void {
    setAgentRuntime("agents", (a) => a.id === agentId, (prev) => ({
      ...prev,
      ...updates,
      updatedAt: new Date().toISOString(),
    }));
  },

  getAgentById(id: string): AgentRuntime | undefined {
    return agentRuntime.agents.find((a) => a.id === id);
  },

  getAgentsByState(state: AgentState): AgentRuntime[] {
    return agentRuntime.agents.filter((a) => a.state === state);
  },

  // ── Research Queue ─────────────────────────────────────────────────────

  enqueueResearch(intent: ResearchIntent, options?: { hypothesisId?: string; experimentId?: string; priority?: number; dependencies?: string[]; agentId?: string }): string {
    const id = ulid();
    const now = new Date().toISOString();
    const item: ResearchQueueItem = {
      id,
      intent,
      hypothesisId: options?.hypothesisId,
      experimentId: options?.experimentId,
      agentId: options?.agentId,
      priority: options?.priority ?? 0,
      dependencies: options?.dependencies ?? [],
      status: "pending",
      createdAt: now,
      updatedAt: now,
    };
    setAgentRuntime("researchQueue", (list) => [...list, item]);
    agentActions.emitEvent("experiment_scheduled", { queueItemId: id, intent });
    return id;
  },

  updateQueueItem(id: string, status: ResearchQueueItem["status"]): void {
    setAgentRuntime("researchQueue", (item) => item.id === id, (prev) => ({
      ...prev,
      status,
      updatedAt: new Date().toISOString(),
    }));
  },

  cancelQueueItem(id: string): void {
    agentActions.updateQueueItem(id, "cancelled");
  },

  getPendingQueueItems(): ResearchQueueItem[] {
    return agentRuntime.researchQueue.filter((item) => item.status === "pending");
  },

  // ── Timeline ───────────────────────────────────────────────────────────

  addTimelineEntry(type: TimelineEntryType, title: string, description?: string, agentId?: string, objectId?: string): string {
    const id = ulid();
    const entry: TimelineEntry = {
      id,
      type,
      timestamp: new Date().toISOString(),
      title,
      description,
      agentId,
      objectId,
    };
    setAgentRuntime("timeline", (list) => [entry, ...list]);
    return id;
  },

  getTimeline(): TimelineEntry[] {
    return agentRuntime.timeline;
  },

  // ── Approval Gates ─────────────────────────────────────────────────────

  requestApproval(type: ApprovalType, title: string, description?: string, agentId?: string, objectId?: string): string {
    const id = ulid();
    const gate: ApprovalGate = {
      id,
      type,
      status: "pending",
      title,
      description,
      agentId,
      objectId,
      requestedAt: new Date().toISOString(),
    };
    setAgentRuntime("approvalGates", (list) => [...list, gate]);
    agentActions.emitEvent("approval_requested", { gateId: id, type, title });
    return id;
  },

  resolveApproval(id: string, status: ApprovalStatus, comment?: string): void {
    setAgentRuntime("approvalGates", (gate) => gate.id === id, (prev) => ({
      ...prev,
      status,
      comment,
      resolvedAt: new Date().toISOString(),
    }));

    const eventMap: Record<ApprovalStatus, ScientificEventType> = {
      pending: "approval_requested",
      approved: "approval_granted",
      rejected: "approval_denied",
      modified: "approval_granted",
    };
    agentActions.emitEvent(eventMap[status], { gateId: id, status, comment });
  },

  getPendingApprovals(): ApprovalGate[] {
    return agentRuntime.approvalGates.filter((g) => g.status === "pending");
  },

  // ── Workflow Graph ─────────────────────────────────────────────────────

  updateWorkflowNode(nodeId: string, status: WorkflowNode["status"], metadata?: Record<string, unknown>): void {
    setAgentRuntime("workflowGraph", "nodes", (node) => node.id === nodeId, (prev) => ({
      ...prev,
      status,
      metadata: { ...prev.metadata, ...metadata },
    }));
    agentActions.emitEvent("workflow_node_entered", { nodeId, status });
  },

  selectWorkflowNode(nodeId: string): void {
    setAgentRuntime("selectedWorkflowNodeId", nodeId);
  },

  addWorkflowNode(node: Omit<WorkflowNode, "id"> & { id?: string }): string {
    const id = node.id ?? ulid();
    const newNode: WorkflowNode = { ...node, id };
    setAgentRuntime("workflowGraph", "nodes", (list) => [...list, newNode]);
    return id;
  },

  addWorkflowEdge(edge: WorkflowEdge): void {
    setAgentRuntime("workflowGraph", "edges", (list) => [...list, edge]);
  },

  /** Replace the whole workflow graph (replaces-in-place per wave-tick;
   * project-specific waves swap by execution id so rows never blur). */
  setWorkflowGraph(graph: WorkflowGraph): void {
    setAgentRuntime("workflowGraph", {
      nodes: graph.nodes.map((n) => ({ ...n })),
      edges: graph.edges.map((e) => ({ ...e })),
    });
  },

  getWorkflowGraph(): WorkflowGraph {
    return agentRuntime.workflowGraph;
  },

  // ── Mission Control ────────────────────────────────────────────────────

  updateMissionControl(updates: Partial<MissionControlState>): void {
    setAgentRuntime("missionControl", (prev) => ({ ...prev, ...updates }));
  },

  refreshMissionControl(): void {
    const activeAgents = agentRuntime.agents.filter((a) => a.state === "running" || a.state === "planning" || a.state === "waiting");
    const runningExperiments = agentRuntime.researchQueue.filter((item) => item.status === "in_progress");
    const pendingApprovals = agentRuntime.approvalGates.filter((g) => g.status === "pending");

    setAgentRuntime("missionControl", (prev) => ({
      ...prev,
      activeAgents,
      runningExperiments,
      pendingApprovals,
    }));
  },

  // ── Explainability ─────────────────────────────────────────────────────

  addExplanation(explanation: DecisionExplanation): void {
    setAgentRuntime("explanations", (list) => [explanation, ...list]);
  },

  getExplanations(): DecisionExplanation[] {
    return agentRuntime.explanations;
  },

  // ── Verification Query (Phase 10 — TASK-046 wired to UMRS) ────────────

  queryVerification(question: string): VerificationAnswer {
    // TASK-046: Call real UMRS HTTP endpoint for verification chat.
    // Offline/demo may still use local runtime for exploration.
    const isOffline =
      process.env["R4_TUI_BACKEND"] === "offline" ||
      process.env["R4_TUI_ALLOW_DEMO_VERIFY"] === "1";

    // TUI audit r2 #63: no direct UMRS HTTP here. The old branch fired an
    // un-awaited fetch and returned a canned "session persisted" answer, which
    // skipped the real verify path. Live mode returns the gated stub, and
    // resolveVerificationForUi falls through to `r4 project verify` (gateway +
    // identity).

    // Offline/demo mode: use local runtime for exploration (V-02 Phase 1 gate)
    if (!isOffline) {
      const empty: VerificationAnswer = {
        question,
        answer:
          "Verification Chat is not connected to UMRS. Run `r4 project verify` (or set R4_TUI_BACKEND=offline to explore demo answers). No evidence was retrieved.",
        reasoningPath: [
          "CLI/live mode: demo verification answers are disabled",
          "Use r4 project verify for real evidence",
        ],
        evidenceIds: [],
        graphTraversal: [],
        confidence: 0,
        supportingPapers: [],
        supportingExperiments: [],
        relatedHypotheses: [],
      };
      agentActions.emitEvent("agent_reasoning", {
        source: "verification_query",
        gated: true,
        question,
      });
      return empty;
    }

    const q = question.toLowerCase();
    const goal = agentRuntime.missionControl.researchGoal || "No research goal set";
    const hypotheses = agentRuntime.missionControl.hypotheses.slice(0, 3);
    const latestExplanation = agentRuntime.explanations[0];
    const activeAgentCount = agentRuntime.agents.filter((a) => a.state === "running" || a.state === "planning" || a.state === "waiting").length;
    const pendingApprovals = agentRuntime.approvalGates.filter((g) => g.status === "pending");
    const blockedAgents = agentRuntime.agents.filter((a) => a.state === "blocked");
    const runningExperiments = agentRuntime.researchQueue.filter((i) => i.status === "in_progress" || i.intent === "submit_hpc_job");
    const workflowNodes = agentRuntime.workflowGraph.nodes.length;
    const workflowEdges = agentRuntime.workflowGraph.edges.length;

    const evidence = (latestExplanation?.supportingEvidence ?? []).slice(0, 5);
    const evidenceIds = evidence.map((e) => e.id);
    const supportingPapers = evidence.map((e) => `${e.id}: ${e.description}`);
    const supportingExperiments = runningExperiments
      .map((item) => item.experimentId)
      .filter((id): id is string => Boolean(id))
      .slice(0, 3)
      .map((id) => `${id}: queued/running`);
    const relatedHypotheses = hypotheses.map((h) => `${h.id}: ${h.statement}`);
    const baseReasoning = latestExplanation?.reasoningChain?.slice(0, 4) ?? [
      `Mission goal: ${goal}`,
      `${activeAgentCount} active agents currently contributing evidence`,
      `${pendingApprovals.length} pending approvals constrain execution`,
    ];
    const graphTraversal = latestExplanation?.graphPaths?.[0] ?? [
      "MissionControl",
      "->",
      `${workflowNodes} workflow nodes`,
      "->",
      `${workflowEdges} workflow edges`,
    ];

    let answer = `Current research goal: ${goal}.`;
    let reasoningPath = baseReasoning;
    let confidence = latestExplanation?.confidenceCalculation?.posterior ?? 0.62;

    if (q.includes("support") || q.includes("evidence")) {
      answer = evidence.length > 0
        ? `${evidence.length} evidence items support the latest decision path.`
        : "No supporting evidence has been registered yet in the runtime explanation store.";
      reasoningPath = [
        "Collected latest decision explanation",
        `Supporting evidence count: ${evidence.length}`,
        "Mapped evidence into verification response",
      ];
      confidence = evidence.length > 0
        ? Math.min(0.95, Math.max(0.45, evidence.reduce((sum, item) => sum + item.confidence, 0) / evidence.length))
        : 0.35;
    } else if (q.includes("contradict") || q.includes("refute")) {
      const rejected = latestExplanation?.rejected ?? [];
      answer = rejected.length > 0
        ? `${rejected.length} alternatives were rejected; review rejection reasons for contradiction signals.`
        : "No explicit contradiction/rejection records found in current decision explanation.";
      reasoningPath = rejected.length > 0
        ? rejected.slice(0, 3).map((r) => `${r.id}: ${r.reason}`)
        : ["No rejected alternatives available", "Check blockers and pending approvals for unresolved contradictions"];
      confidence = rejected.length > 0 ? 0.58 : 0.4;
    } else if (q.includes("missing") || q.includes("gap")) {
      const gapLines: string[] = [];
      if (pendingApprovals.length > 0) gapLines.push(`${pendingApprovals.length} pending approvals`);
      if (blockedAgents.length > 0) gapLines.push(`${blockedAgents.length} blocked agents`);
      const blockedQueue = agentRuntime.researchQueue.filter((i) => i.dependencies.length > 0 && i.status === "pending").length;
      if (blockedQueue > 0) gapLines.push(`${blockedQueue} dependency-blocked queue items`);
      answer = gapLines.length > 0
        ? `Detected execution gaps: ${gapLines.join(", ")}.`
        : "No major execution gaps detected in current runtime snapshot.";
      reasoningPath = [
        "Checked approval-gate backlog",
        "Checked blocked agent states",
        "Checked dependency-blocked research queue",
      ];
      confidence = gapLines.length > 0 ? 0.83 : 0.7;
    } else if (q.includes("graph") || q.includes("show")) {
      answer = `Workflow graph has ${workflowNodes} nodes and ${workflowEdges} edges; mission has ${hypotheses.length} active hypotheses.`;
      reasoningPath = [
        "Read workflow graph state",
        "Read mission-control hypotheses",
        "Summarized graph-centric project status",
      ];
      confidence = 0.98;
    } else if (q.includes("why") || q.includes("reason")) {
      answer = latestExplanation
        ? `Latest decision ${latestExplanation.decisionId} targets ${latestExplanation.target} based on recorded reasoning chain.`
        : `Decision rationale unavailable; using mission-control aggregates for explanation.`;
      reasoningPath = baseReasoning;
      confidence = latestExplanation?.confidenceCalculation?.posterior ?? 0.55;
    }

    const verificationAnswer: VerificationAnswer = {
      question,
      answer,
      reasoningPath,
      evidenceIds,
      graphTraversal,
      confidence,
      supportingPapers,
      supportingExperiments,
      relatedHypotheses,
    };

    agentActions.emitEvent("agent_reasoning", {
      source: "verification_query",
      question,
      confidence,
      evidenceCount: evidenceIds.length,
    });

    return verificationAnswer;
  },

  // ── Scientific Events (Event Log) ──────────────────────────────────────

  emitEvent(eventType: ScientificEventType, payload?: Record<string, unknown>): string {
    const id = ulid();
    const event: ScientificEvent = {
      id,
      eventType,
      timestamp: new Date().toISOString(),
      payload: payload ?? {},
      provenance: [],
    };
    setAgentRuntime("events", (list) => [event, ...list].slice(0, 1000));
    return id;
  },

  getEvents(): ScientificEvent[] {
    return agentRuntime.events;
  },

  getEventsByType(eventType: ScientificEventType): ScientificEvent[] {
    return agentRuntime.events.filter((e) => e.eventType === eventType);
  },

  // ── Reset ──────────────────────────────────────────────────────────────

  resetAgentRuntime(): void {
    setAgentRuntime(produce((state) => {
      state.agents = [];
      state.researchQueue = [];
      state.timeline = [];
      state.approvalGates = [];
      state.workflowGraph = structuredClone(initialWorkflowGraph);
      // L4/V2 (2026-09-18): initialMissionControl is a shared module-level
      // object — assignment-as-reset resets nothing: test file order
      // interleavings pushed hypotheses onto the shared array and the
      // "stays empty" contract regressed nondeterministically. clone so the
      // reset is actually a reset.
      state.missionControl = structuredClone(initialMissionControl);
      state.explanations = [];
      state.events = [];
      state.selectedWorkflowNodeId = null;
    }));
  },
};
