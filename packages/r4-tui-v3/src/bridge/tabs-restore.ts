/**
 * tabs-restore — A1 auto-resume (2026-10-05): rebuild the previous run's
 * chat-tab layout from `tui-session-state.json` and resume its sessions.
 *
 * Claude Code attitude: closing the console keeps sessions (the bridge
 * store is durable); reopening restores every tab's binding. The ACTIVE
 * tab resumes immediately; background tabs mark their runtime
 * `restoredPending` and resume lazily on first activation — the store's
 * tabActivate hook issues the same resume a manual tabOpenSession would.
 */

import { actions, setWorkspace, workspace } from "../stores";
import { applyOnTab, getRuntime, runtimeOrNull } from "../tabs/registry";
import { makeTabMeta } from "../tabs/model";
import { readTabsState } from "../lib/tui-session-state";

/** Replace the fresh first tab with the persisted layout and resume the
 * active session. No-op without a state file (fresh start). */
export async function restoreTabsFromState(): Promise<void> {
  const saved = readTabsState();
  if (!saved) return;
  const metas = saved.tabs.map((t, i) => {
    const meta = makeTabMeta(i + 1);
    if (t.title) meta.title = t.title;
    meta.sessionId = t.sessionId;
    return meta;
  });
  setWorkspace("chatTabs", metas);
  const clamped = Math.min(Math.max(0, saved.activeIndex), metas.length - 1);
  const active = metas[clamped]!;
  setWorkspace("activeChatTabId", active.id);
  for (const meta of metas) getRuntime(meta.id).restoredPending = true;
  if (!workspace.demoMode && active.sessionId) {
    // Captured before the await: by the time resume answers, the tab's meta
    // is rebound to whatever session its bridge opened.
    const wanted = active.sessionId;
    const { resumeSession } = await import("./session-bridge");
    const ok = await resumeSession(wanted);
    const rt = runtimeOrNull(active.id);
    if (rt) rt.restoredPending = false;
    if (!ok) actions.resetTabTitle(active.id);
    const notice = restoreNotice(metas.length, wanted, ok);
    if (notice) applyOnTab(active.id, () => actions.sendSystemNotice(notice));
  }
}

/** The restore line, honest about a failed resume: it claimed "active
 * session resumed" right under the "resume failed" card (and, with one tab,
 * named the fresh session the bridge opened instead). A one-tab success
 * says nothing more — resumeSession already posted its own notice. */
export function restoreNotice(tabCount: number, wanted: string, ok: boolean): string {
  const id8 = wanted.slice(0, 8);
  if (tabCount <= 1) return ok ? "" : `session ${id8} could not be resumed — started a new session`;
  return ok
    ? `restored ${tabCount} tabs (active session resumed; others resume on activation)`
    : `restored ${tabCount} tabs; session ${id8} could not be resumed — this tab started a new session (others resume on activation)`;
}
