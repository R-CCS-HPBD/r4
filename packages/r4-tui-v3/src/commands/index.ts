/**
 * Namespaced command registry.
 */

import { workspace, actions, activeTab } from "../stores";
import type { Space, Command, UiEndpoint, UiJob } from "../types";
import { SPACE_SHORTCUTS } from "../keymap";
import { clearConversation } from "../lib/clear-conversation";

function selectedEndpoint(): UiEndpoint | undefined {
  const id = workspace.selectedObjectId;
  if (id) {
    const selected = workspace.endpoints.find((e) => e.id === id);
    if (selected) return selected;
  }
  return workspace.endpoints.find((e) => e.status !== "stopped");
}

function selectedJob(predicate: (job: UiJob) => boolean): UiJob | undefined {
  const id = workspace.selectedObjectId;
  if (id) {
    const selected = workspace.jobs.find((j) => j.id === id);
    if (selected && predicate(selected)) return selected;
  }
  return workspace.jobs.find(predicate);
}

const SPACE_LABELS: Record<Space, string> = {
  dashboard: "Dashboard",
  research: "Research",
  tasks: "Tasks",
  work: "Work (UMRS)",
  knowledge: "Knowledge",
  infrastructure: "Infrastructure",
  loop: "Workflow builder",
  settings: "Settings",
  sessions: "Sessions",
  codeview: "Code view",
  catalog: "Catalog",
  term: "Terminal",
};

const commandList: Command[] = [
  { id: "ui:command-palette", namespace: "ui", title: "Open command palette", shortcut: "ctrl+p", run: () => actions.openCommandPalette() },
  { id: "ui:slash-bar", namespace: "ui", title: "Open slash command surface", shortcut: "ctrl+. · type /", run: () => actions.openCommandPalette("/") },
  {
    // L3 (2026-09-19 wave): the overlay IS the help surface — palette stays
    // ctrl+p movement-first; a stale "help= palette" pointer would shadow the
    // canonical overlay through the local-run registry path.
    id: "ui:help",
    namespace: "ui",
    title: "Help overlay (F12)",
    slashName: "help",
    slashDescription: "Help overlay — chords + live slash catalog + live keymap",
    run: () => actions.toggleHelp(),
  },
  {
    id: "ui:clear-chat",
    namespace: "ui",
    title: "Clear chat",
    slashName: "clear",
    slashDescription: "Clear the chat and reset the conversation the model sees",
    run: () => void clearConversation(),
  },
  { id: "ui:close-overlay", namespace: "ui", title: "Close overlay", shortcut: "escape", run: () => actions.closeAllOverlays() },
  { id: "ui:quit", namespace: "ui", title: "Quit", shortcut: "ctrl+q", run: () => actions.requestQuit() },
  // S2: "Go to" rows open the detail-pane view (setSpace alone was invisible —
  // the registry looked dead to users after the V4 frame switch).
  ...SPACE_SHORTCUTS.map((space) => ({
    id: `ui:goto-${space}`,
    namespace: "ui",
    title: `Open pane: ${SPACE_LABELS[space]}`,
    run: () => actions.openPane(space),
  })),
  {
    id: "ui:goto-workflows",
    namespace: "ui",
    title: "Open pane: Workflows (UMRS)",
    run: () => actions.openPane("work"),
  },
  {
    // Terminal pane (2026-10-03): interactive PTY shell docked right of chat.
    id: "ui:terminal",
    namespace: "ui",
    title: "Open pane: Terminal (interactive shell)",
    // No key opens the terminal (keymap: click-only focus, /term opens it);
    // the palette hint names the working slash verb instead (round 2).
    shortcut: "/term",
    run: () => actions.openPane("term"),
  },
  {
    // T5 (2026-09-19): keyboard alias path to the Work-pane teams section —
    // /team, /teams and /teamup all route to the Work pane (the Work pane
    // IS the home; routing through this registry means slash.ts itself stays
    // untouched and free of the changed-file sweep).

    id: "ui:team-pane",
    namespace: "ui",
    title: "Team pane (multi-agent sessions)",
    slashName: "team",
    slashDescription: "Multi-agent teamup sessions list & verbs (Work pane → teams)",
    run: () => actions.openPane("work"),
  },
  {
    id: "research:tab-ops",
    namespace: "research",
    title: "Research tab: Ops (Timeline / Approvals / Workflow)",
    shortcut: "alt+shift+1",
    run: () => {
      actions.setSpace("research");
      actions.setResearchBottomTab("ops");
    },
  },
  {
    id: "research:tab-strategy",
    namespace: "research",
    title: "Research tab: Strategy (Strategy / Blackboard / Loop)",
    shortcut: "alt+shift+2",
    run: () => {
      actions.setSpace("research");
      actions.setResearchBottomTab("strategy");
    },
  },
  {
    id: "research:tab-explain",
    namespace: "research",
    title: "Research tab: Explainability",
    shortcut: "alt+shift+3",
    run: () => {
      actions.setSpace("research");
      actions.setResearchBottomTab("explain");
    },
  },
  {
    id: "research:goto-knowledge",
    namespace: "research",
    title: "Set research goal (Knowledge space)",
    run: () => {
      actions.setSpace("knowledge");
      actions.notify("info", "Research goal", "Use Knowledge space to query and verify project context.");
    },
  },
  {
    id: "infrastructure:provision-endpoint",
    namespace: "infrastructure",
    title: "Provision model endpoint",
    run: () => {
      const model = activeTab().currentModel ?? workspace.models[0]?.name;
      if (!model) {
        actions.notify("warning", "Provision endpoint", "No model available to provision.");
        return;
      }
      void actions.provisionEndpoint(model);
    },
  },
  {
    id: "infrastructure:stop-endpoint",
    namespace: "infrastructure",
    title: "Stop endpoint (selected or first active)",
    run: () => {
      const ep = selectedEndpoint();
      if (!ep || ep.status === "stopped") {
        actions.notify("warning", "Stop endpoint", "No active endpoint to stop.");
        return;
      }
      void actions.stopEndpoint(ep.id);
    },
  },
  {
    id: "infrastructure:cancel-job",
    namespace: "infrastructure",
    title: "Cancel job (selected or first in-flight)",
    run: () => {
      const job = selectedJob((j) => j.status === "running" || j.status === "queued");
      if (!job) {
        actions.notify("warning", "Cancel job", "No in-flight job to cancel.");
        return;
      }
      void actions.cancelJob(job.id);
    },
  },
  {
    id: "infrastructure:retry-job",
    namespace: "infrastructure",
    title: "Retry job (selected or first failed)",
    run: () => {
      const job = selectedJob((j) => j.status === "failed");
      if (!job) {
        actions.notify("warning", "Retry job", "No failed job to retry.");
        return;
      }
      void actions.retryJob(job.id);
    },
  },
  {
    id: "infrastructure:job-logs",
    namespace: "infrastructure",
    title: "Show job logs (selected or first job)",
    run: () => {
      const id = workspace.selectedObjectId;
      const job =
        (id ? workspace.jobs.find((j) => j.id === id) : undefined) ?? workspace.jobs[0];
      if (!job) {
        actions.notify("warning", "Job logs", "No job available.");
        return;
      }
      void actions.showJobLogs(job.id);
    },
  },
];

const index = new Map(commandList.map((c) => [c.id, c]));

export function getCommands(): Command[] {
  return commandList;
}

export function getCommand(id: string): Command | undefined {
  return index.get(id);
}

export type CommandRunResult =
  | { kind: "ok" }
  | { kind: "error"; message: string };

export async function runCommand(id: string): Promise<CommandRunResult> {
  const command = index.get(id);
  if (!command) {
    return { kind: "error", message: `Command not found: ${id}` };
  }

  try {
    await command.run();
    return { kind: "ok" };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { kind: "error", message: `Command failed: ${command.title} — ${detail}` };
  }
}
