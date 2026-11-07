/**
 * Map persisted `r4 project` artifacts (list JSON, manifest, plan file)
 * to Console v3 types. Read-only — no pipeline command output.
 */

import type {
  IngestionResult,
  KnowledgeObject,
  ResearchPlan,
  ResearchProject,
  ResearchTask,
} from "../types";

export interface ProjectListEntry {
  project_id: string;
  name?: string | null;
  active?: boolean;
  gateway_url?: string | null;
  model?: string | null;
  metadata?: Record<string, unknown>;
}

export function mapProjectListEntry(entry: ProjectListEntry): ResearchProject {
  return {
    id: entry.project_id,
    name: entry.name?.trim() || entry.project_id,
    description: "",
    status: "active",
    goalIds: [],
  };
}

export function pickActiveProject(entries: ProjectListEntry[]): ResearchProject | null {
  if (!Array.isArray(entries) || entries.length === 0) return null;
  const current = entries.find((e) => e.active) ?? entries[0];
  return mapProjectListEntry(current);
}

/** Manifest length only — artifacts ingested, not yet understood. */
export function mapManifestToIngestion(
  projectId: string,
  manifest: Record<string, unknown>[],
): IngestionResult {
  const count = manifest.length;
  return {
    projectId,
    filesProcessed: count,
    extractedCount: 0,
    graphNodes: 0,
    status: "pending",
    errors: [],
  };
}

/** First non-nullish value, else the fallback — `??` chains without inline branching. */
function firstDefined<T>(fallback: T, ...values: unknown[]): T {
  for (const value of values) {
    if (value !== null && value !== undefined) return value as T;
  }
  return fallback;
}

function artifactName(entry: Record<string, unknown>): string {
  for (const key of ["filename", "path", "name", "title", "artifact_id"]) {
    const value = entry[key];
    if (typeof value === "string" && value.trim()) {
      const base = value.replace(/\\/g, "/").split("/").pop();
      return (base && base.trim()) || value;
    }
  }
  return "artifact";
}

const ARTIFACT_STATUSES: ReadonlySet<string> = new Set([
  "empty",
  "indexing",
  "ready",
  "stale",
  "error",
]);

function artifactStatus(entry: Record<string, unknown>): KnowledgeObject["status"] {
  const raw = String(firstDefined("ready", entry.status, entry.state)).toLowerCase();
  if (!ARTIFACT_STATUSES.has(raw)) return "ready";
  return raw as KnowledgeObject["status"];
}

/** `embedding_status` → pane status: retrieval finds `current` rows. */
const EMBEDDING_STATUS: Readonly<Record<string, KnowledgeObject["status"]>> = {
  current: "ready",
  pending: "indexing",
  stale: "stale",
  failed: "error",
};

function stringsOf(rows: Record<string, unknown>[], key: string): string[] {
  const values = rows.map((row) => String(row[key] ?? "").trim()).filter(Boolean);
  return [...new Set(values)].sort();
}

/** How a contribution's origin shows beside its contributor (K-1 tiers;
 * the same marks as `r4 knowledge ls`). */
const ORIGIN_MARK: Record<string, string> = {
  agent: " (agent)",
  agent_authored_user_kept: " (kept)",
  agent_authored_user_vouched: " (vouched)",
};

/** Each contributor with how they added it: `alice (kept)`. */
function contributorsOf(contributions: Record<string, unknown>[]): string[] {
  const marked = contributions
    .filter((c) => String(c.user_id ?? "").trim())
    .map((c) => `${String(c.user_id).trim()}${ORIGIN_MARK[String(c.origin_actor ?? "")] ?? ""}`);
  return [...new Set(marked)].sort();
}

function serverTitle(row: Record<string, unknown>, contributions: Record<string, unknown>[]): string {
  const titled = contributions.find((c) => typeof c.title === "string" && c.title.trim());
  if (titled) return String(titled.title);
  const meta = (row.metadata ?? {}) as Record<string, unknown>;
  return artifactName({ ...meta, artifact_id: row.artifact_id });
}

/**
 * K1.3: the server's artifact list (`GET /k2/artifacts`: the UMRS spine
 * through the graph-readable predicate) → Knowledge pane rows, with who
 * added each one. Replaces the local `artifact_manifest.json`, which a
 * durable import never wrote (the pane stayed empty after every upload).
 */
export function mapArtifactsToKnowledgeObjects(rows: unknown): KnowledgeObject[] {
  if (!Array.isArray(rows)) return [];
  return (rows as Record<string, unknown>[]).map(mapArtifactRow);
}

function mapArtifactRow(row: Record<string, unknown>): KnowledgeObject {
  const contributions = Array.isArray(row.contributions) ? (row.contributions as Record<string, unknown>[]) : [];
  return {
    id: String(firstDefined("", row.artifact_id)),
    name: serverTitle(row, contributions),
    // An unknown or empty state is not "ready" (the CLI shows "?").
    status: EMBEDDING_STATUS[String(firstDefined("", row.embedding_status))] ?? "indexing",
    documentCount: 1,
    contributors: contributorsOf(contributions),
    graphs: stringsOf(contributions, "graph_id"),
    kind: String(firstDefined("", row.kind)),
    addedAt: String(firstDefined("", row.last_added, row.created_at)),
  };
}

/** The pane's row label: the name and who added it (server rows), else the
 * old document count. */
export function knowledgeRowLabel(k: KnowledgeObject): string {
  const who = k.contributors?.join(", ");
  return who ? `${k.name} · ${who}` : `${k.name} (${k.documentCount} docs)`;
}

/** One catalog row per ingested artifact — live F4 list, not demo fixtures. */
export function mapManifestToKnowledgeObjects(
  manifest: Record<string, unknown>[],
): KnowledgeObject[] {
  return manifest.map((entry, index) => {
    const raw = String(entry.artifact_id ?? entry.id ?? "").trim();
    const id = raw || `artifact-${index + 1}`;
    return {
      id,
      name: artifactName(entry),
      status: artifactStatus(entry),
      documentCount: 1,
    };
  });
}

const PLAN_STATUSES: ReadonlySet<string> = new Set([
  "draft",
  "proposed",
  "approved",
  "executing",
  "completed",
  "failed",
  "cancelled",
]);

function mapPlanStatus(status: unknown): ResearchPlan["status"] {
  const value = String(firstDefined("draft", status)).toLowerCase();
  if (!PLAN_STATUSES.has(value)) return "draft";
  return value as ResearchPlan["status"];
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value as Record<string, unknown>[];
}

function mapObjectiveEntry(obj: Record<string, unknown>): ResearchPlan["objectives"][number] {
  return {
    id: String(firstDefined("", obj.objective_id)),
    type: String(firstDefined("understanding", obj.objective_type)),
    description: String(firstDefined("", obj.description, obj.title)),
    status: "pending",
  };
}

function mapHypothesisEntry(hyp: Record<string, unknown>): ResearchPlan["hypotheses"][number] {
  return {
    id: String(firstDefined("", hyp.hypothesis_id)),
    statement: String(firstDefined("", hyp.statement)),
    confidence: Number(firstDefined(0.5, hyp.confidence)),
    status: "pending",
  };
}

function mapExperimentEntry(exp: Record<string, unknown>): ResearchPlan["experiments"][number] {
  const resources = (exp.required_resources ?? {}) as Record<string, unknown>;
  return {
    id: String(firstDefined("", exp.experiment_id)),
    title: String(firstDefined("", exp.title)),
    status: "pending",
    estimatedGpus: Number(firstDefined(0, resources.gpus, resources.max_gpus)),
  };
}

export function mapResearchPlanFile(data: Record<string, unknown>): ResearchPlan {
  return {
    planId: String(firstDefined("plan", data.plan_id)),
    projectId: String(firstDefined("", data.project_id)),
    researchGoal: String(firstDefined("", data.research_goal, data.title)),
    objectives: asRecordArray(data.objectives).map(mapObjectiveEntry),
    hypotheses: asRecordArray(data.hypotheses).map(mapHypothesisEntry),
    experiments: asRecordArray(data.experiments).map(mapExperimentEntry),
    status: mapPlanStatus(data.status),
    createdAt: String(firstDefined(new Date().toISOString(), data.created_at)),
  };
}

export function mapResearchPlanToTasks(plan: ResearchPlan): ResearchTask[] {
  const tasks: ResearchTask[] = [];
  for (const objective of plan.objectives) {
    if (!objective.id) continue;
    tasks.push({
      id: objective.id,
      goalId: plan.projectId,
      title: objective.description.slice(0, 80) || objective.type,
      status: "pending",
    });
  }
  for (const experiment of plan.experiments) {
    if (!experiment.id) continue;
    tasks.push({
      id: experiment.id,
      goalId: plan.projectId,
      title: experiment.title,
      status: "pending",
    });
  }
  return tasks;
}

// ── ADR-025 research sessions ────────────────────────────────────────────

import type { ResearchSession } from "../types";

function mapSessionLoopLink(lnk: Record<string, unknown>) {
  return {
    loopId: String(firstDefined("", lnk.loop_id, lnk.loopId)),
    venue: String(firstDefined("umrs", lnk.venue)),
    loopRole: String(firstDefined("auto", lnk.loop_role)),
    attachedAt: String(firstDefined("", lnk.attached_at)),
  };
}

function mapSessionLoops(row: Record<string, unknown>): ResearchSession["loops"] {
  if (!Array.isArray(row.loops)) return [];
  return (row.loops as Array<Record<string, unknown>>).map(mapSessionLoopLink);
}

/** Map an UMRS /research/sessions row onto the console ResearchSession shape. */
export function mapSessionRow(row: Record<string, unknown>): ResearchSession {
  return {
    sessionId: String(firstDefined("", row.session_id, row.sessionId)),
    projectId: String(firstDefined("", row.project_id, row.projectId)),
    title: String(firstDefined("", row.title)),
    researchGoal: String(firstDefined("", row.research_goal)),
    status: firstDefined("active", row.status) as ResearchSession["status"],
    authoritativeRuntime: firstDefined(
      "umrs",
      row.authoritative_runtime,
    ) as ResearchSession["authoritativeRuntime"],
    createdAt: String(firstDefined("", row.created_at)),
    updatedAt: String(firstDefined("", row.updated_at)),
    archivedAt: (row.archived_at as string | null | undefined) ?? null,
    loops: mapSessionLoops(row),
  };
}
