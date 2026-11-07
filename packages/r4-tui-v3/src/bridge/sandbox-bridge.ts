/**
 * ADR-101 — the session's local tool sandbox over the bridge: the
 * session-start notice row, `session.sandbox_get` and `session.sandbox_set`.
 *
 * Maintainer requirement (2026-10-30): "default -> off as claude code but r4
 * tui needs to notify users and how to make this on during a session at
 * first". So a local session that opens (init_state, a new session, a
 * resume) with a bridge notice gets one keyed system row (`sandbox-notice`,
 * Enter expands what `/sandbox on` confines); `/sandbox on|off` updates the
 * same row in place.
 */

import { getCompanionBridge } from "./jsonl-client";
import { rpc, type RpcResult } from "./rpc";
import { applyBridgeStatePatch, mapBridgeStateToWorkspace } from "./map-state";
import { actions, workspace } from "../stores";
import { applyOnTab } from "../tabs/registry";
import {
  SANDBOX_NOTICE_KEY,
  mapSandbox,
  sandboxDetailLines,
  sandboxRowText,
  sandboxStartText,
  type SandboxSetReply,
  type SandboxView,
} from "../lib/sandbox";

/** A set can run the backend self-test and restart the MCP daemon. */
const SET_TIMEOUT_MS = 60_000;
const GET_TIMEOUT_MS = 15_000;

function sessionOf(snapshot: unknown): Record<string, unknown> | null {
  if (!snapshot || typeof snapshot !== "object") return null;
  const session = (snapshot as Record<string, unknown>).session;
  return session && typeof session === "object" ? (session as Record<string, unknown>) : null;
}

function showRow(text: string, state: SandboxView): void {
  if (!text) return;
  actions.upsertSystemNotice(SANDBOX_NOTICE_KEY, text);
  actions.setNoticeDetails(SANDBOX_NOTICE_KEY, sandboxDetailLines(state));
}

/** The session-start notice: shown when the bridge's notice is non-empty for
 * a local session; none for a frontend session or an old bridge. */
export function announceSandbox(state: SandboxView | null, venue: string | undefined): void {
  if (state) showRow(sandboxStartText(state, venue), state);
}

/** {@link announceSandbox} for a snapshot (init_state, initialize, load_session). */
export function announceSandboxFromSnapshot(snapshot: unknown): void {
  const session = sessionOf(snapshot);
  if (!session || !("sandbox" in session)) return;
  const venue = typeof session.venue === "string" ? session.venue : undefined;
  announceSandbox(mapSandbox(session.sandbox), venue);
}

/** The keyed row after a set: the new notice, or the short on line. */
export function refreshSandboxRow(state: SandboxView): void {
  showRow(sandboxRowText(state), state);
}

/** A refused verb as one line: the bridge's typed `data.code` + `reason`
 * (sandbox.unavailable | sandbox.self_test_failed | sandbox.remote …). */
export function sandboxErrorText(err: unknown): string {
  // BridgeRequestError's `data`, read by shape (test rails throw plain errors).
  const data = (err as { data?: Record<string, unknown> } | null)?.data;
  const code = typeof data?.code === "string" ? data.code : "";
  const reason = typeof data?.reason === "string" ? data.reason : "";
  if (code) return `${code}: ${reason || "refused"}`;
  return err instanceof Error ? err.message : String(err);
}

export type SandboxOutcome<T> = { ok: true; value: T } | { ok: false; message: string };

/** `session.sandbox_get`: the active session's sandbox, also landed on its tab. */
export async function fetchSandbox(): Promise<SandboxOutcome<SandboxView>> {
  const tabId = workspace.activeChatTabId;
  try {
    const result = await rpc(getCompanionBridge(), "session.sandbox_get", {}, GET_TIMEOUT_MS);
    const view = mapSandbox(result.sandbox);
    if (!view) return { ok: false, message: "the bridge sent no sandbox state" };
    applyOnTab(tabId, () => actions.setSandbox(view));
    return { ok: true, value: view };
  } catch (err) {
    return { ok: false, message: sandboxErrorText(err) };
  }
}

function setReply(result: RpcResult<"session.sandbox_set">, mode: string): SandboxSetReply {
  return {
    sandbox: mapSandbox(result.sandbox) ?? (mapSandbox({ mode }) as SandboxView),
    restartedMcp: (result.restarted_mcp ?? []).map(String),
    stillRunning: (result.still_running ?? []).map(String),
    mcpError: typeof result.mcp_error === "string" ? result.mcp_error : "",
  };
}

function landSet(state: unknown, view: SandboxView): void {
  if (state) applyBridgeStatePatch(mapBridgeStateToWorkspace(state, undefined));
  actions.setSandbox(view);
  refreshSandboxRow(view);
}

/** `session.sandbox_set {mode, default?}`. A refusal changes nothing and comes
 * back as its reason; the reply lands on the tab that asked. */
export async function setSessionSandbox(
  mode: "on" | "off",
  asDefault: boolean,
): Promise<SandboxOutcome<SandboxSetReply>> {
  const tabId = workspace.activeChatTabId;
  try {
    const params: Record<string, unknown> = asDefault ? { mode, default: true } : { mode };
    const result = await rpc(getCompanionBridge(), "session.sandbox_set", params, SET_TIMEOUT_MS);
    const reply = setReply(result, mode);
    applyOnTab(tabId, () => landSet(result.state, reply.sandbox));
    return { ok: true, value: reply };
  } catch (err) {
    return { ok: false, message: sandboxErrorText(err) };
  }
}
