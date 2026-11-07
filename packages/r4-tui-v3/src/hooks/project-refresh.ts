/**
 * Refresh Bun workspace after project create/switch (config change)
 * or after long lifecycle ops (knowledge only).
 */

import { actions, workspace, activeTab } from "../stores";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { loadWorkspace } from "./useBackend";
import { createKnowledgePoller, stopKnowledgePoller } from "./useKnowledge";

/** After create/switch: reload workspace + restart knowledge poller. */
export async function refreshAfterProjectConfigChange(): Promise<void> {
  const source = workspace.dataSource;
  // Per-tab projects (2026-10-01): the ACTIVE TAB's bridge is the authority
  // on "current project" — its selection is process-local. The legacy path
  // (loadWorkspace → `r4 project list --json` in the TUI process) resolves
  // the GLOBAL CLI default and would clobber this tab's project with
  // another tab's (or the terminal's) selection.
  // Only an UNREACHABLE bridge falls back to the CLI snapshot: a bridge that
  // answered "no project" (e.g. after deleting this tab's project) is the
  // truth — the CLI fallback would pick projects.json's first entry
  // (pickActiveProject) for this tab (TUI audit r2 #48).
  const outcome = await applyProjectFromActiveBridge();
  if (outcome === "none") actions.setProject(null);
  if (outcome === "unavailable" && source) await loadWorkspace(source);
  stopKnowledgePoller();
  if (!workspace.demoMode && source && source.name !== "offline") {
    createKnowledgePoller(source, 30_000);
  }
}

/** Set activeTab().project from the active tab bridge's project_current:
 * "applied", "none" (the bridge answered with no project) or "unavailable"
 * (the request failed). */
async function applyProjectFromActiveBridge(): Promise<"applied" | "none" | "unavailable"> {
  try {
    const result = (await getCompanionBridge().request("project_current", {}, 15_000)) as {
      current?: string | null;
      project?: { project_id?: string; name?: string | null } | null;
    };
    const id = result.project?.project_id ?? result.current ?? "";
    if (!id) return "none";
    actions.setProject({
      id,
      name: result.project?.name ?? id,
      description: "",
      status: "active",
      goalIds: [],
    });
    return "applied";
  } catch {
    return "unavailable"; // caller falls back to the CLI snapshot
  }
}

/** After import/init/run: refresh knowledge for current project (no config change). */
export async function refreshProjectKnowledge(): Promise<void> {
  const source = workspace.dataSource;
  if (!source?.pollProjectKnowledge) return;
  const project = activeTab().project;
  if (!project?.id) return;
  try {
    const result = await source.pollProjectKnowledge(project.id, project.name);
    if (activeTab().project?.id !== project.id) return; // the tab moved on: stale
    const { applyProjectKnowledgePoll } = await import("./useKnowledge");
    applyProjectKnowledgePoll(result, project.id);
  } catch {
    /* best-effort */
  }
}

/** Delays (ms) of the follow-up refreshes after a save or an upload: the
 * item lists once the UMRS ingest job has stored it, usually seconds later. */
let knowledgeRefreshDelaysMs: readonly number[] = [4_000, 15_000];

/** Test hook: the follow-up delays (tests pass [] so no timer outlives them). */
export function setKnowledgeRefreshDelaysForTests(delays: readonly number[]): void {
  knowledgeRefreshDelaysMs = delays;
}

/** After `/save` or `/rag upload` (review of K1 part B, 2026-10-24): refresh
 * the Knowledge pane now and as the ingest job lands, instead of waiting for
 * the 30 s poll. Timers never hold the process open. */
export function scheduleKnowledgeRefresh(delays: readonly number[] = knowledgeRefreshDelaysMs): void {
  void refreshProjectKnowledge();
  for (const delay of delays) {
    const timer = setTimeout(() => void refreshProjectKnowledge(), delay);
    (timer as { unref?: () => void }).unref?.();
  }
}
