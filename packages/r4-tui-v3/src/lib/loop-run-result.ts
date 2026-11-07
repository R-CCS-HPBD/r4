/**
 * Map /project run job results (``r4 project loop start --json`` → LoopState
 * dict) into knowledgeStore.loop + notices. The RC1 in-process run retired
 * with WP-07 (2026-09-07); /project run now starts the UMRS loop, so the job
 * completes as soon as the loop exists and the loop record carries progress.
 */

import { knowledgeActions, knowledgeStore } from "../stores/knowledge";
import { actions, workspace, activeTab } from "../stores";
import type { ResearchLoop } from "../types";

export function provisionalLoopFromRunStart(
  projectId: string,
  maxIterations = 2,
): ResearchLoop {
  return {
    loopId: `run:${projectId}`,
    projectId,
    researchGoal: activeTab().project?.description ?? projectId,
    status: "running",
    phase: "running",
    currentIteration: 0,
    maxIterations,
    totalHypothesesTested: 0,
    totalExperimentsRun: 0,
    totalFindings: 0,
    iterations: [],
    simulated: false,
  };
}

function pickNumber(result: Record<string, unknown>, keys: string[], fallback: number): number {
  for (const key of keys) {
    const value = result[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (Array.isArray(value)) return value.length;
  }
  return fallback;
}

function pickString(result: Record<string, unknown>, keys: string[], fallback: string): string {
  for (const key of keys) {
    const value = result[key];
    if (typeof value === "string" && value) return value;
  }
  return fallback;
}

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(["running", "paused", "stopped", "failed"]);

function loopStatus(raw: string): ResearchLoop["status"] {
  return (TERMINAL_STATUSES.has(raw) ? raw : "completed") as ResearchLoop["status"];
}

function mapIterationRow(row: unknown, index: number) {
  const r = (row ?? {}) as Record<string, unknown>;
  return {
    iterationId: pickString(r, ["iterationId", "iteration_id", "id"], `iter-${index + 1}`),
    iterationNumber: pickNumber(r, ["iterationNumber", "iteration_number", "number"], index + 1),
    phase: pickString(r, ["phase"], "done"),
    hypothesis: pickString(r, ["hypothesis"], ""),
    experimentId: pickString(r, ["experimentId", "experiment_id"], ""),
    status: pickString(r, ["status"], "completed"),
  };
}

function mapIterationRows(rows: unknown[]) {
  return rows.map(mapIterationRow);
}

export function mapRunResultToLoop(
  result: Record<string, unknown>,
  projectId: string,
): ResearchLoop {
  const iterationsRaw = Array.isArray(result.iterations) ? result.iterations : [];
  const iterCount = pickNumber(result, ["current_iteration", "iterations"], iterationsRaw.length);
  const findingsCount = pickNumber(result, ["total_findings", "findings"], 0);
  const metadata = (result.metadata ?? {}) as Record<string, unknown>;
  const simulated = Boolean(result.simulated ?? metadata.simulated);
  const status = loopStatus(pickString(result, ["status", "phase"], "completed"));
  const maxFromResult = pickNumber(result, ["max_iterations", "maxIterations"], 0);
  return {
    loopId: pickString(result, ["loop_id", "run_id", "runId"], `run:${projectId}`),
    projectId,
    researchGoal: pickString(
      result,
      ["research_goal", "researchGoal"],
      activeTab().project?.description ?? projectId,
    ),
    status,
    phase: pickString(result, ["phase"], status),
    currentIteration: iterCount,
    maxIterations: maxFromResult > 0 ? maxFromResult : Math.max(iterCount, 2),
    totalHypothesesTested: pickNumber(result, ["total_hypotheses_tested", "totalHypothesesTested"], iterCount),
    totalExperimentsRun: pickNumber(
      result,
      ["total_experiments_run", "totalExperimentsRun", "experiments_run"],
      iterCount,
    ),
    totalFindings: findingsCount,
    iterations: mapIterationRows(iterationsRaw),
    simulated,
  };
}

function applyTerminalFailure(phase: string, projectId: string): void {
  const prev = knowledgeStore.loop;
  knowledgeActions.setLoop({
    ...(prev ?? provisionalLoopFromRunStart(projectId)),
    status: phase === "cancelled" ? "stopped" : "failed",
    phase,
  });
}

function applyProgress(result: unknown, projectId: string): void {
  if (!result || typeof result !== "object") return;
  const raw = result as Record<string, unknown>;
  const prev = knowledgeStore.loop ?? provisionalLoopFromRunStart(projectId);
  knowledgeActions.setLoop({
    ...prev,
    status: "running",
    phase: pickString(raw, ["phase"], "running"),
    currentIteration: pickNumber(raw, ["iteration", "currentIteration", "current_iteration"], prev.currentIteration),
    totalFindings: pickNumber(raw, ["findings", "total_findings"], prev.totalFindings),
    simulated: raw.simulated !== undefined ? Boolean(raw.simulated) : prev.simulated,
  });
}

function findingStatement(finding: unknown): string {
  if (typeof finding === "string") return finding;
  const record = (finding ?? {}) as Record<string, unknown>;
  return String(record.statement ?? JSON.stringify(finding));
}

function applyDone(result: unknown, projectId: string): void {
  const raw = result && typeof result === "object" ? (result as Record<string, unknown>) : {};
  const loop = mapRunResultToLoop(raw, projectId);
  knowledgeActions.setLoop(loop);
  const findingsNote = loop.totalFindings > 0 ? ` · ${loop.totalFindings} finding(s)` : "";
  actions.sendSystemNotice(
    `[project loop] ${loop.simulated ? "simulated" : "umrs"} · ${loop.loopId} ${loop.status} · ${loop.currentIteration}/${loop.maxIterations} iter${findingsNote}`,
  );
  const findings = Array.isArray(raw.findings) ? raw.findings.slice(0, 5) : [];
  for (const f of findings) {
    actions.sendSystemNotice(`[finding] ${findingStatement(f).slice(0, 120)}`);
  }
}

/** Apply run job lifecycle to knowledge loop store + honest notices. */
export function applyRunJobToLoopStore(
  phase: string,
  result: unknown,
  projectId: string | undefined,
): void {
  if (!projectId) return;
  if (phase === "started") {
    knowledgeActions.setLoop(provisionalLoopFromRunStart(projectId));
  } else if (phase === "error" || phase === "cancelled") {
    applyTerminalFailure(phase, projectId);
  } else if (phase === "progress") {
    applyProgress(result, projectId);
  } else if (phase === "done") {
    applyDone(result, projectId);
  }
}
