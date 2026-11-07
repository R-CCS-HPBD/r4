/**
 * Activity Console — diagnostic tray (logs, tools, events, terminal, notifications).
 * Chat stays in the Dashboard pane; this tray does not mirror it.
 */

import type { ActivityTab } from "../types";
import { workspace, actions } from "../stores";

export const ACTIVITY_TABS: readonly ActivityTab[] = [
  "agent-logs",
  "tool-calls",
  "events",
  "terminal",
  "notifications",
  "todos",
] as const;

export const ACTIVITY_TAB_LABELS: Record<ActivityTab, string> = {
  "agent-logs": "Agent",
  "tool-calls": "Tools",
  events: "Events",
  terminal: "Term",
  notifications: "Notifs",
  todos: "Plan",
};

/**
 * Toggle Activity tray.
 * Avoid host-stolen chords:
 * - ctrl+shift+a → Windows Terminal Select All
 * - ctrl+` → Cursor/VS Code “Toggle Terminal”
 */
export const ACTIVITY_TOGGLE_KEYS = ["f9", "ctrl+shift+y"] as const;

/** Digits jump while the tray is open (idle-gated so chat can still type numbers). */
export const ACTIVITY_TAB_DIGIT_KEYS = ACTIVITY_TABS.map((_, i) => String(i + 1));

/** Cycle tabs — modifier chords that work cross-platform. */
export const ACTIVITY_TAB_CYCLE_KEYS = {
  prev: ["ctrl+pageup", "ctrl+shift+["] as const,
  next: ["ctrl+pagedown", "ctrl+shift+]"] as const,
};

export function ensureActivityConsoleOpen(): void {
  if (!workspace.activityOpen) actions.toggleActivityConsole();
}

export function cycleActivityTab(delta: -1 | 1): boolean {
  ensureActivityConsoleOpen();
  const tabs = ACTIVITY_TABS;
  const idx = tabs.indexOf(workspace.activityTab);
  const cur = idx >= 0 ? idx : 0;
  const next = (cur + delta + tabs.length) % tabs.length;
  actions.setActivityTab(tabs[next]!);
  return true;
}

export function gotoActivityTab(tab: ActivityTab): boolean {
  ensureActivityConsoleOpen();
  actions.setActivityTab(tab);
  return true;
}

export function gotoActivityTabByIndex(oneBased: number): boolean {
  const tab = ACTIVITY_TABS[oneBased - 1];
  if (!tab) return false;
  return gotoActivityTab(tab);
}

/**
 * The Activity tray renders ONLY as the /logs pane view (layout/detail-pane):
 * the tray keys (F9 / Ctrl+Shift+Y, Ctrl+T to-dos, the tab cycle chords)
 * show THAT pane (stab-focus, 2026-10-21). They used to flip the bare
 * activityOpen flag, which draws nothing — the keys looked dead.
 */
export function activityTrayVisible(): boolean {
  return workspace.paneView === "logs";
}

/** Show the tray (the /logs pane), optionally on `tab`. The composer keeps
 * the keys: the pane opens unfocused. */
export function showActivityTray(tab?: ActivityTab): boolean {
  if (!activityTrayVisible()) actions.openPane("logs");
  if (tab) return gotoActivityTab(tab);
  ensureActivityConsoleOpen();
  return true;
}

/** Toggle the tray: a second press on the tab it shows (or a bare toggle)
 * closes it; otherwise it opens (or switches) to `tab`. */
export function toggleActivityTray(tab?: ActivityTab): boolean {
  if (activityTrayVisible() && (!tab || workspace.activityTab === tab)) {
    actions.closePane();
    if (workspace.activityOpen) actions.toggleActivityConsole();
    return true;
  }
  return showActivityTray(tab);
}
