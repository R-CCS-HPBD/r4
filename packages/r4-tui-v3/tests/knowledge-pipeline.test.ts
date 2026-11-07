/**
 * P3 pins: build-pipeline stage derivation (pure) + cited drill-in action.
 */
import { describe, it, expect, beforeEach } from "bun:test";
import { pipelineStages, pipelineNextAction } from "../src/lib/knowledge-pipeline";
import { formatQuotaLine } from "../src/lib/governance-quota";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";

beforeEach(() => {
  resetWorkspace();
});

describe("knowledge pipeline stages", () => {
  it("all pending by default; next = plan", () => {
    const stages = pipelineStages({
      hasPlan: false, hasIngestion: false, hasExtraction: false, hasCoverage: false, hasGraph: false,
      runningJobTypes: [],
    });
    expect(stages.every((s) => s.state === "pending")).toBe(true);
    expect(pipelineNextAction(stages)).toBe("plan");
  });

  it("running ingest job marks ingest running (not done)", () => {
    const stages = pipelineStages({
      hasPlan: true, hasIngestion: false, hasExtraction: false, hasCoverage: false, hasGraph: false,
      runningJobTypes: ["KNOWLEDGE_INGEST"],
    });
    expect(stages.find((s) => s.name === "ingest")?.state).toBe("running");
    expect(pipelineNextAction(stages)).toBe("ingest");
  });

  it("done artifacts fill their stages; graph last", () => {
    const stages = pipelineStages({
      hasPlan: true, hasIngestion: true, hasExtraction: true, hasCoverage: true, hasGraph: false,
      runningJobTypes: [],
    });
    expect(stages.filter((s) => s.state === "done").length).toBe(4);
    expect(pipelineNextAction(stages)).toBe("graph");
  });

  it("all done → no next action", () => {
    const stages = pipelineStages({
      hasPlan: true, hasIngestion: true, hasExtraction: true, hasCoverage: true, hasGraph: true,
      runningJobTypes: [],
    });
    expect(pipelineNextAction(stages)).toBeNull();
  });
});

describe("quota gauge line (D3)", () => {
  it("null without quotas; formats counts with overflow suffix", () => {
    expect(formatQuotaLine(null)).toBeNull();
    expect(formatQuotaLine({ quotas: {} } as never)).toBeNull();
    const line = formatQuotaLine({
      quotas: {
        quotas: [
          { project_id: "p1", resource_type: "endpoints", current_count: 2, max_count: 4 },
          { project_id: "p1", resource_type: "models", current_count: 6, max_count: 8 },
          { project_id: "p2", resource_type: "jobs", current_count: 0, max_count: 1 },
          { project_id: "p9", resource_type: "extra", current_count: 0, max_count: 0 },
        ],
      },
    } as never);
    expect(line).toBe("quota p1/endpoints 2/4 · p1/models 6/8 · p2/jobs 0/1 · +1 more");
  });
});

describe("cited drill-in", () => {
  it("selects the artifact when the local catalog knows it", () => {
    setWorkspace("knowledge", [{ id: "art-1", name: "paper-a", documentCount: 2 } as never]);
    actions.openCitedInKnowledge("art-1");
    expect(workspace.paneView).toBe("knowledge");
    expect(workspace.selectedObjectId).toBe("art-1");
  });

  it("opens the pane with an honest note when the artifact is unknown", () => {
    actions.openCitedInKnowledge("ghost-9");
    expect(workspace.paneView).toBe("knowledge");
    expect(workspace.selectedObjectId).not.toBe("ghost-9");
    const note = workspace.chatMessages.at(-1);
    expect(String(note?.content ?? "")).toContain("not in the local catalog");
  });
});
