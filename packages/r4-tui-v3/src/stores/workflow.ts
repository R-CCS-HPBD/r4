/**
 * Research Loop Store — compositional research DAG (not UMRS workflows).
 *
 * For registered UMRS Workflow catalog/run state, use `umrs-workflow.ts`.
 *
 * Manages Research Loop assembly state: building multi-step loops from typed
 * steps, tracking inline errors per step, and gating execution.
 *
 * Usage:
 *   1. Call workflowActions.createStep(type, payload) to add steps
 *   2. Call workflowActions.assemble() to validate the 4-step chain
 *   3. Call workflowActions.approveGate(gateId) to advance past a gate
 *
 * Architectural Invariants:
 *   - Workflow steps are ordered; insertion at arbitrary positions not allowed
 *   - Gates block execution until approved (human-in-the-loop)
 *   - Inline errors per step prevent silent failures
 *   - Max 16 steps per workflow (safety limit)
 */

import { createStore } from "solid-js/store";
import { ulid } from "ulid";
import { isDemoVisuals } from "../lib/console-mode";
import { agentActions } from "./agent-runtime";
import type { WorkflowNodeType, WorkflowNodeStatus, ApprovalType, ApprovalGate } from "../types";

// ── Step Types ──────────────────────────────────────────────────────────

export type WorkflowStepType =
  | "ingest"
  | "extract"
  | "hypothesis"
  | "experiment"
  | "analysis"
  | "review"
  | "publication"
  | "gate";

export interface WorkflowStep {
  id: string;
  type: WorkflowStepType;
  label: string;
  status: WorkflowNodeStatus;
  payload?: Record<string, unknown>;
  /** Inline errors for this step (never silent failure). */
  errors: string[];
  /** Gate metadata when type is 'gate'. */
  gateType?: ApprovalType;
  gateTitle?: string;
}

// ── Store State ─────────────────────────────────────────────────────────

export interface WorkflowStoreState {
  /** Ordered steps in the current workflow. */
  steps: WorkflowStep[];
  /** Whether the workflow is assembled (validated) and ready to run. */
  assembled: boolean;
  /** Whether the workflow is currently running. */
  running: boolean;
  /** Currently pending gate (null when no gate blocks execution). */
  pendingGate: WorkflowStep | null;
  /** All gates created in this session. */
  gates: WorkflowStep[];
  /** Max steps allowed (safety limit). */
  maxSteps: number;
  /** Validation errors from the last assemble() call. */
  validationErrors: string[];
}

/** Fresh state per call: createStore adopts (and mutates) the object it is
 * given, so a shared initial object made reset() keep the old steps. */
const initialState = (): WorkflowStoreState => ({
  steps: [],
  assembled: false,
  running: false,
  pendingGate: null,
  gates: [],
  maxSteps: 16,
  validationErrors: [],
});

const [workflowStore, setWorkflowStore] = createStore<WorkflowStoreState>(initialState());
/** The demo run's pending step timer — cancelled by reset(). */
let runTimer: ReturnType<typeof setTimeout> | null = null;

export { workflowStore };

// ── Step Type Metadata ──────────────────────────────────────────────────

const stepMetadata: Record<WorkflowStepType, { label: string; nodeType: WorkflowNodeType }> = {
  ingest: { label: "Ingest Artifacts", nodeType: "knowledge" },
  extract: { label: "Extract Knowledge", nodeType: "knowledge" },
  hypothesis: { label: "Generate Hypothesis", nodeType: "decision" },
  experiment: { label: "Run Experiment", nodeType: "experiment" },
  analysis: { label: "Analyze Results", nodeType: "agent" },
  review: { label: "Review Findings", nodeType: "review" },
  publication: { label: "Prepare Publication", nodeType: "publication" },
  gate: { label: "Approval Gate", nodeType: "decision" },
};

function patchStep(
  steps: WorkflowStep[],
  id: string,
  patch: Partial<WorkflowStep>,
): WorkflowStep[] {
  return steps.map((step) => (step.id === id ? { ...step, ...patch } : step));
}

/** Demo executor shared by runWorkflow and approveGate: runs the steps from
 * `index` in order (running → completed, 500 ms each), pauses at the next
 * gate, and finishes with every step completed and running=false. The step
 * timer is tracked in runTimer so reset() cancels it (audit r2 #50 review:
 * approveGate's own loop never advanced and re-marked one step forever). */
function runDemoFrom(index: number): void {
  const step = workflowStore.steps[index];
  if (!step) {
    setWorkflowStore("running", false);
    setWorkflowStore("steps", (s) => s.map((st) => ({ ...st, status: "completed" as WorkflowNodeStatus })));
    return;
  }
  if (step.type === "gate") {
    setWorkflowStore("pendingGate", step);
    setWorkflowStore("steps", (s) => patchStep(s, step.id, { status: "waiting" }));
    setWorkflowStore("running", false);
    return;
  }
  setWorkflowStore("steps", (s) => patchStep(s, step.id, { status: "running" }));
  runTimer = setTimeout(() => {
    runTimer = null;
    setWorkflowStore("steps", (s) => patchStep(s, step.id, { status: "completed" }));
    runDemoFrom(index + 1);
  }, 500);
}

// ── Step Templates ──────────────────────────────────────────────────────

/**
 * Standard step templates for building a 4-step workflow quickly.
 */
export function getStandardStepTemplates() {
  return [
    {
      name: "Full Research Pipeline",
      description: "ingest → extract → hypothesis → experiment",
      types: ["ingest", "extract", "hypothesis", "experiment"] as WorkflowStepType[],
    },
    {
      name: "Analysis Loop",
      description: "extract → analysis → review → publication",
      types: ["extract", "analysis", "review", "publication"] as WorkflowStepType[],
    },
    {
      name: "Hypothesis Test",
      description: "hypothesis → gate → experiment → analysis",
      types: ["hypothesis", "gate", "experiment", "analysis"] as WorkflowStepType[],
    },
    {
      name: "Review Cycle",
      description: "ingest → review → gate → publication",
      types: ["ingest", "review", "gate", "publication"] as WorkflowStepType[],
    },
  ];
}

// ── Actions ─────────────────────────────────────────────────────────────

/** Why createStep would return null — for honest UI errors. */
export function createStepBlockReason(): "not_demo" | "full" | null {
  if (!isDemoVisuals()) return "not_demo";
  if (workflowStore.steps.length >= workflowStore.maxSteps) return "full";
  return null;
}

export const CREATE_STEP_NOT_DEMO =
  "Local demo only — start with r4 console --demo, or use Research [R]un demo / /project run";
export const CREATE_STEP_FULL = (max: number) => `Workflow is full (max ${max} steps)`;

export const workflowActions = {
  /**
   * Create a new workflow step and append it.
   * Returns null when not in demo visuals or at max steps — use createStepBlockReason().
   */
  createStep(type: WorkflowStepType, payload?: Record<string, unknown>): WorkflowStep | null {
    if (createStepBlockReason()) return null;

    const meta = stepMetadata[type];
    const step: WorkflowStep = {
      id: ulid(),
      type,
      label: (payload?.label as string) ?? meta.label,
      status: "pending",
      payload,
      errors: [],
      gateType: type === "gate" ? ("experiment_design" as ApprovalType) : undefined,
      gateTitle: type === "gate" ? "Gate: Approve Next Step" : undefined,
    };

    setWorkflowStore("steps", (s) => [...s, step]);
    setWorkflowStore("assembled", false);

    agentActions.emitEvent("agent_started", { source: "workflow_store", action: "step_added", type });
    return step;
  },

  /**
   * Add multiple steps from a template.
   */
  addStepsFromTemplate(template: { name: string; types: WorkflowStepType[] }): {
    created: WorkflowStep[];
    errors: string[];
  } {
    const errors: string[] = [];
    const created: WorkflowStep[] = [];

    const block = createStepBlockReason();
    if (block === "not_demo") {
      errors.push(CREATE_STEP_NOT_DEMO);
      return { created, errors };
    }

    if (workflowStore.steps.length + template.types.length > workflowStore.maxSteps) {
      errors.push(CREATE_STEP_FULL(workflowStore.maxSteps));
      return { created, errors };
    }

    for (const type of template.types) {
      const step = workflowActions.createStep(type);
      if (step) created.push(step);
      else {
        const why = createStepBlockReason();
        errors.push(
          why === "full"
            ? CREATE_STEP_FULL(workflowStore.maxSteps)
            : why === "not_demo"
              ? CREATE_STEP_NOT_DEMO
              : `Failed to add step: ${type}`,
        );
      }
    }

    return { created, errors };
  },

  /**
   * Remove a step by ID.
   */
  removeStep(id: string) {
    setWorkflowStore("steps", (s) => s.filter((step) => step.id !== id));
    setWorkflowStore("assembled", false);
  },

  /**
   * Add an inline error to a step.
   */
  addStepError(id: string, error: string) {
    setWorkflowStore("steps", (s) => {
      const step = s.find((st) => st.id === id);
      if (!step) return s;
      return patchStep(s, id, { errors: [...step.errors, error] });
    });
  },

  /**
   * Clear errors from a specific step.
   */
  clearStepErrors(id: string) {
    setWorkflowStore("steps", (s) => {
      if (!s.some((st) => st.id === id)) return s;
      return patchStep(s, id, { errors: [] });
    });
  },

  /**
   * Validate all steps and return errors.
   */
  _validateSteps(): string[] {
    const steps = workflowStore.steps;

    if (steps.length < 2) {
      return ["Need at least 2 steps to assemble a workflow"];
    }

    const errors: string[] = [];
    for (const step of steps) {
      if (step.errors.length > 0) {
        errors.push(`Step "${step.label}" has ${step.errors.length} error(s)`);
      }
      if (step.status !== "pending") {
        errors.push(`Step "${step.label}" is "${step.status}", not "pending"`);
      }
    }
    return errors;
  },

  /**
   * Validate all steps and mark workflow as assembled.
   */
  assemble(): { valid: boolean; errors: string[] } {
    const validationErrors = workflowActions._validateSteps();

    if (validationErrors.length > 0) {
      setWorkflowStore("validationErrors", validationErrors);
      return { valid: false, errors: validationErrors };
    }

    // Mark all steps as pending and assemble
    for (const step of workflowStore.steps) {
      step.status = "pending";
      step.errors = [];
    }
    setWorkflowStore("assembled", true);
    setWorkflowStore("validationErrors", []);

    agentActions.emitEvent("agent_started", {
      source: "workflow_store",
      action: "assembled",
      stepCount: workflowStore.steps.length,
    });
    return { valid: true, errors: [] };
  },

  /**
   * Start running the workflow.
   */
  runWorkflow() {
    if (!workflowStore.assembled) return;
    setWorkflowStore("running", true);
    runDemoFrom(0);
  },

  /**
   * Approve a gate from the TUI.
   *
   * Human-in-the-loop control. Gate status: waiting → approved → continue.
   */
  approveGate(gateId: string) {
    const gate = workflowStore.gates.find((g) => g.id === gateId)
      || workflowStore.steps.find((s) => s.id === gateId);

    if (!gate) return false;

    const approvalGate: ApprovalGate = {
      id: ulid(),
      type: (gate.gateType as ApprovalType) ?? "experiment_design",
      status: "approved",
      title: gate.gateTitle ?? "Gate approved",
      requestedAt: new Date().toISOString(),
      resolvedAt: new Date().toISOString(),
    };

    agentActions.resolveApproval(approvalGate.id, "approved");

    setWorkflowStore("steps", (s) => patchStep(s, gateId, { status: "completed" }));

    setWorkflowStore("pendingGate", null);
    setWorkflowStore("running", true);

    // Continue from the step after the gate (shared executor: advances,
    // pauses at the next gate, finishes with running=false).
    runDemoFrom(workflowStore.steps.findIndex((s) => s.id === gateId) + 1);
    return true;
  },

  /**
   * Reject a gate.
   */
  rejectGate(gateId: string) {
    const gate = workflowStore.gates.find((g) => g.id === gateId)
      || workflowStore.steps.find((s) => s.id === gateId);

    if (!gate) return false;

    agentActions.resolveApproval(gateId, "rejected");

    setWorkflowStore("steps", (s) => patchStep(s, gateId, { status: "failed" }));

    setWorkflowStore("pendingGate", null);
    setWorkflowStore("running", false);
    return true;
  },

  /**
   * Simulate a validation error on a step (for inline error display testing).
   */
  simulateStepError(id: string, error: string) {
    workflowActions.addStepError(id, error);
  },

  /**
   * Reset the workflow store to initial state.
   */
  reset() {
    if (runTimer) clearTimeout(runTimer);
    runTimer = null;
    setWorkflowStore(initialState());
  },
};
