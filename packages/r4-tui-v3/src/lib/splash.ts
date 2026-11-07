/**
 * Splash branding for the v3 launch frame (W2).
 * Pure content so tests can assert structure without rendering.
 */

/** R4 block-letter logo (4 rows, ≤ 60 cols for narrow terminals). */
export const R4_LOGO = [
  "██████╗ ██╗  ██╗",
  "██╔══██╗██║  ██║",
  "██████╔╝███████║",
  "██╔══██╗╚════██║",
  "██║  ██║     ██║",
  "╚═╝  ╚═╝     ╚═╝",
];

export const SPLASH_TAGLINE = "Research Companion Console";
export const SPLASH_HINTS = [
  "chat is the primary surface — type to start",
  "Esc opens/closes panes · pane verbs per view · card verbs in transcripts",
  "/skills · /mcp · /workflow — selectable overlays · /code coding mode",
  "Ctrl+P command palette — this list IS the help",
];

/**
 * Legacy R4 companion wave art (from companion-tui-js welcome panel).
 * Kept alongside the block logo — adds, never replaces.
 */
export const LEGACY_WAVE_LOGO = [
  "               ██████████████",
  "                      ███████████",
  "         ████████████  ████████████",
  "        █████         ██████████████",
  "       ████    ██████████████████████",
  "      █████  █   █████████████████████",
  "     ██████  ███   ████████████████████",
  "     ██████  █████   ██████████████████",
  "    ███████  ███████   █████████████████",
  "    ███████  ███████████████████████████",
  "     ██████  ██████████████████████████",
  "      █████  ██████████████████████████",
  "      █████  █████████████████████████",
  "       ████  ████████████████████████",
  "         ██  ███████████████████████",
  "          █  █████████████████████",
  "             ███████████████████",
  "                ████████████",
];

export function splashLines(version: string): string[] {
  return [
    ...R4_LOGO,
    "",
    `${SPLASH_TAGLINE} — v${version}`,
    "",
    ...SPLASH_HINTS.map((hint) => `  ${hint}`),
  ];
}

/** Splash body with the legacy wave art appended under the hint block. */
export function splashLinesWithLegacy(version: string): string[] {
  return [...splashLines(version), "", ...LEGACY_WAVE_LOGO];
}
