/**
 * Citation chip → passage preview (knowledge plan K1.4, 2026-10-24).
 *
 * A reply's citation block lists the passages the answer cited (or, when it
 * cited none, the ones the turn retrieved). `o` on the focused reply opens a
 * preview of its first source; ←/→ step through the others. The preview asks
 * the bridge for the passage itself (`citation.open`: the session label is
 * looked up in the evidence ledger and fetched under the server's access
 * check). Offline, or when the fetch fails, it shows the row's 200-character
 * preview and says why.
 */

import { getCompanionBridge } from "../bridge";
import { isOfflineBackend } from "./console-mode";
import { setWorkspace, workspace } from "../stores";
import type { Citation, CitationPreviewState } from "../types";
import type { CitationOpenResult } from "../bridge/rpc-types.gen";

const OPEN_TIMEOUT_MS = 15_000;
/** Neighbouring chunks each side the preview asks for. */
const PREVIEW_WINDOW = 1;

/** Ignores an answer for a source the user already stepped away from. */
let loadSeq = 0;

/** The row's session label: `sid`, else the `[S3]` its title starts with. */
export function citationSid(cite: Citation): string {
  if (cite.sid) return cite.sid;
  const match = /^\[(S\d+)\]/.exec(cite.title);
  return match ? match[1] : "";
}

/** The citation block's header: "Retrieved" when the answer cited none of
 * the passages its turn was shown (the footer's `Retrieved:` line). */
export function citationBlockLabel(citations: readonly Citation[]): string {
  return citations.length > 0 && citations.every((c) => c.kind === "retrieved")
    ? "Retrieved (not cited):"
    : "Citations:";
}

/** The reply header's chip: "cited N" counts the passages the answer cited;
 * a turn that cited none of what it retrieved says "retrieved N" (review of
 * K1 part A: the header read "cited 2" over "Retrieved (not cited):"). */
export function citationChip(citations: readonly Citation[] | undefined): string {
  const rows = citations ?? [];
  const cited = rows.filter((c) => c.kind !== "retrieved").length;
  if (cited > 0) return `cited ${cited}`;
  return rows.length > 0 ? `retrieved ${rows.length}` : "";
}

function fallbackState(state: CitationPreviewState, note: string): CitationPreviewState {
  const cite = state.citations[state.index];
  return { ...state, text: cite?.contentPreview ?? "", note, chunks: [] };
}

function offlineNote(openable: boolean): string {
  if (!openable) return "preview only: this row has no session label to open";
  return "preview only: opening the passage needs a live backend";
}

/** What `citation.open` is asked for: the label, and the passage handle the
 * row carries for when the session no longer holds the label. */
function openParams(cite: Citation, sid: string): Record<string, unknown> {
  return {
    sid,
    window: PREVIEW_WINDOW,
    ...(cite.sourceId ? { source_id: cite.sourceId, title: cite.title } : {}),
    ...(cite.projectId ? { project_id: cite.projectId } : {}),
  };
}

async function loadPassage(seq: number): Promise<void> {
  const state = workspace.citationPreview;
  if (!state) return;
  const cite = state.citations[state.index]!;
  const sid = citationSid(cite);
  const openable = Boolean(sid || cite.sourceId);
  if (!openable || isOfflineBackend()) {
    setWorkspace("citationPreview", fallbackState(state, offlineNote(openable)));
    return;
  }
  try {
    const out = (await getCompanionBridge().request(
      "citation.open",
      openParams(cite, sid || "S?"),
      OPEN_TIMEOUT_MS,
    )) as CitationOpenResult;
    if (seq !== loadSeq || !workspace.citationPreview) return;
    setWorkspace("citationPreview", { ...workspace.citationPreview, text: out.text ?? "", note: "", chunks: out.chunks ?? [] });
  } catch (err) {
    if (seq !== loadSeq || !workspace.citationPreview) return;
    const why = err instanceof Error ? err.message : String(err);
    setWorkspace("citationPreview", fallbackState(workspace.citationPreview, `preview only: ${why}`));
  }
}

function show(citations: Citation[], index: number): Promise<void> {
  setWorkspace("citationPreview", { citations, index, text: null, note: "", chunks: [] });
  loadSeq += 1;
  return loadPassage(loadSeq);
}

/** Open the preview at `index` of a reply's citations (no-op without any). */
export function openCitationPreview(citations: readonly Citation[] | undefined, index = 0): Promise<void> {
  if (!citations || citations.length === 0) return Promise.resolve();
  const at = Math.min(Math.max(0, index), citations.length - 1);
  return show([...citations], at);
}

/** Step to the previous (-1) or next (+1) source; stays at either end. */
export function stepCitationPreview(delta: number): Promise<void> {
  const state = workspace.citationPreview;
  if (!state) return Promise.resolve();
  const next = Math.min(Math.max(0, state.index + delta), state.citations.length - 1);
  if (next === state.index) return Promise.resolve();
  return show(state.citations, next);
}

export function closeCitationPreview(): void {
  loadSeq += 1;
  setWorkspace("citationPreview", null);
}
