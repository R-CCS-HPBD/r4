/**
 * Shiki-backed syntax highlighting for transcript code fences.
 *
 * Uses the JavaScript regex engine (`shiki/engine/javascript`) so the TUI
 * binary stays WASM-free (`bun build --compile` ships no .wasm assets).
 * The highlighter initializes lazily in the background; until it reports
 * ready, callers fall back to the lightweight regex tokenizer in
 * `code-highlight.ts` so rendering is never blocked.
 *
 * Colors come straight from the bundled theme as hex strings, so tokens
 * stay readable independent of the TUI theme's semantic palette slots.
 */

import type { HighlighterCore } from "shiki";
import { createSignal } from "solid-js";

export interface ShikiSpan {
  text: string;
  fg?: string;
}

/** Languages pre-registered at init — covers the surfaces this platform
 * emits (python/shell/json/yaml dominate; the rest are common answers). */
const PRELOAD_LANGS = [
  "python",
  "javascript",
  "typescript",
  "jsx",
  "tsx",
  "json",
  "jsonc",
  "yaml",
  "toml",
  "bash",
  "shellscript",
  "markdown",
  "diff",
  "sql",
  "rust",
  "go",
  "c",
  "cpp",
  "java",
  "dockerfile",
  "makefile",
  "ini",
  "xml",
  "html",
  "css",
  "ruby",
  "lua",
  "perl",
  "r",
] as const;

const THEME = "github-dark-default";

let highlighter: HighlighterCore | null = null;
let initStarted = false;
const [ready, setReady] = createSignal(false);

/** Reactive readiness flag — components re-render when highlighting arms. */
export function shikiReady(): boolean {
  return ready();
}

/** Normalize a fence tag (```python3, ```py, ```sh, ```zsh …) to a shiki lang. */
export function normalizeShikiLang(lang: string | null | undefined): string | null {
  if (!lang) return null;
  const l = lang.trim().toLowerCase();
  if (!l) return null;
  const alias: Record<string, string> = {
    py: "python",
    python3: "python",
    js: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    ts: "typescript",
    mts: "typescript",
    cts: "typescript",
    sh: "bash",
    zsh: "bash",
    shell: "shellscript",
    yml: "yaml",
    "c++": "cpp",
    docker: "dockerfile",
    mk: "makefile",
    patch: "diff",
    plaintext: "markdown",
  };
  return alias[l] ?? l;
}

/** Kick off background init (idempotent). Called once at module import.
 * Dynamic imports keep the multi-MB grammar modules OUT of the static
 * module graph — sibling render paths must not pay shiki's parse cost. */
export function ensureShiki(): void {
  if (initStarted) return;
  initStarted = true;
  void Promise.all([import("shiki"), import("shiki/engine/javascript")])
    .then(([{ createHighlighter }, { createJavaScriptRegexEngine }]) =>
      createHighlighter({
        themes: [THEME],
        langs: [...PRELOAD_LANGS],
        engine: createJavaScriptRegexEngine(),
      }),
    )
    .then((h) => {
      highlighter = h;
      setReady(true);
    })
    .catch(() => {
      // Highlighting is an enhancement — never break the transcript on it.
      highlighter = null;
      setReady(false);
    });
}

/** Loaded-language check for graceful fallback to the regex tokenizer. */
function supportsLang(lang: string): boolean {
  if (!highlighter) return false;
  try {
    return highlighter.getLoadedLanguages().includes(lang);
  } catch {
    return false;
  }
}

/**
 * Highlight code lines → fg-colored spans per line, or null when shiki is
 * not ready / the language is unsupported (caller falls back to regex).
 * One call per fence block; input joined so multi-line constructs
 * (block comments, triple-quoted strings) tokenize correctly.
 */
export function highlightWithShiki(lines: string[], lang: string): ShikiSpan[][] | null {
  if (!highlighter) return null;
  if (!supportsLang(lang)) return null;
  try {
    const result = highlighter.codeToTokens(lines.join("\n"), { lang, theme: THEME });
    return result.tokens.map((lineTokens) =>
      lineTokens.map((t) => ({ text: t.content, fg: t.color })),
    );
  } catch {
    // Grammar hiccups must not blank the row — fall back to regex colors.
    return null;
  }
}

// Arm the engine at import time; streaming sessions open with it ready or
// with the regex fallback, never with a blocked render.
ensureShiki();
