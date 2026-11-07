/**
 * Knowledge CLI mapper tests (read-only persisted artifacts).
 */

import { describe, it, expect } from "bun:test";
import {
  mapManifestToIngestion,
  mapManifestToKnowledgeObjects,
  mapResearchPlanFile,
  mapResearchPlanToTasks,
  pickActiveProject,
} from "../src/adapters/knowledge-mappers";

describe("knowledge-mappers", () => {
  it("picks the active project from list JSON", () => {
    const project = pickActiveProject([
      { project_id: "a", name: "Alpha", active: false },
      { project_id: "b", name: "Beta", active: true },
    ]);
    expect(project?.id).toBe("b");
    expect(project?.name).toBe("Beta");
  });

  it("returns null for empty or non-array list", () => {
    expect(pickActiveProject([])).toBeNull();
  });

  it("maps manifest artifacts to knowledge catalog rows", () => {
    const rows = mapManifestToKnowledgeObjects([
      { artifact_id: "a1", filename: "batch-3-yields.csv" },
      { artifact_id: "a2", path: "papers/gcmc-mof-paper-01.pdf" },
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      id: "a1",
      name: "batch-3-yields.csv",
      status: "ready",
      documentCount: 1,
    });
    expect(rows[1].name).toBe("gcmc-mof-paper-01.pdf");
  });

  it("maps manifest length to ingested-but-not-understood ingestion", () => {
    const ingestion = mapManifestToIngestion("demo", [{ artifact_id: "a" }, { artifact_id: "b" }]);
    expect(ingestion.filesProcessed).toBe(2);
    expect(ingestion.extractedCount).toBe(0);
    expect(ingestion.graphNodes).toBe(0);
    expect(ingestion.status).toBe("pending");
  });

  it("maps research plan file to tasks", () => {
    const plan = mapResearchPlanFile({
      plan_id: "plan-1",
      project_id: "demo",
      research_goal: "Study X",
      status: "draft",
      created_at: "2026-01-01T00:00:00Z",
      objectives: [{ objective_id: "obj-1", description: "Review literature", objective_type: "understanding" }],
      hypotheses: [],
      experiments: [{ experiment_id: "exp-1", title: "Run simulation" }],
    });
    const tasks = mapResearchPlanToTasks(plan);
    expect(tasks).toHaveLength(2);
    expect(tasks[0].title).toContain("Review literature");
    expect(tasks[1].title).toBe("Run simulation");
  });
});
