/**
 * Bridge-backed fetchers for session file changes + governance overview.
 * Tolerant: gateway/bridge down keeps whatever data was already in store.
 */

import { actions } from "../stores";
import { getCompanionBridge } from "../bridge";
import { isOfflineBackend } from "./console-mode";
import type { GovernanceOverview, SessionFileChange } from "../types";

/** Cumulative session file edits recorded by the companion write/edit tools. */
export async function fetchSessionChanges(): Promise<void> {
  if (isOfflineBackend()) return;
  try {
    const res = (await getCompanionBridge().request("get_session_changes", {}, 15_000)) as {
      changes?: Array<Record<string, unknown>>;
    };
    if (Array.isArray(res.changes)) {
      const mapped: SessionFileChange[] = res.changes.map((c) => ({
        path: String(c.path ?? ""),
        before: String(c.before ?? ""),
        after: String(c.after ?? ""),
        inserted: typeof c.inserted === "number" ? c.inserted : undefined,
        deleted: typeof c.deleted === "number" ? c.deleted : undefined,
        edits: typeof c.edits === "number" ? c.edits : undefined,
        diff_text: typeof c.diff_text === "string" ? c.diff_text : undefined,
      }));
      actions.setSessionChanges(mapped);
    }
  } catch {
    /* bridge down — keep whatever inventory we had */
  }
}

/** UMRS governance overview: ownership / quotas / audit sections. */
export async function fetchGovernanceOverview(): Promise<void> {
  if (isOfflineBackend()) return;
  try {
    const res = (await getCompanionBridge().request(
      "get_governance_overview",
      {},
      20_000,
    )) as GovernanceOverview;
    actions.setGovernanceOverview(res);
  } catch {
    /* bridge down */
  }
}
