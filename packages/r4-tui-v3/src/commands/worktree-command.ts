/**
 * /worktree — work in an isolated git worktree, modeled on Claude Code
 * (ADR-108 D-2, W1b). Bridge verbs `worktree.list|enter|exit`.
 *
 *   /worktree [list]                       the repository's worktrees
 *   /worktree <name> [--base=default]      make (or reuse) one and work in it
 *   /worktree exit [keep|remove] [--discard]
 *
 * Tab-scoped like the project: the tab's bridge moves into the worktree and
 * the tab remembers it (`wt:<name>` chip; git changes, `!` and @-mentions
 * follow it). A changed worktree is kept on exit unless `--discard`.
 * Entering and leaving are refused while an answer streams: the bridge parks
 * a mutating verb until the turn ends (the /sandbox rule).
 */

import { actions, activeTab } from "../stores";
import { isOfflineBackend } from "../lib/console-mode";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import {
  WORKTREE_USAGE,
  parseWorktreeArgs,
  worktreeListText,
  worktreeModeText,
  type WorktreeArgs,
  type WorktreeView,
} from "../lib/worktree";
import type { WorktreeEnterResult, WorktreeListResult } from "../bridge/rpc-types.gen";

export type WorktreeDispatchResult = { kind: "handled" } | { kind: "error"; message: string };

export const WORKTREE_STREAMING =
  "⚠ worktree not changed — wait for the answer to finish (or stop it), then run /worktree again";

const handled = (text: string): WorktreeDispatchResult => {
  actions.sendSystemNotice(text);
  return { kind: "handled" };
};

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** The tab's view of a worktree row the bridge returned. */
export function viewOf(result: WorktreeEnterResult): WorktreeView | null {
  const wt = result.worktree;
  if (!wt) return null;
  return { id: wt.id, branch: wt.branch, path: wt.path, root: result.workspace_root || wt.path };
}

async function list(): Promise<WorktreeDispatchResult> {
  const result: WorktreeListResult = await rpc(getCompanionBridge(), "worktree.list", {}, 15_000);
  const modeLine = worktreeModeText(result.mode ?? "off", result.mode_source ?? "default");
  return handled(worktreeListText(result.worktrees, result.current, result.error ?? "", modeLine));
}

/** `/worktree mode [off|auto]`: read, or set this session's mode. */
async function mode(wanted: string): Promise<WorktreeDispatchResult> {
  const result = await rpc(getCompanionBridge(), "worktree.mode", wanted ? { mode: wanted } : {}, 15_000);
  if (!result.ok) return { kind: "error", message: result.message };
  return handled(worktreeModeText(result.mode, result.mode_source));
}

/** `worktree_event` (the tab's own bridge): the tab follows a worktree the
 * agent, auto mode or another surface entered or left. */
export function handleWorktreeEvent(payload: unknown): void {
  const event = (payload ?? {}) as WorktreeEnterResult & { action?: string; source?: string };
  if (!event.ok) return;
  actions.setWorktree(event.action === "enter" ? viewOf(event) : null);
  if (event.source === "agent" || event.source === "auto") {
    actions.sendSystemNotice(`${event.source === "auto" ? "auto " : "agent: "}${event.message}`);
  }
}

async function enter(name: string, base: string): Promise<WorktreeDispatchResult> {
  const result = await rpc(getCompanionBridge(), "worktree.enter", { name, base }, 120_000);
  if (!result.ok) return { kind: "error", message: result.message };
  actions.setWorktree(viewOf(result));
  const warning = result.warning ? `\n⚠ ${result.warning}` : "";
  return handled(`${result.message}\nthe agent now works here; /worktree exit leaves${warning}`);
}

async function leave(action: string, discard: boolean): Promise<WorktreeDispatchResult> {
  const result = await rpc(getCompanionBridge(), "worktree.exit", { action, discard }, 60_000);
  if (!result.ok) return { kind: "error", message: result.message };
  actions.setWorktree(null);
  return handled(result.message);
}

async function run(parsed: WorktreeArgs): Promise<WorktreeDispatchResult> {
  if (parsed.verb === "usage") return { kind: "error", message: WORKTREE_USAGE };
  if (parsed.verb === "list") return list();
  if (activeTab().chatStreaming) return { kind: "error", message: WORKTREE_STREAMING };
  if (parsed.verb === "mode") return mode(parsed.mode);
  if (parsed.verb === "enter") return enter(parsed.name, parsed.base);
  return leave(parsed.action, parsed.discard);
}

export async function handleWorktreeCommand(args: string): Promise<WorktreeDispatchResult> {
  if (isOfflineBackend()) return { kind: "error", message: "worktree: requires a live backend" };
  try {
    return await run(parseWorktreeArgs(args));
  } catch (err) {
    return { kind: "error", message: `worktree failed — ${errorText(err)}` };
  }
}

/** A session started in a worktree (`r4 --worktree NAME`) shows its chip:
 * the bridge's directory is inside a registered worktree. A read; failures
 * keep the tab as it is. */
export async function syncWorktreeFromBridge(): Promise<void> {
  try {
    const result = await rpc(getCompanionBridge(), "worktree.list", {}, 15_000);
    const row = result.worktrees.find((w) => w.id === result.current);
    actions.setWorktree(row ? { id: row.id, branch: row.branch, path: row.path, root: row.path } : null);
  } catch {
    // an older bridge, or no repository — no chip
  }
}
