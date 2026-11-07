/**
 * Build /project status lines from bridge + data source snapshot.
 */

import type { DataSource } from "../adapters/types";

export async function buildProjectStatusLines(
  projectId: string,
  currentProjectId: string | null | undefined,
  source: DataSource | null | undefined,
): Promise<string[]> {
  const lines = [
    `Project: ${projectId}`,
    `Current: ${currentProjectId === projectId ? "yes" : "no"}`,
  ];

  if (!source?.pollProjectKnowledge) {
    lines.push("Snapshot unavailable — no project knowledge reader on this backend.");
    return lines;
  }

  const snap = await source.pollProjectKnowledge(projectId);
  if (snap.ingestion) {
    lines.push(
      `Ingestion: ${snap.ingestion.filesProcessed} files, ${snap.ingestion.graphNodes} graph nodes (${snap.ingestion.status})`,
    );
  } else {
    // K1.3: the server's knowledge list, not a local import manifest.
    const n = snap.knowledge.length;
    lines.push(n > 0 ? `Knowledge: ${n} items (r4 knowledge ls)` : "Knowledge: none yet (/rag upload <file>)");
  }
  if (snap.plan) {
    lines.push(
      `Plan: ${snap.plan.status} — ${snap.plan.objectives.length} objectives, ${snap.plan.hypotheses.length} hypotheses`,
    );
  } else {
    lines.push("Plan: none (run /project plan <id> <goal>)");
  }
  lines.push(`Tasks from plan: ${snap.tasks.length}`);
  if (source.name === "offline") {
    lines.push("(demo fixture snapshot)");
  }
  return lines;
}
