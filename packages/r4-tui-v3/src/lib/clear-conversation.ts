/**
 * /clear — blank the transcript AND reset the conversation the model sees.
 *
 * The companion keeps the turn history on its side (send_message carries
 * only the new text), so clearing the view alone left every earlier turn in
 * the next prompt (TUI audit 2026-10-17). The companion's own /clear
 * (_cmd_clear → session.clear: messages + tool state) is reached through
 * dispatch_slash_command; the catalog leaves it out only because each
 * surface owns the view half.
 */

import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import { actions, workspace, activeTab } from "../stores";
import { isOfflineBackend } from "./console-mode";
import { escStopKeys } from "./esc-interrupt";

export async function clearConversation(): Promise<void> {
  // The bridge parks this RPC until the running turn ends: the rest of the
  // turn would stream into a blank view, then the clear would drop it too.
  if (activeTab().chatStreaming) {
    actions.sendSystemNotice(`turn in progress — press ${escStopKeys()} to stop it, then /clear`);
    return;
  }
  actions.clearChat();
  if (isOfflineBackend()) return;
  try {
    const result = await rpc(getCompanionBridge(), "dispatch_slash_command", { command: "/clear" }, 30_000);
    if (!result?.cleared) {
      actions.sendSystemNotice("clear: transcript cleared, but the companion kept the conversation — /new starts a fresh one");
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    actions.sendSystemNotice(`clear: transcript cleared, but the companion kept the conversation (${message}) — /new starts a fresh one`);
  }
}
