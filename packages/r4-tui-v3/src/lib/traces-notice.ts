/**
 * Shared trace-notice helper (work-row verbs + overlays, 2026-09-27):
 * fetch execution/job traces through the bridge and render them into one
 * keyed chat notice (up to 20 lines). Honest empty states by id type.
 */

import type { actions as ActionsType } from "../stores";
import { getCompanionBridge } from "../bridge";
import { statusIcon } from "../themes";

export interface TraceRow {
  step_name?: string;
  step?: string;
  name?: string;
  status?: string;
  duration_ms?: number;
}

/** Shared row normalization used by both work verbs and overlays. */
export function traceNoticesLines(rows: TraceRow[], cap = 20): string[] {
  return rows.slice(0, cap).map((tr) => {
    const step = (tr.step_name ?? tr.step ?? tr.name ?? "?") as string;
    const status = (tr.status ?? "?") as string;
    const dur = tr.duration_ms !== undefined ? ` ${Math.round(Number(tr.duration_ms) / 1000)}s` : "";
    return `${statusIcon(status)} ${step} · ${status}${dur}`;
  });
}

export async function postTraceNotice(
  storeActions: typeof ActionsType,
  verb: "traces.job" | "traces.execution",
  id: string,
  labelPrefix: string,
): Promise<string | null> {
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(verb, verb === "traces.job" ? { job_id: id } : { execution_id: id }, 15_000)) as {
    traces?: TraceRow[];
    count?: number;
  };
  const rows = Array.isArray(payload.traces) ? payload.traces : [];
  const short = id.slice(0, 12);
  if (rows.length === 0) {
    storeActions.sendSystemNotice(`${labelPrefix} ${short}: none — carries no trace rows`);
    return null;
  }
  const key = `${labelPrefix}:${short}`;
  storeActions.upsertSystemNotice(key, `traces ${short} (${payload.count ?? rows.length})`);
  storeActions.setNoticeDetails(key, traceNoticesLines(rows));
  return key;
}
