/**
 * R4 Console v3 theme tokens and visual vocabulary.
 *
 * Canonical identity: Aurora (from packages/r4-design/tokens.json).
 * `/theme` switches at runtime (persisted at <configRoot>/theme.json);
 * R4_TUI_THEME=<name> overrides the persisted choice at launch.
 *
 * `palette` is a MUTABLE singleton: applyTheme() rewrites its keys in place,
 * so the ~100 static `palette.x` reads across components pick up the new
 * values on the next render. App.tsx wraps the tree in a keyed Show bound to
 * `themeEpoch` so a theme switch remounts everything once and repaints.
 */

import { createSignal } from "solid-js";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { Space } from "../types";
import { configRoot } from "../lib/config-root";

export const aurora = {
  bg: "#111018",
  bgPanel: "#1a1826",
  // Brighter than panel so nav selection / chat input read clearly on black.
  bgActive: "#34304f",
  bgHover: "#2e2b45",
  // Chat bubbles: user tinted accent, assistant stays near-background.
  msgUserBg: "#39336b",
  msgAssistantBg: "#141119",
  fg: "#ededf4",
  // Slightly brighter mute for placeholders and secondary labels.
  fgDim: "#8a87a8",
  fgBright: "#ffffff",
  accent: "#7C6CFF",
  accent2: "#2DD4EE",
  border: "#2e2b45",
  borderFocus: "#7C6CFF",
  statusOk: "#34D399",
  statusWarn: "#FBBF24",
  statusErr: "#FB7185",
  statusInfo: "#38BDF8",
  dashboard: "#7C6CFF",
  tasks: "#FBBF24",
  knowledge: "#2DD4EE",
  infrastructure: "#34D399",
  settings: "#A5A3C2",
  // D4: semantic diff keys (used by ui/DiffBlock).
  diffAdd: "#34D399",
  diffDel: "#FB7185",
  diffHunk: "#2DD4EE",
  diffMeta: "#8a87a8",
  // D4: semantic markdown accents (finalized per-line render path).
  mdHeading: "#7C6CFF",
  mdCode: "#2DD4EE",
  mdQuote: "#8a87a8",
  // T5 topic-16: role-tinted assistant prose — slightly warmer than fgDim so
  // prose runs distinguishable from system banners without inventing accents.
  proseAssistant: "#d6c9ff",
  proseSystem: "#a8d8c0",
} as const;

export const tokyoNight = {
  bg: "#0b0d10",
  bgPanel: "#11141a",
  bgActive: "#1a1f29",
  bgHover: "#232837",
  // Chat bubbles: user tinted accent, assistant stays near-background.
  msgUserBg: "#2b3851",
  msgAssistantBg: "#0e1217",
  fg: "#c7ccd6",
  fgDim: "#6b7280",
  fgBright: "#e6e9ef",
  accent: "#7aa2f7",
  accent2: "#bb9af7",
  border: "#2a2f3a",
  borderFocus: "#7aa2f7",
  statusOk: "#73daca",
  statusWarn: "#e0af68",
  statusErr: "#f7768e",
  statusInfo: "#7aa2f7",
  dashboard: "#7aa2f7",
  tasks: "#e0af68",
  knowledge: "#bb9af7",
  infrastructure: "#73daca",
  settings: "#a9b1d6",
  // D4: semantic diff keys.
  diffAdd: "#73daca",
  diffDel: "#f7768e",
  diffHunk: "#bb9af7",
  diffMeta: "#6b7280",
  // D4: semantic markdown accents.
  mdHeading: "#7aa2f7",
  mdCode: "#7aa2f7",
  mdQuote: "#6b7280",
  proseAssistant: "#a3bedf",
  proseSystem: "#88c0c4",
} as const;

export type ThemePalette = { readonly [K in keyof typeof aurora]: string };

const nord: ThemePalette = {
  bg: "#2e3440",
  bgPanel: "#3b4252",
  bgActive: "#434c5e",
  bgHover: "#4c566a",
  msgUserBg: "#4e6272",
  msgAssistantBg: "#2b303b",
  fg: "#d8dee9",
  fgDim: "#7b88a1",
  fgBright: "#eceff4",
  accent: "#88c0d0",
  accent2: "#81a1c1",
  border: "#4c566a",
  borderFocus: "#88c0d0",
  statusOk: "#a3be8c",
  statusWarn: "#ebcb8b",
  statusErr: "#bf616a",
  statusInfo: "#81a1c1",
  dashboard: "#88c0d0",
  tasks: "#ebcb8b",
  knowledge: "#81a1c1",
  infrastructure: "#a3be8c",
  settings: "#b48ead",
  diffAdd: "#a3be8c",
  diffDel: "#bf616a",
  diffHunk: "#81a1c1",
  diffMeta: "#616e88",
  mdHeading: "#88c0d0",
  mdCode: "#a3be8c",
  mdQuote: "#616e88",
  proseAssistant: "#a9c7f0",
  proseSystem: "#a8c879",
};

const dracula: ThemePalette = {
  bg: "#282a36",
  bgPanel: "#21222c",
  bgActive: "#44475a",
  bgHover: "#3a3c4e",
  msgUserBg: "#584a74",
  msgAssistantBg: "#232430",
  fg: "#f8f8f2",
  fgDim: "#8990b3",
  fgBright: "#ffffff",
  accent: "#bd93f9",
  accent2: "#8be9fd",
  border: "#44475a",
  borderFocus: "#bd93f9",
  statusOk: "#50fa7b",
  statusWarn: "#f1fa8c",
  statusErr: "#ff5555",
  statusInfo: "#8be9fd",
  dashboard: "#bd93f9",
  tasks: "#f1fa8c",
  knowledge: "#8be9fd",
  infrastructure: "#50fa7b",
  settings: "#ff79c6",
  diffAdd: "#50fa7b",
  diffDel: "#ff5555",
  diffHunk: "#8be9fd",
  diffMeta: "#6272a4",
  mdHeading: "#bd93f9",
  mdCode: "#50fa7b",
  mdQuote: "#6272a4",
  proseAssistant: "#f8a2b6",
  proseSystem: "#88d6cd",
};

const gruvboxDark: ThemePalette = {
  bg: "#282828",
  bgPanel: "#1d2021",
  bgActive: "#3c3836",
  bgHover: "#32302f",
  msgUserBg: "#544724",
  msgAssistantBg: "#242424",
  fg: "#ebdbb2",
  fgDim: "#928374",
  fgBright: "#fbf1c7",
  accent: "#fabd2f",
  accent2: "#8ec07c",
  border: "#504945",
  borderFocus: "#fabd2f",
  statusOk: "#b8bb26",
  statusWarn: "#fabd2f",
  statusErr: "#fb4934",
  statusInfo: "#83a598",
  dashboard: "#fabd2f",
  tasks: "#fe8019",
  knowledge: "#8ec07c",
  infrastructure: "#b8bb26",
  settings: "#d3869b",
  diffAdd: "#b8bb26",
  diffDel: "#fb4934",
  diffHunk: "#83a598",
  diffMeta: "#928374",
  mdHeading: "#fabd2f",
  mdCode: "#b8bb26",
  mdQuote: "#928374",
  proseAssistant: "#f0d4a7",
  proseSystem: "#8fb870",
};

const catppuccinMocha: ThemePalette = {
  bg: "#1e1e2e",
  bgPanel: "#181825",
  bgActive: "#313244",
  bgHover: "#292c3c",
  msgUserBg: "#453c5a",
  msgAssistantBg: "#1a1a26",
  fg: "#cdd6f4",
  fgDim: "#7f849c",
  fgBright: "#ffffff",
  accent: "#cba6f7",
  accent2: "#89dceb",
  border: "#45475a",
  borderFocus: "#cba6f7",
  statusOk: "#a6e3a1",
  statusWarn: "#f9e2af",
  statusErr: "#f38ba8",
  statusInfo: "#89b4fa",
  dashboard: "#cba6f7",
  tasks: "#f9e2af",
  knowledge: "#89dceb",
  infrastructure: "#a6e3a1",
  settings: "#f5c2e7",
  diffAdd: "#a6e3a1",
  diffDel: "#f38ba8",
  diffHunk: "#89dceb",
  diffMeta: "#7f849c",
  mdHeading: "#cba6f7",
  mdCode: "#a6e3a1",
  mdQuote: "#7f849c",
  proseAssistant: "#f2bfd6",
  proseSystem: "#77cad3",
};

const solarizedDark: ThemePalette = {
  bg: "#002b36",
  bgPanel: "#073642",
  bgActive: "#0a4552",
  bgHover: "#0c414d",
  msgUserBg: "#10506d",
  msgAssistantBg: "#032630",
  fg: "#839496",
  fgDim: "#5d747c",
  fgBright: "#fdf6e3",
  accent: "#268bd2",
  accent2: "#2aa198",
  border: "#0a4552",
  borderFocus: "#268bd2",
  statusOk: "#859900",
  statusWarn: "#b58900",
  statusErr: "#dc322f",
  statusInfo: "#268bd2",
  dashboard: "#268bd2",
  tasks: "#b58900",
  knowledge: "#2aa198",
  infrastructure: "#859900",
  settings: "#6c71c4",
  diffAdd: "#859900",
  diffDel: "#dc322f",
  diffHunk: "#2aa198",
  diffMeta: "#586e75",
  mdHeading: "#268bd2",
  mdCode: "#859900",
  mdQuote: "#586e75",
  proseAssistant: "#dd96a4",
  proseSystem: "#88d6b7",
};

const solarizedLight: ThemePalette = {
  bg: "#fdf6e3",
  bgPanel: "#eee8d5",
  bgActive: "#e0d7c0",
  bgHover: "#e9e2d0",
  msgUserBg: "#bcd1d4",
  msgAssistantBg: "#f6efdc",
  fg: "#586e75",
  fgDim: "#93a1a1",
  fgBright: "#002b36",
  accent: "#268bd2",
  accent2: "#2aa198",
  border: "#d8cfb8",
  borderFocus: "#268bd2",
  statusOk: "#859900",
  statusWarn: "#b58900",
  statusErr: "#dc322f",
  statusInfo: "#268bd2",
  dashboard: "#268bd2",
  tasks: "#b58900",
  knowledge: "#2aa198",
  infrastructure: "#859900",
  settings: "#6c71c4",
  diffAdd: "#859900",
  diffDel: "#dc322f",
  diffHunk: "#2aa198",
  diffMeta: "#93a1a1",
  mdHeading: "#268bd2",
  mdCode: "#859900",
  mdQuote: "#93a1a1",
  proseAssistant: "#f2bfd6",
  proseSystem: "#77cad3",
};

const monokai: ThemePalette = {
  bg: "#272822",
  bgPanel: "#1e1f1a",
  bgActive: "#3e3d32",
  bgHover: "#34352c",
  msgUserBg: "#423853",
  msgAssistantBg: "#22231c",
  fg: "#f8f8f2",
  fgDim: "#908e7f",
  fgBright: "#ffffff",
  accent: "#ae81ff",
  accent2: "#66d9ef",
  border: "#49483e",
  borderFocus: "#ae81ff",
  statusOk: "#a6e22e",
  statusWarn: "#e6db74",
  statusErr: "#f92672",
  statusInfo: "#66d9ef",
  dashboard: "#ae81ff",
  tasks: "#e6db74",
  knowledge: "#66d9ef",
  infrastructure: "#a6e22e",
  settings: "#fd971f",
  diffAdd: "#a6e22e",
  diffDel: "#f92672",
  diffHunk: "#66d9ef",
  diffMeta: "#75715e",
  mdHeading: "#ae81ff",
  mdCode: "#a6e22e",
  mdQuote: "#75715e",
  proseAssistant: "#f4a5bf",
  proseSystem: "#65d8bb",
};

const oneDark: ThemePalette = {
  bg: "#282c34",
  bgPanel: "#21252b",
  bgActive: "#3e4451",
  bgHover: "#363c46",
  msgUserBg: "#35516a",
  msgAssistantBg: "#242830",
  fg: "#abb2bf",
  fgDim: "#7f848e",
  fgBright: "#ffffff",
  accent: "#61afef",
  accent2: "#56b6c2",
  border: "#3e4451",
  borderFocus: "#61afef",
  statusOk: "#98c379",
  statusWarn: "#e5c07b",
  statusErr: "#e06c75",
  statusInfo: "#61afef",
  dashboard: "#61afef",
  tasks: "#e5c07b",
  knowledge: "#56b6c2",
  infrastructure: "#98c379",
  settings: "#c678dd",
  diffAdd: "#98c379",
  diffDel: "#e06c75",
  diffHunk: "#56b6c2",
  diffMeta: "#5c6370",
  mdHeading: "#c678dd",
  mdCode: "#98c379",
  mdQuote: "#5c6370",
  proseAssistant: "#a9c7f0",
  proseSystem: "#a8c879",
};

const rosePine: ThemePalette = {
  bg: "#191724",
  bgPanel: "#1f1d2e",
  bgActive: "#26233a",
  bgHover: "#2a283d",
  msgUserBg: "#48405c",
  msgAssistantBg: "#1b1926",
  fg: "#e0def4",
  fgDim: "#908caa",
  fgBright: "#ffffff",
  accent: "#c4a7e7",
  accent2: "#9ccfd8",
  border: "#403d52",
  borderFocus: "#c4a7e7",
  statusOk: "#9ccfd8",
  statusWarn: "#f6c177",
  statusErr: "#eb6f92",
  statusInfo: "#9ccfd8",
  dashboard: "#c4a7e7",
  tasks: "#f6c177",
  knowledge: "#9ccfd8",
  infrastructure: "#ebbcba",
  settings: "#6e6a86",
  diffAdd: "#31748f",
  diffDel: "#eb6f92",
  diffHunk: "#9ccfd8",
  diffMeta: "#6e6a86",
  mdHeading: "#c4a7e7",
  mdCode: "#f6c177",
  mdQuote: "#6e6a86",
  proseAssistant: "#d4a5a8",
  proseSystem: "#79aba8",
};

const kanagawa: ThemePalette = {
  bg: "#1f1f28",
  bgPanel: "#16161d",
  bgActive: "#2a2a37",
  bgHover: "#252533",
  msgUserBg: "#333c51",
  msgAssistantBg: "#1a1a22",
  fg: "#dcd7ba",
  fgDim: "#8a8a8a",
  fgBright: "#ffffff",
  accent: "#7e9cd8",
  accent2: "#7fb4ca",
  border: "#363646",
  borderFocus: "#7e9cd8",
  statusOk: "#98bb6c",
  statusWarn: "#e6c384",
  statusErr: "#ff5d62",
  statusInfo: "#7fb4ca",
  dashboard: "#7e9cd8",
  tasks: "#e6c384",
  knowledge: "#7fb4ca",
  infrastructure: "#98bb6c",
  settings: "#957fb8",
  diffAdd: "#98bb6c",
  diffDel: "#ff5d62",
  diffHunk: "#7fb4ca",
  diffMeta: "#727169",
  mdHeading: "#957fb8",
  mdCode: "#98bb6c",
  mdQuote: "#727169",
  proseAssistant: "#98bb68",
  proseSystem: "#9db8cf",
};

/** Accessibility-first: maximum separation, no subtle grays. */
const highContrast: ThemePalette = {
  bg: "#000000",
  bgPanel: "#0a0a0a",
  bgActive: "#1f1f1f",
  bgHover: "#171717",
  msgUserBg: "#1b3847",
  msgAssistantBg: "#050505",
  fg: "#ffffff",
  fgDim: "#b0b0b0",
  fgBright: "#ffffff",
  accent: "#4dc4ff",
  accent2: "#00e5a0",
  border: "#666666",
  borderFocus: "#ffff00",
  statusOk: "#3ff23f",
  statusWarn: "#ffd700",
  statusErr: "#ff4646",
  statusInfo: "#4dc4ff",
  dashboard: "#4dc4ff",
  tasks: "#ffd700",
  knowledge: "#00e5a0",
  infrastructure: "#3ff23f",
  settings: "#d0d0d0",
  diffAdd: "#3ff23f",
  diffDel: "#ff4646",
  diffHunk: "#00e5a0",
  diffMeta: "#b0b0b0",
  mdHeading: "#ffff00",
  mdCode: "#3ff23f",
  mdQuote: "#b0b0b0",
  proseAssistant: "#ffd01c",
  proseSystem: "#21aab9",
};

export interface ThemeDefinition {
  name: string;
  description: string;
  colors: ThemePalette;
}

/** All selectable themes — order is the /theme picker order. */
export const THEMES: ThemeDefinition[] = [
  { name: "aurora", description: "R4 identity (default)", colors: aurora },
  { name: "tokyo-night", description: "Tokyo Night — cool blue/lavender dark", colors: tokyoNight },
  { name: "nord", description: "Nord — arctic, low-contrast blue", colors: nord },
  { name: "dracula", description: "Dracula — vibrant purple dark", colors: dracula },
  { name: "gruvbox-dark", description: "Gruvbox — warm retro groove", colors: gruvboxDark },
  { name: "catppuccin-mocha", description: "Catppuccin Mocha — soft pastel dark", colors: catppuccinMocha },
  { name: "solarized-dark", description: "Solarized Dark — precision contrast", colors: solarizedDark },
  { name: "solarized-light", description: "Solarized Light — paper-bright", colors: solarizedLight },
  { name: "monokai", description: "Monokai — classic editor neon", colors: monokai },
  { name: "one-dark", description: "One Dark — Atom's subdued palette", colors: oneDark },
  { name: "rose-pine", description: "Rosé Pine — muted pines & roses", colors: rosePine },
  { name: "kanagawa", description: "Kanagawa — wave-inspired sumi ink", colors: kanagawa },
  { name: "high-contrast", description: "High contrast — accessibility first", colors: highContrast },
];

/** Spelling conveniences → canonical theme names (env var + /theme args). */
const THEME_ALIASES: Record<string, string> = {
  tokyo: "tokyo-night",
  "tokyo_night": "tokyo-night",
  gruvbox: "gruvbox-dark",
  catppuccin: "catppuccin-mocha",
  mocha: "catppuccin-mocha",
  solarized: "solarized-dark",
  rosepine: "rose-pine",
  "rose-pine-moon": "rose-pine",
  onedark: "one-dark",
  default: "aurora",
  hc: "high-contrast",
};

/** Normalize user input → canonical theme name (null when unknown). */
export function resolveThemeName(input: string): string | null {
  const q = input.trim().toLowerCase().replace(/[\s_]+/g, "-");
  if (!q) return null;
  if (THEMES.some((t) => t.name === q)) return q;
  return THEME_ALIASES[q] ?? null;
}

function themeFilePath(env: Record<string, string | undefined> = process.env): string {
  return join(configRoot(env), "theme.json");
}

function readPersistedTheme(env: Record<string, string | undefined> = process.env): string | null {
  try {
    const raw = JSON.parse(readFileSync(themeFilePath(env), "utf8")) as unknown;
    const name = (raw as { theme?: unknown }).theme;
    return typeof name === "string" ? resolveThemeName(name) : null;
  } catch {
    return null;
  }
}

function writePersistedTheme(name: string, env: Record<string, string | undefined> = process.env): void {
  const path = themeFilePath(env);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify({ theme: name }, null, 2) + "\n");
}

/** Active palette — mutable singleton; applyTheme() rewrites keys in place. */
export const palette: ThemePalette = { ...aurora };

/**
 * Remount epoch: bumped on every applyTheme(). App.tsx keys its tree on this
 * signal so a theme switch rebuilds every palette-propped node in one go.
 */
const [themeEpoch, setThemeEpoch] = createSignal(1);
export { themeEpoch };

let activeThemeName = "aurora";

function seedPalette(colors: ThemePalette): void {
  Object.assign(palette as unknown as Record<string, string>, colors);
  // T5 topic-16: prose-tint defaults when a theme lacks its own keys — pair
  // them with aurora's warm accents so every theme renders distinctly.
  const anyColors = colors as unknown as Record<string, string | undefined>;
  (palette as unknown as Record<string, string>).proseAssistant =
    anyColors.proseAssistant ?? aurora.proseAssistant;
  (palette as unknown as Record<string, string>).proseSystem =
    anyColors.proseSystem ?? aurora.proseSystem;
}

/** Launch-time theme: env var wins, then persisted choice, else aurora. */
(function initTheme() {
  const fromEnv = typeof process !== "undefined" ? resolveThemeName(process.env?.R4_TUI_THEME ?? "") : null;
  const name = fromEnv ?? readPersistedTheme() ?? "aurora";
  const def = THEMES.find((t) => t.name === name) ?? THEMES[0]!;
  activeThemeName = def.name;
  seedPalette(def.colors);
})();

export function currentThemeName(): string {
  return activeThemeName;
}

export function listThemes(): ThemeDefinition[] {
  return THEMES;
}

/**
 * Apply a theme by name (aliases allowed): rewrites the palette singleton,
 * persists the choice, and bumps the remount epoch. `persist: false` previews
 * without writing (used by tests / env-override scenarios).
 */
export function applyTheme(
  input: string,
  opts: { persist?: boolean; env?: Record<string, string | undefined> } = {},
): { ok: true; name: string; previous: string } | { ok: false; error: string } {
  const name = resolveThemeName(input);
  if (!name) {
    return { ok: false, error: `unknown theme "${input}" — try: ${THEMES.map((t) => t.name).join(", ")}` };
  }
  const def = THEMES.find((t) => t.name === name)!;
  const previous = activeThemeName;
  activeThemeName = def.name;
  seedPalette(def.colors);
  if (opts.persist !== false) {
    try {
      writePersistedTheme(def.name, opts.env ?? process.env);
    } catch {
      /* cosmetic-only failure — the live session still got the theme */
    }
  }
  setThemeEpoch((n) => n + 1);
  return { ok: true, name: def.name, previous };
}

export const icons = {
  project: "◆",
  goal: "◎",
  task: "▤",
  agent: "●",
  evidence: "◈",
  knowledge: "✦",
  publication: "▣",
  model: "🧠",
  endpoint: "⚡",
  job: "🔧",
  event: "›",
  sparkle: "✨",
  warning: "⚠",
  error: "✖",
  check: "✔",
  search: "🔍",
  menu: "☰",
} as const;

export const mascot = {
  body: [
    "   ╭─────╮   ",
    "  ╱  o o  ╲  ",
    " │    ◡    │ ",
    " │ ╭─────╮ │ ",
    "  ╰┳───┳━╯  ",
    "   ┃   ┃   ",
    "   ┗━━━┛   ",
  ],
  width: 13,
  height: 7,
};

export function statusColor(state: string): string {
  switch (state) {
    case "connected":
    case "ready":
    case "completed":
    case "success":
    case "achieved":
    case "accepted":
    case "published":
      return palette.statusOk;
    case "running":
    case "thinking":
    case "executing":
    case "active":
    case "connecting":
      return palette.statusInfo;
    case "degraded":
    case "warning":
    case "waiting":
    case "proposed":
      return palette.statusWarn;
    case "failed":
    case "error":
    case "disconnected":
    case "abandoned":
    case "withdrawn":
      return palette.statusErr;
    default:
      return palette.fgDim;
  }
}

export function statusIcon(state: string): string {
  switch (state) {
    case "ready":
    case "completed":
    case "success":
      return icons.check;
    case "running":
    case "thinking":
    case "executing":
      return "◐";
    case "failed":
    case "error":
      return icons.error;
    case "warning":
    case "degraded":
      return icons.warning;
    default:
      return "○";
  }
}

export function spaceColor(space: Space): string {
  switch (space) {
    case "dashboard":
      return palette.dashboard;
    case "tasks":
      return palette.tasks;
    case "knowledge":
      return palette.knowledge;
    case "infrastructure":
      return palette.infrastructure;
    case "settings":
      return palette.settings;
    default:
      return palette.accent;
  }
}
