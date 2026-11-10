/**
 * The companion bridge wire, shared by every client of
 * `r4.companion.ui.bridge_server` (ADR-103 D-3, 2026-10-31): the console's
 * `jsonl-client.ts` and the VS Code extension's `src/bridge/companion-bridge.ts`.
 *
 * Pure: no Bun, Solid, OpenTUI or store import, and nothing runs at import.
 * The VS Code extension bundles this file as is, and its
 * `test/shared-modules.test.mjs` fails if it ever imports one of those.
 */

export const BRIDGE_PROTOCOL_VERSION = 1;

/** The bridge's argv; the `r4 --web` flag rides `--web-access`, the
 * `r4 --sandbox` flag `--sandbox` (bridge_server argparse). */
export function bridgeArgv(
  python: string,
  workspace: string,
  webAccess: string | null,
  sandbox: "on" | "off" | null = null,
): string[] {
  const argv = [python, "-m", "r4.companion.ui.bridge_server", "--fs-root", workspace];
  if (webAccess) argv.push("--web-access", webAccess);
  if (sandbox) argv.push("--sandbox", sandbox);
  return argv;
}

/**
 * Validate the initialize response's protocol version.
 * Integer mismatch → refuse to start (garbled UI is worse than a clear error).
 * Missing field → legacy pre-handshake server; accepted with a stderr notice so
 * the user knows to upgrade r4. `client` names the side that refuses.
 */
export function checkBridgeProtocol(initResult: unknown, onLegacy?: () => void, client = "TUI"): void {
  const version =
    initResult && typeof initResult === "object"
      ? (initResult as Record<string, unknown>).bridge_protocol
      : undefined;
  if (version === BRIDGE_PROTOCOL_VERSION) return;
  if (version === undefined) {
    onLegacy?.();
    return;
  }
  throw new Error(
    `Bridge protocol mismatch: ${client} expects v${BRIDGE_PROTOCOL_VERSION}, server speaks v${String(version)}. ` +
      "Upgrade r4 and the TUI together (pip install -U r4 / rebuild packages/r4-tui-v3).",
  );
}

/**
 * Methods that run a whole agent turn on the bridge (`_run_cancellable`).
 * While one is pending the server answers reads at once from its read pool
 * and parks mutating RPCs until the turn ends (plan H-8, 2026-10-18; the
 * classes live in r4/src/r4/companion/ui/rpc_concurrency.py, and
 * r4/tests/test_bridge_concurrency.py keeps this set equal to its TURN class).
 * Pollers need no turn guard; before H-8 every mid-turn RPC parked.
 */
export const TURN_METHODS: ReadonlySet<string> = new Set([
  "send_message",
  "confirm_plan",
  "dispatch_slash_command",
]);

/**
 * A bridge RPC failure that keeps the server's recovery line: `error.hint`
 * (401 → /login, 404 → /models, 429 → /quota …) and `error.type`.
 */
export class BridgeRequestError extends Error {
  readonly hint?: string;
  readonly errorType?: string;
  /** `error.data` — a typed case for clients (c3-session: `code` is
   * "session_conflict" | "session_corrupt", plus its ids). */
  readonly data?: Record<string, unknown>;

  constructor(message: string, hint?: string, errorType?: string, data?: Record<string, unknown>) {
    super(message);
    this.name = "BridgeRequestError";
    if (hint) this.hint = hint;
    if (errorType) this.errorType = errorType;
    if (data && typeof data === "object") this.data = data;
  }
}

/** The server's recovery hint carried by a rejected bridge request ("" when none). */
export function bridgeErrorHint(err: unknown): string {
  const hint = err instanceof BridgeRequestError ? err.hint : undefined;
  return typeof hint === "string" ? hint.trim() : "";
}

/** The error a failed reply (`ok: false`) rejects with. */
export function bridgeReplyError(response: Record<string, unknown>): BridgeRequestError {
  const errObj = response.error as
    | { message?: string; hint?: string; type?: string; data?: Record<string, unknown> }
    | undefined;
  return new BridgeRequestError(errObj?.message || "bridge request failed", errObj?.hint, errObj?.type, errObj?.data);
}

/** The client-side event a one-way bridge line is emitted as. */
export type BridgeChannel =
  | "event"
  | "project_job_event"
  | "team_event"
  | "search_server_event"
  | "worktree_event"
  | "session_title"
  | "session_inbox"
  | "approval"
  | "ask_user"
  | "turn_stopped"
  | "control_event";

export type RoutedBridgeLine =
  | { kind: "notification"; channel: BridgeChannel; payload: unknown }
  | { kind: "response" };

function objectField(line: Record<string, unknown>, key: string): boolean {
  return Boolean(line[key]) && typeof line[key] === "object";
}

const OBJECT_CHANNELS: ReadonlyArray<[string, BridgeChannel]> = [
  ["event", "event"],
  ["project_job_event", "project_job_event"],
  ["team_event", "team_event"],
  ["search_server_event", "search_server_event"],
  ["worktree_event", "worktree_event"],
  ["session_title", "session_title"],
  // ADR-114 D-6: messages from another R4 session reached this tab's inbox.
  ["session_inbox", "session_inbox"],
];

const TRUTHY_CHANNELS: ReadonlyArray<[string, BridgeChannel]> = [
  ["approval_request", "approval"],
  ["ask_user_request", "ask_user"],
  ["turn_stopped", "turn_stopped"],
];

/**
 * Route one parsed bridge line: a one-way notification (and the channel it
 * is emitted on) or a reply to a request. Control-schema envelopes
 * (budget_state, cost, thinking, canonical tool lines) are top-level
 * `{"type", "payload"}` lines carrying read-models; the whole line is the
 * payload.
 */
export function routeBridgeLine(line: Record<string, unknown>): RoutedBridgeLine {
  for (const [key, channel] of OBJECT_CHANNELS) {
    if (objectField(line, key)) return { kind: "notification", channel, payload: line[key] };
  }
  for (const [key, channel] of TRUTHY_CHANNELS) {
    if (line[key]) return { kind: "notification", channel, payload: line[key] };
  }
  if (typeof line.type === "string" && (line.payload === null || typeof line.payload === "object")) {
    return { kind: "notification", channel: "control_event", payload: line };
  }
  return { kind: "response" };
}

/** ADR-104 D-3: the answer to an approval_request. */
export type ApprovalWireDecision = "once" | "always" | "reject";

/** Out-of-band reply to a server-initiated tool approval request. */
export function approvalResponseLine(id: string | number, decision: ApprovalWireDecision): string {
  return JSON.stringify({ approval_response: { id, decision } });
}

/** Out-of-band reply to a server-initiated ask_user request. */
export function askUserResponseLine(id: string | number, answer: string | null): string {
  return JSON.stringify({ ask_user_response: { id, answer } });
}

/** Out-of-band stop of the in-flight turn (no reply expected). */
export const CANCEL_TURN_LINE = JSON.stringify({ cancel_turn: true });
