/**
 * The chat queue rail on any bridge client (ADR-103 D-3, 2026-10-31): the
 * console (`chat-queue-rail.ts`, its active tab's bridge) and the VS Code
 * extension's bridge host. Remote-first via `session.queue_remote`
 * (server-side cross-client lane); falls back to session-local verbs when
 * the server lane is unreachable (offline/dev-tier lane rules).
 *
 * Never fabricated: a failure never silently pretends the action landed.
 * Pure: no store, Solid or Bun.
 */

import { BridgeRequestError } from "../bridge/wire";
import { rpc, type RpcRail, type RpcResult } from "../bridge/rpc";

/** Any one of the three session-local queue replies (one verb answers per call). */
type LocalQueueReply = Partial<
  RpcResult<"session.queue_append"> & RpcResult<"session.queue_pop"> & RpcResult<"session.queue_clear">
>;

export type QueueAction = "append" | "pop" | "clear";
/** `error`: why a not-applied verb failed (a typed session error when the
 * session changed in another console), for the caller's message.
 * `unconfirmed`: no answer came (a timeout, the bridge died mid-request), so
 * the server may still have applied it; only `applied: false` without it is
 * a definite refusal (cycle 3 verifier, round 5). */
export type QueueOutcome = { applied: boolean; usedRemote: boolean; count: number; error?: unknown; unconfirmed?: boolean };

/** Remote-first queue op. Remote lane failures fall through to local class
 * (silent-on-network but never silently claims applied). */
export async function queueOp(
  rail: RpcRail,
  action: QueueAction,
  opts: { content?: string; clientId?: string; remoteOnly?: boolean } = {},
): Promise<QueueOutcome> {
  try {
    const params: Record<string, unknown> = { action };
    if (action === "append" && opts.content) params.content = opts.content;
    if (opts.clientId) params.client_id = opts.clientId;
    const result = await rpc(rail, "session.queue_remote", params, 10_000);
    const applied = Boolean(result.applied);
    return { applied, usedRemote: true, count: Array.isArray(result.queue) ? result.queue.length : 0 };
  } catch (error) {
    if (opts.remoteOnly) return notApplied(error);
    return await queueLocal(rail, action, opts.content);
  }
}

/** Local-lane fallback (drops to session-local verbs; best-effort). */
async function queueLocal(rail: RpcRail, action: QueueAction, content?: string): Promise<QueueOutcome> {
  const localVerb = action === "append" ? "session.queue_append" : action === "pop" ? "session.queue_pop" : "session.queue_clear";
  try {
    const params: Record<string, unknown> = action === "append" && content ? { content } : {};
    const result: LocalQueueReply = await rpc(rail, localVerb, params, 10_000);
    return { applied: true, usedRemote: false, count: localQueueCount(result) };
  } catch (error) {
    return notApplied(error);
  }
}

/** Failures after which the request may still have reached the server.
 * Only an answer from the bridge is definite: a refusal, an HTTP status, a
 * connect error, a typed session error (nothing was applied), and "bridge
 * unavailable" (thrown before anything was written). The bridge's own HTTP
 * timeout or broken read (R4TimeoutError; R4Error "Request failed") is not,
 * nor is any failure without a bridge answer: the console's timeout, a
 * bridge that exited mid-request. */
const MAYBE_SENT_TYPES = new Set(["R4TimeoutError", "R4Error"]);

export function mayHaveLanded(error: unknown): boolean {
  if (isBridgeRequestError(error)) return MAYBE_SENT_TYPES.has(error.errorType ?? "");
  return !(error instanceof Error && error.message === "bridge unavailable");
}

/** A bridge's error reply, by class or by name: a second bundle copy of the
 * class (the VS Code extension bundles the wire on its own) fails instanceof. */
function isBridgeRequestError(error: unknown): error is BridgeRequestError {
  return error instanceof BridgeRequestError || (error instanceof Error && error.name === "BridgeRequestError");
}

function notApplied(error: unknown): QueueOutcome {
  const outcome: QueueOutcome = { applied: false, usedRemote: false, count: 0, error };
  if (mayHaveLanded(error)) outcome.unconfirmed = true;
  return outcome;
}

/** The queue length a local verb reports: queue rows, remaining, or cleared. */
function localQueueCount(result: LocalQueueReply): number {
  if (Array.isArray(result.queue)) return result.queue.length;
  if (typeof result.remaining === "number") return result.remaining;
  return typeof result.cleared === "number" ? result.cleared : 0;
}
