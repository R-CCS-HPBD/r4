/**
 * Bridge results → the webview's shapes for VP-2b (ADR-103): loaded messages
 * with their turn ids, citations, the session's changed files and its plan.
 * Pure; the host posts what these return.
 */

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}

/** A snapshot message; `turnId` is its ADR-057 turn (metadata.turn_id). */
export function toViewMessage(message: unknown): { role: string; content: string; turnId?: string } {
  const m = record(message);
  const turnId = str(record(m.metadata).turn_id);
  return { role: str(m.role), content: str(m.content), ...(turnId ? { turnId } : {}) };
}

export interface ViewCitation {
  sid: string;
  title: string;
  artifactId: string;
  sourceId: string;
  projectId: string;
  preview: string;
}

/** `send_message` citations (K1.4): the passages a reply cited. */
export function toCitations(value: unknown): ViewCitation[] {
  return rows(value).map((c) => ({
    sid: str(c.sid),
    title: str(c.title) || str(c.artifact_id) || "source",
    artifactId: str(c.artifact_id),
    sourceId: str(c.source_id),
    projectId: str(c.project_id),
    preview: str(c.preview),
  }));
}

export interface ViewChangedFile {
  path: string;
  inserted: number;
  deleted: number;
  created: boolean;
  reverted: boolean;
}

export interface ChangeContent {
  before: string;
  after: string;
}

/** `get_session_changes` → the card's rows, plus each file's two sides for the diff editor. */
export function toChangedFiles(value: unknown): { files: ViewChangedFile[]; contents: Map<string, ChangeContent> } {
  const contents = new Map<string, ChangeContent>();
  const files = rows(record(value).changes).map((c) => {
    const path = str(c.path);
    contents.set(path, { before: str(c.before), after: str(c.after) });
    return {
      path,
      inserted: Number(c.inserted ?? 0),
      deleted: Number(c.deleted ?? 0),
      created: c.before_existed === false,
      reverted: Boolean(c.reverted),
    };
  });
  return { files: files.filter((f) => f.path), contents };
}

export interface ViewPlanStep {
  id: string;
  title: string;
  status: string;
  depth: number;
}

/** `get_session_plan` task rows, children under their parent. */
export function toPlanSteps(value: unknown): ViewPlanStep[] {
  const steps = rows(record(value).steps);
  const byId = new Map(steps.map((s) => [str(s.id), s]));
  const depthOf = (step: Record<string, unknown>, seen = new Set<string>()): number => {
    const parent = str(step.parent_id);
    if (!parent || seen.has(parent) || !byId.has(parent)) return 0;
    seen.add(parent);
    return 1 + depthOf(byId.get(parent)!, seen);
  };
  return steps
    .map((s) => ({ id: str(s.id), title: str(s.title), status: str(s.status) || "pending", depth: depthOf(s) }))
    .filter((s) => s.title);
}
