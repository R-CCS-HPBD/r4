import { describe, expect, it } from "bun:test";
import {
  formatAgentMetrics,
  agentsEmptyMessage,
  agentsPanelExpanded,
} from "../src/components/agent-monitor";
import { formatTimelineLine } from "../src/components/research-timeline";
import { formatStepLine, formatTemplateTypes } from "../src/routes/loop";

describe("formatAgentMetrics", () => {
  it("renders prog and conf as one contiguous string", () => {
    expect(formatAgentMetrics({ progress: 45, confidence: 0.72 })).toBe("Prog 45% · Conf 72%");
  });

  it("omits missing fields", () => {
    expect(formatAgentMetrics({ progress: 68 })).toBe("Prog 68%");
    expect(formatAgentMetrics({ confidence: 0.58 })).toBe("Conf 58%");
  });
});

describe("agents panel density", () => {
  it("live empty copy never pushes Run demo", () => {
    expect(agentsEmptyMessage(false)).toBe("No agents yet");
    expect(agentsEmptyMessage(false).toLowerCase()).not.toContain("demo");
  });

  it("demo empty copy mentions demo", () => {
    expect(agentsEmptyMessage(true).toLowerCase()).toContain("demo");
  });

  it("collapses when empty and not focused; expands on focus or agents", () => {
    expect(agentsPanelExpanded([], "graph")).toBe(false);
    expect(agentsPanelExpanded([], "agents")).toBe(true);
    expect(agentsPanelExpanded([{ state: "running" }], "graph")).toBe(true);
  });
});

describe("formatTimelineLine", () => {
  it("keeps icon and title on one line without overlap markers", () => {
    const line = formatTimelineLine({
      timestamp: "2026-08-04T01:01:04.000Z",
      type: "knowledge_correction",
      title: "Corrected binding affinity",
    });
    expect(line).toContain("01:01:04");
    expect(line).toContain("knowledge correction");
    expect(line).toContain("Corrected binding affinity");
    expect(line).not.toMatch(/!knowledg/);
  });
});

describe("Research DAG formatting", () => {
  it("joins template types without overlap artifacts", () => {
    expect(formatTemplateTypes(["ingest", "extract", "hypothesis"])).toBe(
      "ingest → extract → hypothesis",
    );
  });

  it("formats a step on one line", () => {
    const line = formatStepLine(1, {
      status: "pending",
      label: "Ingest artifacts",
      type: "ingest",
      errors: [],
    });
    expect(line).toContain("#1");
    expect(line).toContain("Ingest artifacts");
    expect(line).toContain("(ingest)");
  });
});
