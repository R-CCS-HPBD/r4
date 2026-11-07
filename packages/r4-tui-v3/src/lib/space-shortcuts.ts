/**
 * Space-scoped letter shortcuts shared by route handlers and the global keymap.
 * Letter keys must not fire while the user is typing in chat or an overlay is open.
 */

import { workspace, actions, activeTab } from "../stores";
import { keyClaimants, keyClaimed } from "../keymap/key-claims";
import "../keymap/store-claims";
import { agentRuntime } from "../stores/agent-runtime";
import { getActiveProjectJobs } from "../commands/project-job-events";
import { PUBLIC_ENDPOINT_STATE, endpointLabel } from "./endpoint-label";
import type { UiEndpoint } from "../types";

export type InfraShortcutKey = "s" | "c" | "r" | "l";

/** True when letter shortcuts should be ignored (typing / overlays).
 * Plan H-6 (2026-10-18): one question to the claims registry. The
 * `letters` claimants are the store layers in keymap/store-claims.ts
 * (modal overlays, pickers, the splash, a focused verify input, the
 * composer's draft while the composer owns the keys, r2 #67) and the
 * composer's Ctrl+R search (its query never lands in chatDraft; r2 #9). */
export function letterShortcutsBlocked(): boolean {
  return keyClaimed("letters");
}

/** The composer's draft holds the letters (not a focused pane). */
function draftHoldsKeys(): boolean {
  return keyClaimants("letters").includes("composer-draft");
}

export type InfraSelectionKind = "endpoint" | "job" | "none";

export function getInfraSelection(): {
  kind: InfraSelectionKind;
  endpoint?: (typeof workspace.endpoints)[number];
  job?: (typeof workspace.jobs)[number];
} {
  const id = workspace.selectedObjectId;
  if (!id) return { kind: "none" };
  const endpoint = workspace.endpoints.find((e) => e.id === id);
  if (endpoint) return { kind: "endpoint", endpoint };
  const job = workspace.jobs.find((j) => j.id === id);
  if (job) return { kind: "job", job };
  return { kind: "none" };
}

function endpointActionsLine(ep: UiEndpoint): string {
  // A configured public source is not a provisioned endpoint: nothing to
  // stop, and its models are picked with /models (wave 82).
  if (ep.isPublic) return `Public source · ${endpointLabel(ep)} · ${PUBLIC_ENDPOINT_STATE} · /models picks its models`;
  const stop = ep.status !== "stopped" ? "[s] stop" : "s (already stopped)";
  return `Endpoint · ${ep.status} · ${stop} · Tab → Jobs for cancel/retry/logs`;
}

/** One-line hint of keys that apply to the current Infra selection. */
export function infraAvailableActionsLine(): string {
  const sel = getInfraSelection();
  if (sel.kind === "endpoint" && sel.endpoint) return endpointActionsLine(sel.endpoint);
  if (sel.kind === "job" && sel.job) {
    const job = sel.job;
    const parts: string[] = ["[l] details"];
    if (job.status === "running" || job.status === "queued") parts.unshift("[c] cancel");
    if (job.status === "failed") parts.unshift("[r] retry");
    return `Job · ${job.status} · ${parts.join(" · ")}`;
  }
  return "Select an endpoint or job (Tab / ↑↓) · then s/c/r/l";
}

function infraFeedback(message: string) {
  actions.setSpaceFeedback(message);
  actions.sendSystemNotice(message);
  actions.appendTerminalLine(message);
}

type InfraSelection = ReturnType<typeof getInfraSelection>;

function infraBlockedFeedback() {
  const why =
    draftHoldsKeys()
      ? "Letter shortcuts blocked — clear the Dashboard chat draft (or Esc), then retry"
      : "Letter shortcuts blocked — close overlays / slash bar, then retry";
  infraFeedback(why);
}

function infraStopEndpoint(sel: InfraSelection): boolean {
  if (sel.kind !== "endpoint" || !sel.endpoint) {
    infraFeedback("Stop needs an endpoint — Tab to Endpoints, ↑↓ select, then s");
    return true;
  }
  if (sel.endpoint.isPublic) {
    infraFeedback(`Nothing to stop — ${endpointLabel(sel.endpoint)} is a configured public source`);
    return true;
  }
  if (sel.endpoint.status === "stopped") {
    infraFeedback(`Endpoint already stopped (${sel.endpoint.id.slice(0, 8)})`);
    return true;
  }
  infraFeedback(`Stopping endpoint ${sel.endpoint.id.slice(0, 8)}…`);
  void actions.stopEndpoint(sel.endpoint.id);
  return true;
}

function infraCancelJob(sel: InfraSelection): boolean {
  if (sel.kind !== "job" || !sel.job) {
    infraFeedback("Cancel needs a job — Tab to Jobs, ↑↓ select, then c");
    return true;
  }
  if (sel.job.status !== "running" && sel.job.status !== "queued") {
    infraFeedback(`Cannot cancel job in status “${sel.job.status}” (need running/queued)`);
    return true;
  }
  infraFeedback(`Cancelling job ${sel.job.id.slice(0, 8)}…`);
  void actions.cancelJob(sel.job.id);
  return true;
}

function infraRetryJob(sel: InfraSelection): boolean {
  if (sel.kind !== "job" || !sel.job) {
    infraFeedback("Retry needs a failed job — Tab to Jobs, ↑↓ select, then r");
    return true;
  }
  if (sel.job.status !== "failed") {
    infraFeedback(`Cannot retry job in status “${sel.job.status}” (need failed)`);
    return true;
  }
  infraFeedback(`Retrying job ${sel.job.id.slice(0, 8)}…`);
  void actions.retryJob(sel.job.id);
  return true;
}

function infraJobLogs(sel: InfraSelection): boolean {
  if (sel.kind !== "job" || !sel.job) {
    infraFeedback("Logs need a job — Tab to Jobs, ↑↓ select, then l");
    return true;
  }
  infraFeedback(`Loading job details for ${sel.job.id.slice(0, 8)}…`);
  void actions.showJobLogs(sel.job.id);
  return true;
}

/**
 * Infra letter shortcuts. Always consumes the key on this space and explains
 * when the action does not apply (no more silent no-ops).
 */
export function handleInfrastructureShortcut(key: InfraShortcutKey): boolean {
  if (workspace.paneView !== "infrastructure") return false;
  if (letterShortcutsBlocked()) {
    infraBlockedFeedback();
    return true;
  }

  const sel = getInfraSelection();
  switch (key) {
    case "s":
      return infraStopEndpoint(sel);
    case "c":
      return infraCancelJob(sel);
    case "r":
      return infraRetryJob(sel);
    case "l":
      return infraJobLogs(sel);
  }
  return false;
}

export function handleResearchRunDemo(): boolean {
  if (workspace.paneView !== "research") return false;
  if (letterShortcutsBlocked()) return false;
  const id = activeTab().project?.id;
  if (!id) {
    actions.sendSystemNotice("No project selected — /project switch <id> first");
    return true;
  }
  void actions.dispatchSlashFromChat(
    `/project run ${id} --simulate --approve --max-iterations 2`,
  );
  return true;
}

/** Real mode — requires durable workflow port; fails with structured error if missing. */
export function handleResearchRunReal(): boolean {
  if (workspace.paneView !== "research") return false;
  if (letterShortcutsBlocked()) return false;
  const id = activeTab().project?.id;
  if (!id) {
    actions.sendSystemNotice("No project selected — /project switch <id> first");
    return true;
  }
  actions.sendSystemNotice(
    "[project run] real mode — needs Gateway durable workflow port; use Run demo if unavailable",
  );
  void actions.dispatchSlashFromChat(
    `/project run ${id} --real --approve --max-iterations 2`,
  );
  return true;
}

export function handleResearchCancelJob(): boolean {
  if (workspace.paneView !== "research") return false;
  if (letterShortcutsBlocked()) return false;
  const jobs = [...getActiveProjectJobs().entries()];
  const first = jobs[0];
  if (!first) {
    actions.sendSystemNotice("No active project job to cancel");
    return true;
  }
  void actions.dispatchSlashFromChat(`/project cancel ${first[0]}`);
  return true;
}

/** Open numbered Start actions menu (WSL / no-mouse). */
export function handleResearchStartMenu(): boolean {
  if (workspace.paneView !== "research") return false;
  if (letterShortcutsBlocked()) return false;
  actions.openStartActionsMenu();
  return true;
}

export type TaskShortcutKey = "enter" | "a" | "r" | "k";

/** Draft chat about a plan task, then open Dashboard chat. */
export function askAboutSelectedTask(): boolean {
  const id = workspace.selectedObjectId;
  const task = id ? workspace.tasks.find((t) => t.id === id) : undefined;
  if (!task) {
    actions.sendSystemNotice("Select a task first (↑↓)");
    return false;
  }
  const draft = [
    `Help with this research task: ${task.title}`,
    `(status: ${task.status}; goal: ${task.goalId})`,
    "Suggest next steps or how to advance it via /project run.",
  ].join(" ");
  actions.setPendingChatDraft(draft);
  actions.setSpace("dashboard");
  if (!workspace.contextPanelOpen) actions.toggleContextPanel();
  return true;
}

/** Research Project graph: Enter asks; k opens Knowledge. */
export type ResearchGraphKey = "enter" | "k";

/** Draft chat about a goal/hypothesis/knowledge row; null when id matches none. */
function researchObjectDraft(id: string): string | null {
  const goal = workspace.goals.find((g) => g.id === id);
  if (goal) {
    return [
      `Help with this research goal: ${goal.statement}`,
      `(status: ${goal.status})`,
      "Suggest how to advance via /project plan or /project run.",
    ].join(" ");
  }

  // Hypotheses live on missionControl.
  const hyp = agentRuntime.missionControl.hypotheses.find((h) => h.id === id);
  if (hyp) {
    return [
      `Help evaluate this hypothesis: ${hyp.statement}`,
      `(confidence: ${Math.round(hyp.confidence * 100)}%)`,
      "Suggest experiments or next /project run steps.",
    ].join(" ");
  }

  const knowledge = workspace.knowledge.find((k) => k.id === id);
  if (knowledge) {
    return [
      `Help with this local project file: ${knowledge.name} (status: ${knowledge.status}).`,
      "Prefer knowledge_show or r4 project verify. lab_catalog_search is the Gateway catalog — if it is empty, do not retry.",
    ].join(" ");
  }

  return null;
}

/** Push a draft into Dashboard chat and make sure the panel is visible. */
function sendResearchDraft(draft: string): boolean {
  actions.setPendingChatDraft(draft);
  actions.setSpace("dashboard");
  if (!workspace.contextPanelOpen) actions.toggleContextPanel();
  return true;
}

export function askAboutSelectedResearchObject(): boolean {
  const id = workspace.selectedObjectId;
  if (!id || id.startsWith("wf:") || id.startsWith("agent:")) {
    actions.sendSystemNotice("Select a graph row first (↑↓)");
    return false;
  }

  if (workspace.tasks.some((t) => t.id === id)) return askAboutSelectedTask();

  const draft = researchObjectDraft(id);
  if (draft) return sendResearchDraft(draft);

  actions.sendSystemNotice("Select a graph task/goal/hypothesis first (↑↓)");
  return false;
}

export function handleResearchGraphShortcut(key: ResearchGraphKey): boolean {
  if (workspace.paneView !== "research") return false;
  if (letterShortcutsBlocked()) return false;
  // Only when graph pane is focused (Tab cycle) — avoid stealing Enter from verify/approvals.
  const focus = workspace.researchFocus ?? "graph";
  if (focus !== "graph") return false;

  if (key === "enter") {
    return askAboutSelectedResearchObject();
  }
  if (key === "k") {
    if (!workspace.selectedObjectId) {
      actions.sendSystemNotice("Select a graph row first (↑↓)");
      return false;
    }
    actions.setSpace("knowledge");
    return true;
  }
  return false;
}

/**
 * Tasks space: plan-task monitor + handoffs.
 * Enter/a → ask in chat · r → Research · k → Knowledge
 */
export function handleTaskShortcut(key: TaskShortcutKey): boolean {
  if (workspace.paneView !== "tasks") return false;
  if (letterShortcutsBlocked()) return false;

  if (key === "enter" || key === "a") {
    return askAboutSelectedTask();
  }
  if (key === "r") {
    actions.setSpace("research");
    return true;
  }
  if (key === "k") {
    actions.setSpace("knowledge");
    return true;
  }
  return false;
}
