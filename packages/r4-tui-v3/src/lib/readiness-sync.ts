/**
 * Map readiness RPC / assessment into knowledge store + approval gates.
 * Accepts both camelCase (CLI) and snake_case / Companion (can_run, readiness_level).
 */

import { knowledgeActions } from "../stores/knowledge";
import { agentActions, agentRuntime } from "../stores/agent-runtime";
import type { ReadinessAssessment } from "../types";

const READINESS_OBJECT_PREFIX = "readiness:";

/** ReadinessLevel values (umrs knowledge/readiness.py; `r4 project
 * readiness --json` and the gateway report) → the display scale, and
 * whether that level lets a run proceed ("partially_ready" = gaps, can
 * proceed). They used to read as level "none", never ready (TUI audit
 * 2026-10-17). */
const READINESS_LEVELS: Record<string, { level: ReadinessAssessment["readinessLevel"]; ready: boolean }> = {
  ready: { level: "high", ready: true },
  partially_ready: { level: "medium", ready: true },
  not_ready: { level: "low", ready: false },
  blocked: { level: "none", ready: false },
};

const DISPLAY_LEVELS = new Set(["low", "medium", "high", "none"]);

function asLevel(raw: unknown): ReadinessAssessment["readinessLevel"] {
  const level = String(raw ?? "none").toLowerCase();
  if (DISPLAY_LEVELS.has(level)) return level as ReadinessAssessment["readinessLevel"];
  return READINESS_LEVELS[level]?.level ?? "none";
}

/** Payloads without can_run: `ready`/`ok`, else the readiness level. */
function readyWithoutCanRun(raw: Record<string, unknown>, rawLevel: unknown): boolean {
  const explicit = raw.ready ?? raw.ok;
  if (explicit !== undefined) return Boolean(explicit);
  const known = READINESS_LEVELS[String(rawLevel).toLowerCase()];
  return known ? known.ready : false;
}

export function parseReadinessResult(raw: Record<string, unknown>): ReadinessAssessment {
  const checksRaw = Array.isArray(raw.readinessChecks)
    ? raw.readinessChecks
    : Array.isArray(raw.readiness_checks)
      ? raw.readiness_checks
      : [];
  const readinessChecks = checksRaw.map((c) => {
    const row = (c ?? {}) as Record<string, unknown>;
    return {
      check: String(row.check ?? row.name ?? "check"),
      passed: Boolean(row.passed ?? row.ok),
      details: String(row.details ?? row.message ?? ""),
    };
  });
  const blockersRaw = Array.isArray(raw.blockers) ? raw.blockers : [];
  const blockers = blockersRaw.map((b) => String(b));
  const rawLevel = raw.readinessLevel ?? raw.readiness_level;
  const readinessLevel = asLevel(rawLevel);

  const canRun =
    raw.can_run !== undefined
      ? Boolean(raw.can_run)
      : raw.canRun !== undefined
        ? Boolean(raw.canRun)
        : undefined;
  const userApproved =
    raw.user_approved !== undefined
      ? Boolean(raw.user_approved)
      : raw.userApproved !== undefined
        ? Boolean(raw.userApproved)
        : undefined;

  // Companion uses can_run; older payloads use ready; the CLI only a level.
  const ready = canRun !== undefined ? canRun : readyWithoutCanRun(raw, rawLevel);

  return {
    ready,
    canRun: canRun ?? ready,
    userApproved,
    approvalStale: raw.approval_stale === true,
    readinessLevel,
    readinessChecks,
    blockers,
  };
}

/**
 * The approval line of a readiness gate. It names the real command: the
 * per-project approval is recorded by `r4 project approve` (UMRS
 * readiness/approve); "--approve" only flags one loop run. A lapsed
 * approval (readiness fell below the approved threshold) says so.
 */
export function readinessApprovalHint(assessment: ReadinessAssessment): string {
  if (assessment.approvalStale) {
    return " Approval lapsed (readiness fell below the approved threshold); re-run `r4 project approve`.";
  }
  if (assessment.userApproved === false) return " Not user-approved yet: run `r4 project approve`.";
  return " Approve with `r4 project approve`.";
}

/** Sync assessment into store and ensure pending gates for blockers / not-ready. */
export function applyReadinessAssessment(assessment: ReadinessAssessment, projectId: string): void {
  knowledgeActions.setReadiness(assessment);

  const canProceed = assessment.canRun ?? assessment.ready;
  const blockers =
    assessment.blockers.length > 0
      ? assessment.blockers
      : canProceed
        ? []
        : assessment.readinessChecks.filter((c) => !c.passed).map((c) => c.check || c.details);

  if (canProceed && blockers.length === 0) {
    for (const gate of agentRuntime.approvalGates) {
      if (
        gate.status === "pending" &&
        gate.objectId?.startsWith(READINESS_OBJECT_PREFIX)
      ) {
        agentActions.resolveApproval(gate.id, "approved", "readiness cleared");
      }
    }
    agentActions.refreshMissionControl();
    return;
  }

  const existingTitles = new Set(
    agentRuntime.approvalGates
      .filter((g) => g.status === "pending" && g.objectId?.startsWith(READINESS_OBJECT_PREFIX))
      .map((g) => g.title),
  );

  const approvedHint = readinessApprovalHint(assessment);

  if (blockers.length === 0 && !canProceed) {
    const title = `Readiness: project ${projectId} not ready`;
    if (!existingTitles.has(title)) {
      agentActions.requestApproval(
        "experiment_execution",
        title,
        `Level ${assessment.readinessLevel}.${approvedHint}`,
        undefined,
        `${READINESS_OBJECT_PREFIX}${projectId}`,
      );
    }
  } else {
    for (const blocker of blockers.slice(0, 5)) {
      const title = `Readiness: ${blocker.slice(0, 48)}`;
      if (existingTitles.has(title)) continue;
      agentActions.requestApproval(
        "experiment_execution",
        title,
        `Blocker for ${projectId}.${approvedHint}`,
        undefined,
        `${READINESS_OBJECT_PREFIX}${projectId}:${blocker.slice(0, 24)}`,
      );
    }
  }

  agentActions.updateMissionControl({
    blockers: blockers.slice(0, 8).map((description, i) => ({
      id: `blk-${i}`,
      description,
      severity: "warning" as const,
    })),
  });
  agentActions.refreshMissionControl();
}

export function isReadinessGate(objectId: string | undefined): boolean {
  return Boolean(objectId?.startsWith(READINESS_OBJECT_PREFIX));
}

export function projectIdFromReadinessGate(objectId: string | undefined): string | null {
  if (!objectId?.startsWith(READINESS_OBJECT_PREFIX)) return null;
  const rest = objectId.slice(READINESS_OBJECT_PREFIX.length);
  const colon = rest.indexOf(":");
  return colon >= 0 ? rest.slice(0, colon) : rest || null;
}
