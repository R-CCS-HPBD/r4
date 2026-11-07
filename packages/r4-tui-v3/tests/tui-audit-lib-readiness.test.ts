/**
 * TUI audit 2026-10-17 (ts-lib lane, #60) — a finished project_readiness job
 * applies its own result (`r4 project readiness <id> --json`), scoped to the
 * job's project; it no longer refetches the unscoped server report and
 * raises a "not ready" gate for a ready project.
 */
import { describe, test, expect, beforeEach } from "bun:test";
import {
  handleProjectJobEvent,
  resetProjectJobEventsForTests,
  trackActiveProjectJob,
} from "../src/commands/project-job-events";
import { parseReadinessResult } from "../src/lib/readiness-sync";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { knowledgeActions, knowledgeStore } from "../src/stores/knowledge";

let readinessCalls = 0;

function readinessGates() {
  return agentRuntime.approvalGates.filter((g) => g.status === "pending" && g.objectId?.startsWith("readiness:"));
}

function finishReadiness(result: unknown, jobId = "job-r1") {
  trackActiveProjectJob(jobId, "readiness");
  handleProjectJobEvent({ jobId, operation: "readiness", phase: "done", message: "done", result });
}

beforeEach(() => {
  resetWorkspace();
  resetProjectJobEventsForTests();
  agentActions.resetAgentRuntime();
  knowledgeActions.setReadiness(null);
  readinessCalls = 0;
  actions.setDataSource({
    readiness: async () => {
      readinessCalls++;
      return { readiness_level: "BLOCKED" };
    },
  } as never);
});

describe("readiness job result", () => {
  test("a ready CLI report clears the gate and skips the refetch", async () => {
    finishReadiness({
      project_id: "p1",
      readiness_level: "ready",
      confidence_level: "high",
      critical_issues: 0,
      recommendation: "proceed",
      next_steps: [],
    });
    await Bun.sleep(10);
    expect(readinessCalls).toBe(0);
    expect(knowledgeStore.readiness?.ready).toBe(true);
    expect(readinessGates()).toEqual([]);
  });

  test("a not_ready report raises a gate for the job's project", async () => {
    finishReadiness({ project_id: "p2", readiness_level: "not_ready", critical_issues: 3, recommendation: "ingest more" });
    await Bun.sleep(10);
    expect(readinessCalls).toBe(0);
    expect(knowledgeStore.readiness?.ready).toBe(false);
    const gates = readinessGates();
    expect(gates.length).toBe(1);
    expect(gates[0]!.title).toContain("p2");
  });

  test("an error payload from the refetch is an error, not 'not ready'", async () => {
    actions.setDataSource({ readiness: async () => ({ error: "HTTP 503" }) } as never);
    // No result on the event: the current project's refetch is the fallback.
    setWorkspace("project", { id: "p3", name: "p3" } as never);
    finishReadiness(undefined, "job-r3");
    await Bun.sleep(20);
    expect(readinessGates()).toEqual([]);
    const card = workspace.chatMessages.find((m) => m.errorCard);
    expect(card?.errorCard?.detail).toContain("HTTP 503");
  });
});

describe("parseReadinessResult reads the CLI levels", () => {
  test.each([
    ["ready", true, "high"],
    ["partially_ready", true, "medium"],
    ["not_ready", false, "low"],
    ["blocked", false, "none"],
  ] as const)("%s → ready=%p level=%s", (level, ready, mapped) => {
    const parsed = parseReadinessResult({ readiness_level: level });
    expect(parsed.ready).toBe(ready);
    expect(parsed.readinessLevel).toBe(mapped);
  });

  test("explicit can_run still wins", () => {
    expect(parseReadinessResult({ can_run: false, readiness_level: "ready" }).ready).toBe(false);
  });
});
