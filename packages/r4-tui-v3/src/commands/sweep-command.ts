/**
 * /sweep slash family (sweep batch 3, 2026-09-27): HPC sweep templates —
 * list the pack, dry-render one template (in-memory, never writes).
 * Bridge verbs sweep.templates / sweep.preview.
 */

import { getCompanionBridge } from "../bridge";
import { rpc } from "../bridge/rpc";
import { actions } from "../stores";
import { isOfflineBackend } from "../lib/console-mode";
import { SWEEP_HELP, SWEEP_PREVIEW_USAGE, sweepListText, sweepPreviewText } from "../lib/ops-text";

export type SweepDispatchResult = { kind: "handled" };

function _parseSub(args: string): { verb: string; rest: string[] } {
  const tokens = args.trim().split(/\s+/).filter(Boolean);
  const verb = (tokens.shift() ?? "").toLowerCase();
  return { verb, rest: tokens };
}

async function _list(): Promise<void> {
  const bridge = getCompanionBridge();
  try {
    actions.sendSystemNotice(sweepListText(await rpc(bridge, "sweep.templates", {}, 15_000)));
  } catch (err) {
    actions.sendSystemNotice(
      `sweep list failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

async function _preview(name: string): Promise<void> {
  if (!name) {
    actions.sendSystemNotice(SWEEP_PREVIEW_USAGE);
    return;
  }
  const bridge = getCompanionBridge();
  try {
    actions.sendSystemNotice(sweepPreviewText(name, await rpc(bridge, "sweep.preview", { name }, 15_000)));
  } catch (err) {
    actions.sendSystemNotice(
      `sweep preview failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

export async function handleSweepCommand(args: string): Promise<SweepDispatchResult> {
  if (isOfflineBackend()) {
    actions.sendSystemNotice("sweep: requires a live backend");
    return { kind: "handled" };
  }
  const { verb, rest } = _parseSub(args);
  if (verb === "list" || verb === "") {
    await _list();
    return { kind: "handled" };
  }
  if (verb === "preview") {
    await _preview(rest[0] ?? "");
    return { kind: "handled" };
  }
  actions.sendSystemNotice(SWEEP_HELP);
  return { kind: "handled" };
}
