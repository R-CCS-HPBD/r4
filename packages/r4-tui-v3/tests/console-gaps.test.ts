/**
 * Project status + knowledge verify helpers.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { OfflineDataSource } from "../src/adapters/offline";
import { buildProjectStatusLines } from "../src/lib/project-status";
import { mapVerifyResponse } from "../src/lib/knowledge-verify";
import { applyPollEventSideEffects } from "../src/lib/poll-event-effects";
import { buildHealthReportLines } from "../src/lib/health-report";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentRuntime, agentActions } from "../src/stores/agent-runtime";
import { seedAgentRuntimeDemo } from "../src/stores/agent-runtime-demo";
import { knowledgeStore } from "../src/stores/knowledge";
import { demoProject } from "../src/adapters/fixtures";

beforeEach(() => {
  resetWorkspace();
});

describe("buildProjectStatusLines", () => {
  it("includes demo fixture snapshot for offline source", async () => {
    const source = new OfflineDataSource();
    const lines = await buildProjectStatusLines(demoProject.id, demoProject.id, source);
    expect(lines.join("\n")).toContain("Ingestion:");
    expect(lines.join("\n")).toContain("demo fixture snapshot");
    expect(lines.join("\n")).not.toContain("unavailable");
  });
});

describe("mapVerifyResponse", () => {
  it("maps snake_case and camelCase verify payloads", () => {
    const mapped = mapVerifyResponse("Why?", {
      answer: "Because evidence.",
      confidence: 0.8,
      reasoning_path: ["step 1"],
      evidence_ids: ["E1"],
    });
    expect(mapped.answer).toBe("Because evidence.");
    expect(mapped.confidence).toBe(0.8);
    expect(mapped.reasoningPath).toEqual(["step 1"]);
    expect(mapped.evidenceIds).toEqual(["E1"]);
  });

  it("maps CLI evidence objects onto evidenceIds", () => {
    const mapped = mapVerifyResponse("yield", {
      answer: "Catalog match: batch-3-yields.csv",
      confidence: 0.3,
      evidence: [{ source_name: "batch-3-yields.csv", source_id: "dataset-1" }],
    });
    expect(mapped.evidenceIds).toEqual(["batch-3-yields.csv"]);
  });
});

describe("buildHealthReportLines", () => {
  it("reports offline connection state", async () => {
    const source = new OfflineDataSource();
    actions.setDataSource(source);
    actions.setDemoMode(true);
    actions.setConnection({ gateway: "connected", umrs: "connected", hpc: "connected" });
    const lines = await buildHealthReportLines();
    expect(lines.join("\n")).toContain("demo/offline");
    expect(lines.join("\n")).toContain("GW: connected");
    expect(lines.join("\n")).toContain("Demo fixtures");
  });
});

describe("/health slash command", () => {
  it("is available offline", async () => {
    resetSlashCatalogCache();
    const previousBackend = process.env.R4_TUI_BACKEND;
    process.env.R4_TUI_BACKEND = "offline";
    const { setDemoMode } = await import("../src/lib/console-mode");
    try {
      setDemoMode(true);
      const result = await dispatchSlashCommand("/health");
      expect(result.kind).toBe("handled");
    } finally {
      setDemoMode(false);
      if (previousBackend === undefined) delete process.env.R4_TUI_BACKEND;
      else process.env.R4_TUI_BACKEND = previousBackend;
      resetSlashCatalogCache();
    }
  });
});

describe("seedAgentRuntimeDemo", () => {
  it("syncs mission control active agent count with runtime agents", () => {
    agentActions.resetAgentRuntime();
    seedAgentRuntimeDemo();
    expect(agentRuntime.agents.filter((a) => a.state === "running").length).toBe(3);
    expect(agentRuntime.missionControl.activeAgents.length).toBe(3);
    expect(agentRuntime.missionControl.pendingApprovals.length).toBe(1);
  });
});

describe("applyPollEventSideEffects", () => {
  it("notifies on endpoint-ready and job-failed", () => {
    resetWorkspace();
    applyPollEventSideEffects([
      {
        id: "e1",
        timestamp: new Date().toISOString(),
        type: "job",
        actor: "backend",
        action: "endpoint-ready",
        detail: "llama-3 endpoint",
      },
      {
        id: "e2",
        timestamp: new Date().toISOString(),
        type: "job",
        actor: "backend",
        action: "job-failed",
        detail: "OOM",
      },
    ]);
    expect(workspace.notifications.length).toBeGreaterThanOrEqual(2);
  });
});

describe("store backend actions", () => {
  it("verifyKnowledge uses dataSource.verifyKnowledge in offline mode", async () => {
    const source = new OfflineDataSource();
    actions.setProject(demoProject);
    actions.setDataSource(source);
    actions.setDemoMode(true);
    await actions.verifyKnowledge("Why is catalyst X promising?");
    expect(knowledgeStore.verification?.answer).toContain("knowledge graph");
    expect(knowledgeStore.verification?.confidence).toBeGreaterThan(0);
  });

  it("showJobLogs appends terminal lines", async () => {
    const source = new OfflineDataSource();
    actions.setDataSource(source);
    await actions.showJobLogs("job-1");
    expect(workspace.terminalLines.length).toBeGreaterThan(0);
    expect(workspace.terminalLines[0]).toContain("job-1");
  });
});
