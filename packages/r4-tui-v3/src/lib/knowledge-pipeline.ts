/**
 * Knowledge build pipeline stage derivation (P3).
 * Pure: client-known artifacts only (ingestion/extraction/coverage records,
 * loaded graph content, running ingest jobs) — never invented progress.
 */

export type PipelineStageName = "plan" | "ingest" | "extract" | "coverage" | "graph";
export type PipelineStageState = "done" | "running" | "pending";

export interface PipelineStage {
  name: PipelineStageName;
  state: PipelineStageState;
}

export interface PipelineInput {
  hasPlan: boolean;
  hasIngestion: boolean;
  hasExtraction: boolean;
  hasCoverage: boolean;
  hasGraph: boolean;
  /** Job types currently running/queued (e.g. KNOWLEDGE_INGEST). */
  runningJobTypes: string[];
}

export function pipelineStages(input: PipelineInput): PipelineStage[] {
  const running = (re: RegExp) => input.runningJobTypes.some((t) => re.test(t));
  const mk = (name: PipelineStageName, done: boolean, run: boolean): PipelineStage => ({
    name,
    state: run && !done ? "running" : done ? "done" : "pending",
  });
  return [
    mk("plan", input.hasPlan, false),
    mk("ingest", input.hasIngestion, running(/ingest|knowledge/i)),
    mk("extract", input.hasExtraction, running(/extract/i)),
    mk("coverage", input.hasCoverage, false),
    mk("graph", input.hasGraph, false),
  ];
}

/** First not-done stage — the "next action" anchor (null when all done). */
export function pipelineNextAction(stages: PipelineStage[]): PipelineStageName | null {
  const next = stages.find((s) => s.state !== "done");
  return next ? next.name : null;
}
