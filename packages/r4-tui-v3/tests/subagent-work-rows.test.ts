/** S1: ordinary subagent lifecycle rows correlate by backend agent_id. */
import { beforeEach, describe, expect, test } from "bun:test";
import {
  onBridgeEvent,
  resetCompanionRuntimeBridgeForTests,
} from "../src/bridge/companion-runtime-bridge";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { resetWorkspace } from "../src/stores";

beforeEach(() => {
  resetWorkspace();
  resetCompanionRuntimeBridgeForTests();
  agentActions.resetAgentRuntime();
});

function spawn(agentId: string, task = "inspect", followUp = false, turn = 0): void {
  onBridgeEvent({
    type: "agent_spawn",
    message: `Delegating ${task}`,
    data: {
      agent_id: agentId,
      child_session_id: `child-${agentId}`,
      parent_session_id: "parent-session",
      parent_agent_id: "parent-agent",
      task,
      model: "child-model",
      follow_up: followUp,
      subagent_turn: turn,
    },
  });
}

function result(agentId: string, status = "completed", turn = 0): void {
  onBridgeEvent({
    type: "agent_result",
    message: status === "completed" ? "answer" : status,
    data: {
      agent_id: agentId,
      child_session_id: `child-${agentId}`,
      parent_session_id: "parent-session",
      parent_agent_id: "parent-agent",
      status,
      result_preview: status === "completed" ? "answer" : "",
      error: status === "completed" ? "" : status,
      subagent_turn: turn,
    },
  });
}

describe("subagent work rows", () => {
  test("spawn and result upsert one backend-keyed row with child lineage", () => {
    spawn("backend-1", "read a file");
    result("backend-1");
    result("backend-1");

    expect(agentRuntime.agents).toHaveLength(1);
    const row = agentRuntime.agents[0]!;
    expect(row.id).toBe("backend-1");
    expect(row.state).toBe("completed");
    expect(row.sessionId).toBe("child-backend-1");
    expect(row.parentSessionId).toBe("parent-session");
    expect(row.parentAgentId).toBe("parent-agent");
    expect(row.currentTask).toBe("read a file");
  });

  test("result before spawn is defensive and a late spawn stays terminal", () => {
    result("early-1", "failed");
    spawn("early-1", "late registration");
    spawn("early-1", "duplicate registration");

    expect(agentRuntime.agents).toHaveLength(1);
    expect(agentRuntime.agents[0]!.id).toBe("early-1");
    expect(agentRuntime.agents[0]!.state).toBe("failed");
    expect(agentRuntime.agents[0]!.currentTask).toBe("late registration");
  });

  test("a follow-up reopens one row and its failed result is not discarded", () => {
    spawn("follow-1", "initial task");
    result("follow-1", "completed");
    spawn("follow-1", "follow-up task", true, 1);

    expect(agentRuntime.agents).toHaveLength(1);
    expect(agentRuntime.agents[0]!.state).toBe("running");
    expect(agentRuntime.agents[0]!.currentTask).toBe("follow-up task");

    result("follow-1", "failed", 1);
    result("follow-1", "failed", 1);

    expect(agentRuntime.agents).toHaveLength(1);
    expect(agentRuntime.agents[0]!.state).toBe("failed");
    expect(agentRuntime.agents[0]!.subagentTurn).toBe(1);
    expect(agentRuntime.agents[0]!.errorMessage).toBe("failed");
  });

  test("a late spawn cannot reopen its already-terminal numbered turn", () => {
    spawn("late-follow-1", "initial task");
    result("late-follow-1", "completed");
    result("late-follow-1", "failed", 1);
    spawn("late-follow-1", "late follow-up spawn", true, 1);

    expect(agentRuntime.agents).toHaveLength(1);
    expect(agentRuntime.agents[0]!.state).toBe("failed");
    expect(agentRuntime.agents[0]!.subagentTurn).toBe(1);
    expect(agentRuntime.agents[0]!.currentTask).toBe("late follow-up spawn");
  });

  test("children, terminal states and missing identities never merge", () => {
    spawn("child-a");
    spawn("child-b");
    result("child-a", "cancelled");
    result("child-b", "error");
    spawn("");
    result("");

    expect(agentRuntime.agents).toHaveLength(2);
    expect(agentActions.getAgentById("child-a")!.state).toBe("cancelled");
    expect(agentActions.getAgentById("child-b")!.state).toBe("failed");
  });

  test("the runtime factory honors a backend id and still generates other ids", () => {
    const base = {
      role: "companion" as const,
      goal: "test",
      budget: {},
      permissions: { egress: "none" as const, filesystem: "read_only" as const, compute: "none" as const },
    };
    const supplied = agentActions.createAgent({ ...base, id: "backend-fixed" });
    const generated = agentActions.createAgent(base);

    expect(supplied).toBe("backend-fixed");
    expect(generated).not.toBe("backend-fixed");
    expect(generated).toHaveLength(26);
  });
});
