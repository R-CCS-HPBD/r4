/**
 * tui-session-state — the one durable binding of "which chat tab holds
 * which session" (2026-10-05, A1 auto-resume).
 *
 * Claude Code attitude: the bridge session store already outlives the TUI;
 * what died with the process was the tab LAYOUT (order, titles, session
 * bindings). This file is that layout:
 *
 *   ${R4_CONFIG_HOME or ~/.config/r4}/tui-session-state.json
 *   { "tabs": [{"title","sessionId"}], "activeIndex": 1 }
 *
 * Rules: absent/corrupt → null (fresh start, no crash); writes are
 * atomic (tmp + rename) and debounced by the single writer call-site;
 * tab ids are process-local uuid's, so the file persists ORDER + an
 * active INDEX — never an id. Session ids stay display-derived — the
 * bridge session store stays the authority for transcripts (this file
 * never duplicates content).
 */

import { homedir } from "os";
import { join } from "path";
import { readFileSync, writeFileSync, renameSync, mkdirSync } from "fs";

export interface PersistedTab {
  title: string;
  sessionId: string;
}

export interface TabsState {
  tabs: PersistedTab[];
  activeIndex: number;
}

function statePath(): string {
  const base = process.env.R4_CONFIG_HOME ?? join(homedir(), ".config", "r4");
  return join(base, "tui-session-state.json");
}

/** Read the persisted layout; null when absent or malformed. */
export function readTabsState(): TabsState | null {
  try {
    const raw = JSON.parse(readFileSync(statePath(), "utf-8")) as Partial<TabsState>;
    if (!raw || !Array.isArray(raw.tabs) || raw.tabs.length === 0) return null;
    const tabs = raw.tabs
      .filter(
        (t): t is PersistedTab =>
          !!t &&
          typeof (t as { sessionId?: unknown }).sessionId === "string" &&
          (t as { sessionId?: string }).sessionId!.length > 0,
      )
      .map((t) => ({
        title: typeof t.title === "string" ? t.title : "",
        sessionId: String(t.sessionId),
      }));
    if (tabs.length === 0) return null;
    const activeIndex = typeof raw.activeIndex === "number" ? raw.activeIndex : 0;
    return { tabs, activeIndex: Math.min(Math.max(0, activeIndex), tabs.length - 1) };
  } catch {
    return null; // absent or corrupt — fresh start, never a crash
  }
}

/** Atomic replace (tmp + rename); single-flight — a stale in-flight write
 * never lands after a newer one. */
let pending: TabsState | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;

export function writeTabsState(state: TabsState, debounceMs = 250): void {
  pending = state;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    const next = pending;
    pending = null;
    if (!next) return;
    try {
      const path = statePath();
      mkdirSync(join(path, ".."), { recursive: true });
      const tmp = `${path}.tmp`;
      writeFileSync(tmp, JSON.stringify(next, null, 2) + "\n", "utf-8");
      renameSync(tmp, path);
    } catch {
      // a missed layout save is never a TUI failure
    }
  }, debounceMs);
  timer.unref?.();
}

/** Test seam: flush the debounced write synchronously. */
export async function flushTabsState(): Promise<void> {
  const ms = 50;
  await new Promise((r) => setTimeout(r, ms));
}
