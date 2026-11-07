/**
 * Settings (F7) preferences (issue #217, 2026-10-27): the preferences that
 * already exist elsewhere (slash verbs, pickers, config files) listed with
 * their current values, so the pane is a preference surface and not only a
 * status readout. Nothing here adds persistence: each row says where its
 * value lives today ("this session", "saved", "saved per project").
 *
 * `preferenceRows()` is a pure read of the stores; `activatePreference()`
 * toggles the two session switches in place and hands every other row to
 * the surface that already owns it (the /theme and /models pickers,
 * /settings matrix, /settings rules, /statusline).
 */

import { actions, activeTab, workspace } from "../stores";
import { currentThemeName } from "../themes";
import { modeBadge } from "./mode-badge";
import { readStatuslineCommand } from "./statusline";
import { truncate } from "./text";

export type PreferenceKey =
  | "bell"
  | "ambient"
  | "theme"
  | "model"
  | "mode"
  | "rules"
  | "statusline";

/** Where the value lives: memory for this session, a config file, or a
 * per-project config file. */
export type PreferenceScope = "session" | "saved" | "project";

export interface PreferenceRow {
  key: PreferenceKey;
  label: string;
  value: string;
  scope: PreferenceScope;
  /** What Enter (or a click) does, shown under the list for the cursor row. */
  hint: string;
}

/** Inputs the rows read; a parameter so tests can pin any state. */
export interface PreferenceSnapshot {
  bell: boolean;
  ambient: boolean;
  theme: string;
  model: string | null;
  trustSource: string;
  interactionMode: string;
  projectId: string;
  statusline: string;
}

export const PREFERENCE_VALUE_MAX = 24;

const SCOPE_LABEL: Record<PreferenceScope, string> = {
  session: "this session",
  saved: "saved",
  project: "saved per project",
};

const onOff = (value: boolean): string => (value ? "on" : "off");

/** Read the current values from the stores and the config files. */
export function preferenceSnapshot(): PreferenceSnapshot {
  const tab = activeTab();
  const projectId = tab.project?.id ?? "default";
  return {
    bell: workspace.completionBellEnabled,
    ambient: workspace.ambientCardsEnabled,
    theme: currentThemeName(),
    model: tab.currentModel ?? null,
    trustSource: tab.trustSource,
    interactionMode: tab.interactionMode,
    projectId,
    statusline: readStatuslineCommand(),
  };
}

/** The Preferences rows, in display order. */
export function preferenceRows(snap: PreferenceSnapshot = preferenceSnapshot()): PreferenceRow[] {
  // ADR-104: one mode row (the permission is derived from it, never set);
  // rules are the companion's, listed on Enter (no file read here).
  const mode = modeBadge(snap.interactionMode, snap.trustSource).replace(/^mode:/, "");
  return [
    { key: "bell", label: "Completion bell", value: onOff(snap.bell), scope: "session", hint: "Enter toggles (same as /settings bell on|off)" },
    { key: "ambient", label: "Ambient event cards", value: onOff(snap.ambient), scope: "session", hint: "Enter toggles (same as /settings ambient_cards on|off)" },
    { key: "theme", label: "Theme", value: snap.theme, scope: "saved", hint: "Enter opens the /theme picker (live preview)" },
    { key: "model", label: "Model", value: snap.model ?? "auto", scope: "session", hint: "Enter opens the /models picker; /models default <name> saves" },
    { key: "mode", label: "Mode", value: mode, scope: "session", hint: "Shift+Tab or /mode switches; Enter shows /settings matrix (what each mode runs)" },
    { key: "rules", label: "Approval rules", value: snap.projectId, scope: "project", hint: "Enter lists /settings rules from the companion (rm <n> · clear)" },
    { key: "statusline", label: "Status line", value: snap.statusline || "off", scope: "saved", hint: "Enter shows /statusline; /statusline <cmd>|off sets it" },
  ];
}

/** One display line: `Label: value (scope)`, the value clipped. */
export function preferenceRowText(row: PreferenceRow): string {
  return `${row.label}: ${truncate(row.value, PREFERENCE_VALUE_MAX)} (${SCOPE_LABEL[row.scope]})`;
}

/** Rows that open an existing slash surface instead of toggling. */
const PREFERENCE_SLASH: Partial<Record<PreferenceKey, string>> = {
  theme: "/theme",
  model: "/models",
  mode: "/settings matrix",
  rules: "/settings rules",
  statusline: "/statusline",
};

function toggleSessionSwitch(key: "bell" | "ambient"): void {
  if (key === "bell") {
    const next = !workspace.completionBellEnabled;
    actions.setCompletionBellEnabled(next);
    actions.sendSystemNotice(`completion bell: ${next ? "ON" : "OFF"} (this session)`);
    return;
  }
  const next = !workspace.ambientCardsEnabled;
  actions.setAmbientCardsEnabled(next);
  actions.sendSystemNotice(`ambient event cards: ${next ? "ON" : "OFF"} (this session)`);
}

/** Open a slash surface from the pane. dispatchSlashFromChat clears the
 * composer (it assumes the line came from there); a half-typed draft is put
 * back, as a Tab into the pane keeps it (TUI audit r2 #67). */
async function openSurface(line: string): Promise<void> {
  const draft = activeTab().chatDraft;
  await actions.dispatchSlashFromChat(line);
  if (draft) actions.setPendingChatDraft(draft);
}

/** Act on a row: toggle a session switch, or open the surface that owns it. */
export async function activatePreference(key: PreferenceKey): Promise<void> {
  if (key === "bell" || key === "ambient") {
    toggleSessionSwitch(key);
    return;
  }
  const line = PREFERENCE_SLASH[key];
  if (line) await openSurface(line);
}

/** The Settings pane's keyboard panels, in Tab order. */
export type SettingsPanelId = "prefs" | "skills" | "mcp";

const PANEL_ORDER: readonly SettingsPanelId[] = ["prefs", "skills", "mcp"];

/** Tab: the next panel, wrapping. */
export function nextSettingsPanel(current: SettingsPanelId): SettingsPanelId {
  const at = PANEL_ORDER.indexOf(current);
  return PANEL_ORDER[(at + 1) % PANEL_ORDER.length]!;
}
