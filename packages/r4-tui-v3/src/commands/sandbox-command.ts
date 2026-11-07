/**
 * /sandbox [status|on|off|default on|default off] — the session's local tool
 * sandbox (ADR-101). Off by default; `on` confines the agent's shell, Python
 * and local MCP servers for this session, `default on|off` also stores the
 * profile default. Bridge verbs session.sandbox_get / session.sandbox_set.
 * A frontend (remote) session already runs in the server's sandbox (ADR-046).
 *
 * `on|off` is refused while an answer streams (review LC-1, 2026-10-30): the
 * bridge parks a mutating verb until the turn ends, so the change would not
 * reach the rest of that turn and a long turn timed the request out while it
 * still applied afterwards. A set that times out is never reported as "not
 * changed": the state is read back first.
 */

import { actions, activeTab, workspace } from "../stores";
import { applyOnTab } from "../tabs/registry";
import { isOfflineBackend } from "../lib/console-mode";
import { SANDBOX_USAGE, sandboxSetText, sandboxStatusText } from "../lib/sandbox";
import { fetchSandbox, setSessionSandbox } from "../bridge/sandbox-bridge";

export type SandboxDispatchResult = { kind: "handled" } | { kind: "error"; message: string };

const REMOTE_MESSAGE =
  "sandbox: a remote session runs in the server's sandbox (ADR-046); /sandbox applies to local sessions";
export const STREAMING_MESSAGE =
  "⚠ sandbox not changed — wait for the answer to finish (or stop it), then run /sandbox again";

/** A reply posted on the tab that asked: a set can take up to 60 s (the
 * self-test and the MCP restart) and the user may switch tabs meanwhile
 * (tui-lane review 2026-10-30), like the state and the keyed row. */
function postOn(tabId: string, text: string): SandboxDispatchResult {
  applyOnTab(tabId, () => actions.sendSystemNotice(text));
  return { kind: "handled" };
}

/** A refusal: the dispatcher posts an error result on the then-active tab,
 * so one that resolves after a tab switch is posted on the asking tab here. */
function refuse(tabId: string, message: string): SandboxDispatchResult {
  return tabId === workspace.activeChatTabId ? { kind: "error", message } : postOn(tabId, message);
}

async function showStatus(): Promise<SandboxDispatchResult> {
  const tabId = workspace.activeChatTabId;
  const outcome = await fetchSandbox();
  if (!outcome.ok) return refuse(tabId, `sandbox status failed — ${outcome.message}`);
  return postOn(tabId, sandboxStatusText(outcome.value));
}

/** A failed set: a refusal changed nothing; a timeout may still apply, so
 * the state is read back instead of claiming "not changed". */
async function failedSet(tabId: string, mode: "on" | "off", message: string): Promise<SandboxDispatchResult> {
  if (!/timed out/i.test(message)) return refuse(tabId, `⚠ sandbox not changed — ${message}`);
  const now = await fetchSandbox();
  const state = now.ok ? `the sandbox is ${now.value.mode} now` : "its state could not be read";
  return refuse(tabId, `⚠ sandbox ${mode}: no answer in time (${message}); ${state} — see /sandbox status`);
}

async function setMode(mode: "on" | "off", asDefault: boolean): Promise<SandboxDispatchResult> {
  if (activeTab().sessionVenue === "frontend") return { kind: "error", message: REMOTE_MESSAGE };
  if (activeTab().chatStreaming) return { kind: "error", message: STREAMING_MESSAGE };
  const tabId = workspace.activeChatTabId;
  const outcome = await setSessionSandbox(mode, asDefault);
  if (!outcome.ok) return failedSet(tabId, mode, outcome.message);
  return postOn(tabId, sandboxSetText(outcome.value, asDefault));
}

/** The verb line (lower-cased, single-spaced) → its handler. */
const VERBS = new Map<string, () => Promise<SandboxDispatchResult>>([
  ["", showStatus],
  ["status", showStatus],
  ["on", () => setMode("on", false)],
  ["off", () => setMode("off", false)],
  ["default on", () => setMode("on", true)],
  ["default off", () => setMode("off", true)],
]);

export async function handleSandboxCommand(args: string): Promise<SandboxDispatchResult> {
  if (isOfflineBackend()) return { kind: "error", message: "sandbox: requires a live backend" };
  const verb = args.trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  const handler = VERBS.get(verb);
  return handler ? handler() : { kind: "error", message: SANDBOX_USAGE };
}
