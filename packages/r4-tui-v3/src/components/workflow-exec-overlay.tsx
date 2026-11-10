/**
 * Workflow-execution mission overlay (TUI/rccs sweep batch, 2026-09-27).
 *
 * Enter on a workflow-execution row: one card joining what the HPC run is
 * RIGHT NOW — workflow identity, status, a live status DAG (2026-10-20:
 * definition depends_on topology + glyph-per-node status from the polled
 * execution record, with a bucket-count summary header; linear fallback
 * honestly labeled), per-step rows (status, wall time, measured GPU cost
 * when the step executor stamped it), and a cost footer summing only
 * measured steps (unmeasured steps counted, never approximated — ADR-067's
 * own rule). Re-renders reactively off the store's live poll; `T` opens
 * execution traces, `x` cancels, Esc closes.
 */

import { For, Show, createMemo } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import { palette, statusIcon } from "../themes";
import { workspace, actions } from "../stores";
import { umrsWorkflowStore, umrsWorkflowActions } from "../stores/umrs-workflow";
import { clipOneLine } from "../lib/team-live";
import { progressSuffix } from "../lib/workflow-activity";
import { buildExecDag } from "../lib/workflow-exec-graph";
import { statusForStep } from "../lib/workflow-graph-projection";
import { runDefinitionKey, runLabelWithDigest, type VersionedRun } from "../lib/workflow-versions";
import { Floating } from "../ui/Floating";

interface StepRow {
  name: string;
  status: string;
  elapsedSeconds?: number;
  gpuSeconds?: number;
  jobId?: string;
  error?: string;
}

function _asRecord(v: unknown): Record<string, unknown> | undefined {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : undefined;
}

function _num(v: unknown): number | undefined {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : undefined;
}

/** Normalize the execution's result steps (map or list) into display rows.
 * Anything missing stays undefined — a step with no sacct stamp keeps no cost. */
export function mapExecutionSteps(exec: Record<string, unknown>): StepRow[] {
  const results = _asRecord(exec.results) ?? {};
  const raw = results.steps ?? exec.steps;
  const source: Array<[string, Record<string, unknown>]> = Array.isArray(raw)
    ? raw.map((v, i) => [String(i), _asRecord(v) ?? {}])
    : raw && typeof raw === "object"
      ? Object.entries(raw as Record<string, unknown>).map(([k, v]) => [k, _asRecord(v) ?? {}])
      : [];
  return source.slice(0, 32).map(([key, step]) => {
    const extra = _asRecord(step.extra) ?? {};
    const slurm = _asRecord(extra.slurm) ?? {};
    const error =
      typeof step.error === "string" ? step.error
      : typeof step.stderr_tail === "string" ? step.stderr_tail
      : undefined;
    return {
      name: String(step.name ?? step.step_name ?? key),
      status: String(step.status ?? "?"),
      elapsedSeconds: _num(slurm.elapsed_seconds) ?? _num(step.elapsed_seconds),
      gpuSeconds: _num(slurm.gpu_seconds),
      jobId: typeof slurm.job_id === "string" ? slurm.job_id : undefined,
      error,
    };
  });
}

export function costSummary(rows: StepRow[]): { measuredGpuSeconds: number; unmeasured: number } {
  let measured = 0;
  let unmeasured = 0;
  for (const row of rows) {
    if (row.gpuSeconds !== undefined) measured += row.gpuSeconds;
    else if (statusForStep(row.status) === "completed") unmeasured += 1;
  }
  return { measuredGpuSeconds: measured, unmeasured };
}

function StepLine(props: { row: StepRow }) {
  const tail = () => {
    const bits: string[] = [];
    if (props.row.elapsedSeconds !== undefined) bits.push(`${Math.round(props.row.elapsedSeconds)}s`);
    if (props.row.gpuSeconds !== undefined) bits.push(`gpu ${props.row.gpuSeconds.toFixed(1)}s`);
    if (props.row.error) bits.push(clipOneLine(props.row.error, 48));
    return bits.join(" · ");
  };
  return (
    <box flexDirection="column">
      <box height={1} overflow="hidden">
        <text fg={palette.fg}>
          {`  ${statusIcon(props.row.status)} ${props.row.name} · ${props.row.status}${tail() ? ` · ${tail()}` : ""}`}
        </text>
      </box>
    </box>
  );
}

export function WorkflowExecOverlay() {
  const dims = useTerminalDims();
  const execId = () => workspace.execDetailId;

  const exec = createMemo(() => {
    const id = execId();
    return id
      ? (umrsWorkflowStore.activeExecutions[id] as unknown as Record<string, unknown> | undefined)
      : undefined;
  });

  const rows = createMemo(() => mapExecutionSteps(exec() ?? {}));
  const cost = createMemo(() => costSummary(rows()));

  /** Cached definition topology for the DAG (`workflow.package`, fetched at
   * overlay-open in the work route): undefined until it lands, absent on
   * error — buildExecDag degrades to the honestly labeled linear chain. */
  const dagModel = createMemo(() => {
    const e = exec();
    const key = e ? runDefinitionKey(e as VersionedRun) : undefined;
    const definition = key ? umrsWorkflowStore.definitions[key] : undefined;
    const width = Math.max(30, Math.floor(dims().width * 0.72) - 4);
    return buildExecDag({ rows: rows(), definitionSteps: definition?.steps ?? null, width });
  });


  const header = createMemo(() => {
    const e = exec();
    if (!e) return undefined;
    // ADR-113 D-4: the version and code digest this run pinned.
    const name = runLabelWithDigest(e as VersionedRun);
    const status = String(e.status ?? "?");
    const progress = progressSuffix(e.progress, " · ");
    return {
      title: `${name} · ${String(execId() ?? "").slice(0, 12)}`,
      statusLine: `status: ${status}${progress}`,
    };
  });

  return (
    <Show when={execId()}>
      <Floating
        position="absolute"
        width="72%"
        left="14%"
        top="8%"
        flexDirection="column"
        padding={2}
      >
        <text fg={palette.accent}>workflow execution — Esc closes · T traces · x cancel</text>
        <box height={1} marginTop={1} />
        <Show when={header()} fallback={<text fg={palette.fgDim}>loading execution…</text>}>
          <box height={1} overflow="hidden">
            <text fg={palette.fg}>{header()!.title}</text>
          </box>
          <box height={1} overflow="hidden">
            <text fg={palette.fg}>{header()!.statusLine}</text>
          </box>
        </Show>
        <scrollbox flexGrow={1} scrollY overflow="hidden">
          <Show when={dagModel()}>
            {(dag) => (
              <>
                <box height={1} marginTop={1}>
                  <text fg={palette.accent2}>{dag().header}</text>
                </box>
                <For each={dag().lines}>
                  {(line) => (
                    <box height={1} overflow="hidden">
                      <text fg={palette.fg}>{line}</text>
                    </box>
                  )}
                </For>
              </>
            )}
          </Show>
          <box height={1} marginTop={1}>
            <text fg={palette.accent2}>steps</text>
          </box>
          <Show
            when={rows().length > 0}
            fallback={<text fg={palette.fgDim}>  (no step rows on this execution yet)</text>}
          >
            <For each={rows()}>{(row) => <StepLine row={row} />}</For>
          </Show>
        </scrollbox>
        <box height={1} marginTop={1}>
          <text fg={palette.fgDim}>
            {cost().measuredGpuSeconds > 0
              ? `cost (sacct-measured): gpu ${cost().measuredGpuSeconds.toFixed(1)}s${cost().unmeasured > 0 ? ` · ${cost().unmeasured} completed step(s) unmeasured` : ""}`
              : cost().unmeasured > 0
                ? `cost: ${cost().unmeasured} completed step(s) unmeasured (sacct stamp absent — never approximated)`
                : ""}
          </text>
        </box>
      </Floating>
    </Show>
  );
}
