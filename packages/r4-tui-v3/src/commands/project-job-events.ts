/**
 * Project lifecycle job events from the Companion bridge (shared by slash + runtime bridge).
 */

import { getCompanionBridge } from "../bridge/jsonl-client";
import { actions, workspace, activeTab } from "../stores";
import { refreshProjectKnowledge } from "../hooks/project-refresh";
import {
  applyReadinessAssessment,
  parseReadinessResult,
} from "../lib/readiness-sync";
import { applyRc1JobToWorkflowGraph } from "../lib/rc1-workflow-graph";
import { applyRunJobToLoopStore } from "../lib/loop-run-result";

const activeProjectJobs = new Map<string, string>();
const terminalProjectJobs = new Set<string>();
const wiredProjectBridges = new WeakSet<object>();

export interface ProjectJobEventPayload {
  jobId?: string;
  operation?: string;
  phase?: string;
  message?: string;
  error?: string;
  result?: unknown;
}

/** Snapshot of in-flight project jobs (jobId → operation). */
export function getActiveProjectJobs(): ReadonlyMap<string, string> {
  return activeProjectJobs;
}

/** Register a job started by slash long-ops (unless already terminal). */
export function trackActiveProjectJob(jobId: string, operation: string): void {
  if (terminalProjectJobs.has(jobId)) return;
  activeProjectJobs.set(jobId, operation);
}

function shouldTrackJobPhase(jobId: string, phase: string, operation?: string): boolean {
  if (phase === "started") {
    activeProjectJobs.set(jobId, operation ?? "job");
    return true;
  }
  return !terminalProjectJobs.has(jobId) && activeProjectJobs.has(jobId);
}

function notifyProjectJobPhase(ev: ProjectJobEventPayload, phase: string): void {
  // Progress is high-frequency — update loop store only, no chat spam.
  if (phase === "progress") return;
  const msg = ev.message ?? phase;
  if (phase === "error") {
    actions.sendSystemNotice(`[project ${ev.operation}] ${msg}: ${ev.error ?? ""}`.trim());
  } else {
    actions.sendSystemNotice(`[project ${ev.operation}] ${msg}`);
  }
}

async function fetchAndApplyReadiness(projectId: string): Promise<void> {
  const source = workspace.dataSource;
  if (!source?.readiness) return;
  try {
    const raw = await source.readiness(projectId);
    // The knowledge script exits 0 with {"error": …} on an HTTP failure.
    if (typeof raw?.error === "string") throw new Error(raw.error);
    applyReadinessAssessment(parseReadinessResult(raw), projectId);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    actions.sendErrorCard({
      what: "project readiness RPC failed",
      action: "dropped",
      detail: message,
    });
  }
}

/** The job ran `r4 project readiness <id> --json`; its report is the result.
 * Refetching instead read the gateway's unscoped report and raised "not
 * ready" for a ready project (TUI audit 2026-10-17). The refetch stays for
 * a job that carried no report. */
function applyReadinessJob(result: unknown): void {
  const report = result && typeof result === "object" ? (result as Record<string, unknown>) : null;
  const projectId = typeof report?.project_id === "string" ? report.project_id : activeTab().project?.id;
  if (!projectId) return;
  if (report) applyReadinessAssessment(parseReadinessResult(report), projectId);
  else void fetchAndApplyReadiness(projectId);
}

function finishProjectJob(
  jobId: string,
  phase: string,
  operation?: string,
  result?: unknown,
): void {
  activeProjectJobs.delete(jobId);
  terminalProjectJobs.add(jobId);
  const op = operation ?? "";
  if (phase === "done" && ["import", "init", "run", "plan"].includes(op)) {
    void refreshProjectKnowledge();
  }
  if (phase === "done" && op === "readiness") applyReadinessJob(result);
  if (op === "run") {
    applyRunJobToLoopStore(phase, result, activeTab().project?.id);
  }
}

/** Non-terminal phases of the `run` op mirror onto the loop store as they arrive. */
function applyRunPhase(ev: ProjectJobEventPayload, phase: string): void {
  if (ev.operation !== "run") return;
  if (phase === "started") applyRunJobToLoopStore("started", undefined, activeTab().project?.id);
  else if (phase === "progress") applyRunJobToLoopStore("progress", ev.result, activeTab().project?.id);
}

export function handleProjectJobEvent(raw: unknown): void {
  const ev = raw as ProjectJobEventPayload;
  const phase = ev.phase ?? "";
  const jobId = ev.jobId ?? "";
  if (!jobId) return;
  if (!shouldTrackJobPhase(jobId, phase, ev.operation)) return;
  notifyProjectJobPhase(ev, phase);
  if (ev.operation) {
    applyRc1JobToWorkflowGraph(ev.operation, phase, jobId);
  }
  applyRunPhase(ev, phase);
  if (["done", "error", "cancelled"].includes(phase)) {
    finishProjectJob(jobId, phase, ev.operation, ev.result);
  }
}

export function wireProjectJobEventsOnce(): void {
  bindProjectJobEvents(getCompanionBridge());
}

/** Attach to one bridge (chat-tabs wiring binds each tab's bridge this way;
 *  withExitHandler=false there — tab-lifetime handling owns the exit line). */
export function bindProjectJobEvents(
  bridge: ReturnType<typeof getCompanionBridge>,
  opts?: { withExitHandler?: boolean },
): void {
  if (wiredProjectBridges.has(bridge)) return;
  wiredProjectBridges.add(bridge);
  bridge.on("project_job_event", handleProjectJobEvent);
  if (opts?.withExitHandler === false) return;
  bridge.on("exit", () => {
    for (const [jobId, operation] of activeProjectJobs) {
      actions.sendErrorCard({
        what: `[project ${operation}] job dropped — bridge exited`,
        action: "dropped",
        detail: `job ${jobId}`,
      });
    }
    activeProjectJobs.clear();
    terminalProjectJobs.clear();
    actions.setChatFallback(true, "companion bridge exited");
  });
}

/** Test helper */
export function resetProjectJobEventsForTests(): void {
  activeProjectJobs.clear();
  terminalProjectJobs.clear();
}
