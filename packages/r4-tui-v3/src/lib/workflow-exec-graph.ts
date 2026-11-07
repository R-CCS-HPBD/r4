/**
 * Live execution DAG for the workflow-execution overlay (2026-10-20).
 *
 * Merges the registered workflow DEFINITION's declared topology
 * (`workflow.package` steps with `depends_on`; fallback: declaration order
 * when the definition is unavailable — the definition card's honesty rule)
 * with the live per-step statuses of the polled execution record, and draws
 * it with the shared layered box-and-arrow renderer (`dag-layout`).
 *
 * Summarized status: a header count per bucket reusing the graph pane's
 * buckets via `statusForStep`. Detailed status: one narrow glyph inside each
 * node box (✔ ◐ ○ ✖ ◌ ⚠ — all narrow cells so the drawing stays aligned);
 * the step rows below the drawing remain the times/GPU/error detail surface.
 */

import { effectiveSteps, renderDag, type DagStep } from "./dag-layout";
import { statusForStep } from "./workflow-graph-projection";
import type { WorkflowDefStep } from "./workflow-def-types";

/** The least an execution step row must carry to join the DAG (structural). */
export interface ExecStatusRow {
  name: string;
  status: string;
}

export interface ExecDagModel {
  /** Accent header: `graph (N steps · ✔ 2 ◐ 1)` (+ topology note when linear). */
  header: string;
  /** The drawn lines, or a one-line honest fallback (`too wide…`). */
  lines: string[];
}

/** Bucket → narrow glyph, in summary-display order. */
const BUCKET_GLYPHS: ReadonlyArray<[string, string]> = [
  ["completed", "✔"],
  ["running", "◐"],
  ["pending", "○"],
  ["failed", "✖"],
  ["waiting", "◌"],
  ["blocked", "⚠"],
];

function bucketGlyph(bucket: string): string {
  return BUCKET_GLYPHS.find(([b]) => b === bucket)?.[1] ?? "○";
}

/** Latest status bucket per step name; unreported definition steps read pending. */
function bucketByStep(rows: ExecStatusRow[], defined: string[]): Map<string, string> {
  const buckets = new Map<string, string>();
  for (const name of defined) buckets.set(name, "pending");
  for (const row of rows) buckets.set(row.name, statusForStep(row.status));
  return buckets;
}

/** Nodes + topology: definition steps when present, else the execution rows
 * in recorded order (declaration-order chain via effectiveSteps). */
function dagSteps(
  rows: ExecStatusRow[],
  definitionSteps: WorkflowDefStep[] | undefined | null,
): { steps: DagStep[]; linear: boolean; byName: Map<string, string> } {
  if (definitionSteps && definitionSteps.length > 0) {
    const declared = definitionSteps.map((s) => ({ name: s.name, depends_on: s.depends_on }));
    const eff = effectiveSteps(declared);
    return { steps: eff.steps, linear: eff.linear, byName: bucketByStep(rows, eff.steps.map((s) => s.name)) };
  }
  const eff = effectiveSteps(rows.map((r) => ({ name: r.name })));
  return { steps: eff.steps, linear: eff.linear, byName: bucketByStep(rows, []) };
}

function summaryText(byName: Map<string, string>): string {
  const counts = new Map<string, number>();
  for (const bucket of byName.values()) counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
  return BUCKET_GLYPHS.filter(([b]) => (counts.get(b) ?? 0) > 0)
    .map(([b, glyph]) => `${glyph} ${counts.get(b)}`)
    .join(" ");
}

/** Short honest note kept small enough to survive the card's one-line clip. */
function topologyNote(linear: boolean, definitionSteps: WorkflowDefStep[] | undefined | null): string {
  if (!linear) return "";
  return definitionSteps && definitionSteps.length > 0
    ? " · linear (no depends_on)"
    : " · linear (no definition)";
}

/** Draw with status-glyph labels; tries the shrinking label budgets. */
function draw(steps: DagStep[], byName: Map<string, string>, width: number): string[] | null {
  const labelFor = (name: string): string => `${bucketGlyph(byName.get(name) ?? "pending")} ${name}`;
  for (const labelMax of [28, 18, 12]) {
    const drawing = renderDag(steps, { maxWidth: width, labelMax, labelFor });
    if (drawing.fits) return drawing.lines;
  }
  return null;
}

/** One live DAG model for an execution; null when there is honestly nothing
 * to draw (no definition, no step rows) — the caller renders no section. */
export function buildExecDag(src: {
  rows: ExecStatusRow[];
  definitionSteps?: WorkflowDefStep[] | null;
  width: number;
}): ExecDagModel | null {
  const { steps, linear, byName } = dagSteps(src.rows, src.definitionSteps);
  if (steps.length === 0) return null;
  const summary = summaryText(byName);
  const header = `graph (${steps.length} steps${summary ? ` · ${summary}` : ""}${topologyNote(linear, src.definitionSteps)})`;
  const lines = draw(steps, byName, src.width);
  return {
    header,
    lines: lines ?? ["too wide for this card — step list below has every step"],
  };
}
