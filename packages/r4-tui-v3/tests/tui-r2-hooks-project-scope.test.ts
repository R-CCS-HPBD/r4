/**
 * TUI audit r2 (2026-10-17), hooks lane — findings 47/65 (mission control
 * keeps the previous project's goal/hypotheses/blockers), 55 (science store
 * keeps the previous project's graph/proposals) and 48 (deleting the tab's
 * project puts projects.json's first entry on the tab).
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { createRoot } from "solid-js";
import { resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { scienceActions, scienceStore } from "../src/stores/science";
import {
  _resetAppliedProjectForTests,
  applyProjectKnowledgePoll,
  createKnowledgePoller,
  stopKnowledgePoller,
} from "../src/hooks/useKnowledge";
import { refreshAfterProjectConfigChange } from "../src/hooks/project-refresh";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import type { DataSource, ProjectKnowledgePollResult } from "../src/adapters/types";

function project(id: string) {
  return { id, name: id, description: "", status: "active" as const, goalIds: [] };
}

function poll(plan: ProjectKnowledgePollResult["plan"]): ProjectKnowledgePollResult {
  return {
    knowledge: [],
    tasks: [],
    ingestion: null,
    coverage: null,
    readiness: null,
    plan,
    loop: null,
    sessions: null,
  };
}

const planA = {
  planId: "pA",
  projectId: "A",
  researchGoal: "Goal of A",
  objectives: [],
  hypotheses: [{ id: "h1", statement: "A's hypothesis", confidence: 0.6, status: "proposed" }],
  experiments: [],
  status: "proposed",
  createdAt: new Date().toISOString(),
} as unknown as ProjectKnowledgePollResult["plan"];

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  resetWorkspace();
  stopKnowledgePoller();
  _resetAppliedProjectForTests();
  scienceActions.reset();
  agentActions.updateMissionControl({ researchGoal: "", hypotheses: [], blockers: [] });
});

afterEach(() => {
  stopKnowledgePoller();
  setCompanionBridgeForTests(null);
});

describe("project switch clears project-scoped state", () => {
  it("a poll for another project clears the previous goal, hypotheses and blockers", () => {
    applyProjectKnowledgePoll(poll(planA), "A");
    agentActions.updateMissionControl({
      blockers: [{ id: "b1", description: "A blocker", severity: "warning" }],
    } as never);
    expect(agentRuntime.missionControl.researchGoal).toBe("Goal of A");

    applyProjectKnowledgePoll(poll(null), "B");
    expect(agentRuntime.missionControl.researchGoal).toBe("");
    expect(agentRuntime.missionControl.hypotheses).toHaveLength(0);
    expect(agentRuntime.missionControl.blockers).toHaveLength(0);
  });

  it("a poll for another project drops the previous project's server graph", async () => {
    setWorkspace("platform", {
      graphContent: async () => ({ graph_id: "gA", nodes: [{ id: "n1", label: "A node" }], edges: [] }),
    } as never);
    applyProjectKnowledgePoll(poll(planA), "A");
    await scienceActions.loadGraphContent("gA");
    expect(scienceStore.graphContent).not.toBeNull();
    expect(scienceStore.selectedGraphId).toBe("gA");

    applyProjectKnowledgePoll(poll(null), "B");
    expect(scienceStore.graphContent).toBeNull();
    expect(scienceStore.selectedGraphId).toBeNull();
  });

  it("a project change (tab switch) re-polls at once and clears the old project's state", async () => {
    const polled: string[] = [];
    const source = {
      name: "r4-cli",
      pollProjectKnowledge: async (id: string) => {
        polled.push(id);
        return poll(id === "A" ? planA : null);
      },
    } as unknown as DataSource;
    setWorkspace("project", project("A"));
    createRoot(() => createKnowledgePoller(source, 60_000));
    await flush();
    await flush();
    expect(agentRuntime.missionControl.researchGoal).toBe("Goal of A");

    setWorkspace("project", project("B")); // what a tab snapshot restore does
    expect(agentRuntime.missionControl.researchGoal).toBe("");
    await flush();
    await flush();
    expect(polled).toEqual(["A", "B"]);
  });
});

describe("refreshAfterProjectConfigChange after deleting the tab's project", () => {
  it("keeps the tab project-less when the bridge answers with no project", async () => {
    let cliSnapshots = 0;
    setCompanionBridgeForTests({
      request: async () => ({ current: null, project: null }),
    } as unknown as CompanionBridgeClient);
    setWorkspace("dataSource", {
      name: "r4-cli",
      loadWorkspace: async () => {
        cliSnapshots += 1;
        return { project: project("FIRST_IN_PROJECTS_JSON") };
      },
    } as never);
    setWorkspace("project", project("DELETED"));
    await refreshAfterProjectConfigChange();
    expect(cliSnapshots).toBe(0);
    expect(workspace.project).toBeNull();
  });

  it("still falls back to the CLI snapshot when the bridge is unreachable", async () => {
    let cliSnapshots = 0;
    setCompanionBridgeForTests({
      request: async () => {
        throw new Error("bridge down");
      },
    } as unknown as CompanionBridgeClient);
    setWorkspace("dataSource", {
      name: "r4-cli",
      loadWorkspace: async () => {
        cliSnapshots += 1;
        return { project: project("CLI_DEFAULT") };
      },
    } as never);
    await refreshAfterProjectConfigChange();
    expect(cliSnapshots).toBe(1);
    expect(workspace.project?.id).toBe("CLI_DEFAULT");
  });
});
