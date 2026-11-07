/**
 * F9 chat-queue rail driver for the console: the shared rail
 * (`queue-rail.ts`, ADR-103 D-3, 2026-10-31) on the active tab's bridge.
 *
 * Never fabricated: a failure never silently pretends the action landed.
 */

import { getCompanionBridge } from "../bridge/jsonl-client";
import { queueOp, type QueueAction, type QueueOutcome } from "./queue-rail";

export { mayHaveLanded } from "./queue-rail";

/** Remote-first queue op on the active tab's bridge. */
export async function queueRemote(
  action: QueueAction,
  opts: { content?: string; clientId?: string; remoteOnly?: boolean } = {},
): Promise<QueueOutcome> {
  return queueOp(getCompanionBridge(), action, opts);
}
