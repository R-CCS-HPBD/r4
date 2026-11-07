/**
 * /theme — color theme switcher.
 *
 * Bare `/theme` opens the interactive picker; `/theme <name>` applies directly
 * (aliases ok, e.g. `tokyo`); `/theme list` prints names inline. Applied theme
 * persists at <configRoot>/theme.json and remounts the UI via themeEpoch.
 */

import { actions } from "../stores";
import { applyTheme, currentThemeName, listThemes } from "../themes";
import { takeThemePreviewOrigin, themePicker } from "./slash-pickers";
import type { SlashDispatchResult } from "./slash";

export function handleThemeCommand(args: string): SlashDispatchResult {
  const q = args.trim();
  if (!q) {
    actions.openSlashPicker(themePicker());
    return { kind: "handled" };
  }
  if (q === "list" || q === "ls") {
    const names = listThemes()
      .map((t) => (t.name === currentThemeName() ? `${t.name} (current)` : t.name))
      .join(" · ");
    actions.sendSystemNotice(`themes: ${names} — /theme <name> to switch`);
    return { kind: "handled" };
  }
  const result = applyTheme(q);
  if (!result.ok) {
    return { kind: "error", message: result.error };
  }
  const previous = takeThemePreviewOrigin(result.previous);
  actions.sendSystemNotice(
    previous === result.name
      ? `theme → ${result.name} (already active)`
      : `theme → ${result.name} (was ${previous}) — persisted`,
  );
  return { kind: "handled" };
}
