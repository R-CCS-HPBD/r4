/**
 * Poll persisted project knowledge files (manifest / plan) in CLI mode.
 * Read-only — does not run pipeline commands.
 */

import { createEffect, createRoot, on, onCleanup } from "solid-js";
import type { DataSource, ProjectKnowledgePollResult } from "../adapters/types";
import { actions, workspace, activeTab } from "../stores";
import { knowledgeActions } from "../stores/knowledge";
import { agentActions } from "../stores/agent-runtime";
import { scienceActions } from "../stores/science";

let activeKnowledgeStop: (() => void) | null = null;
/** Project the mission-control/science state on screen belongs to. */
let lastAppliedProjectId: string | null = null;

export function stopKnowledgePoller(): void {
  activeKnowledgeStop?.();
  activeKnowledgeStop = null;
}

/** Project-scoped UI that a poll result does not overwrite on its own (the
 * mission-control merge keeps absent fields; the science store is filled by
 * pane reads): cleared the moment the active project changes — /project
 * switch, tab switch, delete (TUI audit r2 #47/#65/#55). */
export function clearProjectScopedState(): void {
  agentActions.updateMissionControl({ researchGoal: "", hypotheses: [], blockers: [] });
  scienceActions.reset();
  actions.setKnowledge([]);
  actions.setTasks([]);
}

/** Test hook: forget which project the on-screen state belongs to. */
export function _resetAppliedProjectForTests(): void {
  lastAppliedProjectId = null;
}

function noteAppliedProject(projectId: string | undefined): void {
  if (!projectId) return;
  if (lastAppliedProjectId !== null && lastAppliedProjectId !== projectId) {
    clearProjectScopedState();
  }
  lastAppliedProjectId = projectId;
}

/** Always apply — including empty — so project switches and removals clear
 * stale UI. `projectId` names the project the result was polled for; a
 * result for another project than the last one first clears that project's
 * mission control / science state. */
export function applyProjectKnowledgePoll(result: ProjectKnowledgePollResult, projectId?: string): void {
  noteAppliedProject(projectId);
  // An unreadable server list (expired token, 503, timeout) keeps the rows
  // on screen and says so: writing [] showed "No project knowledge yet. Add
  // a file…" (review of K1 part B, 2026-10-24).
  knowledgeActions.setListError(result.knowledgeError ?? null);
  if (!result.knowledgeError) actions.setKnowledge(result.knowledge);
  actions.setTasks(result.tasks);
  knowledgeActions.setIngestion(result.ingestion);
  knowledgeActions.setCoverage(result.coverage);
  knowledgeActions.setReadiness(result.readiness);
  knowledgeActions.setPlan(result.plan);
  knowledgeActions.setLoop(result.loop);
  knowledgeActions.setSessions(result.sessions);
  const goal = result.plan?.researchGoal?.trim();
  const hypotheses =
    result.plan?.hypotheses?.map((h) => ({
      id: h.id,
      statement: h.statement,
      confidence: h.confidence,
    })) ?? [];
  const blockers =
    result.readiness?.blockers?.map((description, i) => ({
      id: `poll-blk-${i}`,
      description,
      severity: "warning" as const,
    })) ?? [];

  agentActions.updateMissionControl({
    ...(goal ? { researchGoal: goal } : {}),
    ...(hypotheses.length > 0 ? { hypotheses } : {}),
    ...(blockers.length > 0 ? { blockers } : {}),
  });
  agentActions.refreshMissionControl();
}

export function createKnowledgePoller(source: DataSource, intervalMs = 30_000) {
  stopKnowledgePoller();
  if (!source.pollProjectKnowledge) {
    return { stop: () => {} };
  }

  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlight = false;
  let rerun = false;

  const tick = async () => {
    if (stopped) return;
    if (inFlight) {
      rerun = true; // the project moved while a poll ran — poll again after it
      return;
    }
    const project = activeTab().project;
    if (!project?.id) return;

    inFlight = true;
    knowledgeActions.setLoading(true);
    try {
      const result = await source.pollProjectKnowledge!(project.id, project.name);
      // A result for a project the tab has left is stale: never paint it.
      if (activeTab().project?.id === project.id) applyProjectKnowledgePoll(result, project.id);
      knowledgeActions.setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      knowledgeActions.setError(message);
    } finally {
      knowledgeActions.setLoading(false);
      inFlight = false;
    }
    if (rerun) {
      rerun = false;
      await tick();
    }
  };

  const schedule = () => {
    if (stopped) return;
    timer = setTimeout(() => {
      void tick().finally(schedule);
    }, intervalMs);
  };

  // Project-id watcher (tab switch / project switch): clear the previous
  // project's state now and poll the new one now — not on the next 30 s tick.
  const disposeWatch = createRoot((dispose) => {
    createEffect(
      on(
        () => activeTab().project?.id ?? null,
        (id) => {
          if (lastAppliedProjectId !== null && id !== lastAppliedProjectId) {
            clearProjectScopedState();
            if (!id) lastAppliedProjectId = null;
          }
          void tick();
        },
        { defer: true },
      ),
    );
    return dispose;
  });

  void tick();
  schedule();

  const stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
    disposeWatch();
  };
  activeKnowledgeStop = stop;
  onCleanup(stop);
  return { stop };
}
