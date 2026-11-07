/**
 * Session worktrees in the console (ADR-108 D-2, W1b) — pure helpers.
 *
 * A tab's bridge works in one directory; `/worktree <name>` moves it into an
 * isolated git worktree (`.r4/worktrees/<name>`, branch `r4/<name>`) and
 * `/worktree exit` moves it back. The tab keeps the worktree it is in, so
 * the console's own file features (git changes, `!` shell, @-mentions)
 * follow it (`resolveWorkspace`). Bridge-free; the Python side is
 * `r4/src/r4/companion/ui/worktree_bridge.py` and `r4/src/r4/worktrees.py`.
 */

import type { ChipState } from "./chip-state";

/** The worktree a tab works in. */
export interface WorktreeView {
  id: string;
  branch: string;
  path: string;
  /** The tab's workspace root (the worktree's path). */
  root: string;
}

export const WORKTREE_USAGE =
  "usage: /worktree [list] · /worktree <name> · /worktree exit [keep|remove] [--discard] · /worktree mode [off|auto]";

/** One registered worktree (`worktree.list` rows). */
export interface WorktreeRowLike {
  id: string;
  branch: string;
  path: string;
  parent?: string;
  changed?: boolean;
}

/** The status chip: `wt:<id>` while the tab works in a worktree. */
export function worktreeChip(view: WorktreeView | null | undefined): { label: string; state: ChipState } | null {
  return view ? { label: `wt:${view.id}`, state: "info" } : null;
}

/** The mode line `/worktree list` and `/worktree mode` print. */
export function worktreeModeText(mode: string, source: string): string {
  const what = mode === "auto" ? "the agent moves into a worktree before its first write" : "worktrees only when asked";
  return `worktree mode: ${mode} (${source}) — ${what}`;
}

/** `/worktree list` text. */
export function worktreeListText(rows: WorktreeRowLike[], current: string, error = "", modeLine = ""): string {
  const tail = modeLine ? `\n${modeLine}` : "";
  if (error) return `worktrees: ${error} — worktrees need a git repository${tail}`;
  if (rows.length === 0) return `worktrees: none — /worktree <name> creates one and works in it${tail}`;
  const lines = rows.map((r) => {
    const here = r.id === current ? "▸" : " ";
    const parent = r.parent ? ` ← ${r.parent}` : "";
    return `${here} ${r.id}${parent}  ${r.branch}  ${r.changed ? "changed" : "clean"}`;
  });
  return `worktrees (${rows.length}):\n${lines.join("\n")}${tail}`;
}

/** Parsed `/worktree` arguments. */
export type WorktreeArgs =
  | { verb: "list" }
  | { verb: "enter"; name: string; base: "head" | "default" }
  | { verb: "exit"; action: "keep" | "remove"; discard: boolean }
  | { verb: "mode"; mode: string }
  | { verb: "usage" };

function parseExit(rest: string[]): WorktreeArgs {
  const action = rest.includes("remove") ? "remove" : "keep";
  return { verb: "exit", action, discard: rest.includes("--discard") };
}

/** `/worktree` → what to do. */
export function parseWorktreeArgs(args: string): WorktreeArgs {
  const [head = "", ...rest] = args.trim().split(/\s+/).filter(Boolean);
  if (head === "" || head === "list") return { verb: "list" };
  if (head === "exit" || head === "leave") return parseExit(rest);
  if (head === "mode") return { verb: "mode", mode: rest[0] ?? "" };
  if (head === "help" || head.startsWith("-")) return { verb: "usage" };
  return { verb: "enter", name: head, base: rest.includes("--base=default") ? "default" : "head" };
}
