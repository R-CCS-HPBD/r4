/**
 * Live RC1 pipeline graph driven by project_job_event phases.
 */

import { agentActions, agentRuntime } from "../stores/agent-runtime";
import type { WorkflowNode } from "../types";

const PIPELINE: { id: string; label: string; ops: string[] }[] = [
  { id: "rc1-import", label: "Import", ops: ["import"] },
  { id: "rc1-init", label: "Init", ops: ["init"] },
  { id: "rc1-plan", label: "Plan", ops: ["plan"] },
  { id: "rc1-readiness", label: "Readiness", ops: ["readiness"] },
  { id: "rc1-run", label: "Run", ops: ["run"] },
];

function ensureLivePipelineGraph(): void {
  const hasRc1 = agentRuntime.workflowGraph.nodes.some((n) => n.id.startsWith("rc1-"));
  if (hasRc1) return;

  // Don't clobber a populated demo DAG mid-session unless empty.
  if (agentRuntime.workflowGraph.nodes.length > 0) return;

  for (const step of PIPELINE) {
    agentActions.addWorkflowNode({
      id: step.id,
      type: "knowledge",
      label: step.label,
      status: "pending",
    });
  }
  for (let i = 0; i < PIPELINE.length - 1; i++) {
    agentActions.addWorkflowEdge({ from: PIPELINE[i]!.id, to: PIPELINE[i + 1]!.id });
  }
}

function nodeIdForOperation(operation: string): string | null {
  const op = operation.toLowerCase();
  const step = PIPELINE.find((s) => s.ops.includes(op));
  return step?.id ?? null;
}

function phaseToStatus(phase: string): WorkflowNode["status"] | null {
  if (phase === "started") return "running";
  if (phase === "done") return "completed";
  if (phase === "error") return "failed";
  if (phase === "cancelled") return "failed";
  return null;
}

/** Update (or create) the live RC1 pipeline graph from a job event. */
export function applyRc1JobToWorkflowGraph(operation: string, phase: string, jobId?: string): void {
  ensureLivePipelineGraph();
  const nodeId = nodeIdForOperation(operation);
  if (!nodeId) return;
  const status = phaseToStatus(phase);
  if (!status) return;

  const exists = agentRuntime.workflowGraph.nodes.some((n) => n.id === nodeId);
  if (!exists) {
    agentActions.addWorkflowNode({
      id: nodeId,
      type: "knowledge",
      label: operation,
      status,
      metadata: jobId ? { jobId } : undefined,
    });
  } else {
    agentActions.updateWorkflowNode(nodeId, status, jobId ? { jobId, operation } : { operation });
  }
}
