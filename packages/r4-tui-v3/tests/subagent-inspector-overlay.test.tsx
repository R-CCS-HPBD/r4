/** D-70 S4: Work-row → child inspector → parent/back navigation and real detail. */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { overlayCloseSweep } from "../src/keymap";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { setCompanionBridgeForTests, type CompanionBridgeClient } from "../src/bridge/jsonl-client";
import type { InspectSubagentResult } from "../src/bridge/rpc-types.gen";
import { AgentDetailOverlay } from "../src/components/agent-detail-overlay";
import { SubagentInspectorOverlay } from "../src/components/subagent-inspector-overlay";

function inspection(): InspectSubagentResult {
  return {
    session: {
      id: "child-a",
      name: "Subagent agent-a",
      created_at: "2026-11-10T00:00:00Z",
      updated_at: "2026-11-10T00:00:02Z",
      model: "child-model",
      session_kind: "subagent",
      parent_session_id: "parent-session",
      parent_agent_id: "parent-agent",
      agent_id: "agent-a",
      task: "read the actual child input",
      system_prompt: "the actual child system prompt",
      declared_tools: ["file_read"],
      execution_kind: "async",
      status: "completed",
      started_at: "2026-11-10T00:00:00Z",
      ended_at: "2026-11-10T00:00:02Z",
      terminal_error: "",
      follow_up_count: 0,
      venue: "local",
      event_log_error: "",
      messages: [
        { role: "user", content: "read the actual child input", metadata: { subagent_turn: 0 } },
        { role: "assistant", content: "the actual child response", metadata: { subagent_turn: 0 } },
      ],
    },
    session_id: "child-a",
    events: [
      {
        id: 1,
        session_id: "child-a",
        event_type: "tool_result",
        timestamp: "2026-11-10T00:00:01Z",
        message: "actual bounded tool output",
        data: { name: "file_read", result: "[REDACTED] tool result" },
        tool_name: "file_read",
        tool_status: "success",
        tool_duration_ms: 5,
        agent_id: "agent-a",
        parent_agent_id: "parent-agent",
        task_id: null,
        progress_pct: null,
        intent: null,
        rationale: null,
      },
    ],
    next_after_id: 1,
    has_more: false,
    total_events: 1,
    limit: 100,
    read_only: true,
    event_payload_cap: 8000,
    event_payloads_may_be_abridged: true,
    source: "local",
    remote_available: false,
    remote_reason: "",
  };
}

beforeEach(() => {
  resetWorkspace();
  agentActions.resetAgentRuntime();
  actions.setSessionId("parent-session");
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  actions.closeAllOverlays();
  agentActions.resetAgentRuntime();
});

describe("agent detail navigation", () => {
  it("opens a durable child from its Work-row detail and Esc returns to the parent detail", async () => {
    agentActions.createAgent({
      id: "agent-a",
      role: "companion",
      goal: "child goal",
      state: "completed",
      currentTask: "read the actual child input",
      sessionId: "child-a",
      parentSessionId: "parent-session",
      parentAgentId: "parent-agent",
      budget: {},
      permissions: { egress: "none", filesystem: "read_only", compute: "none" },
    });
    actions.setAgentDetailId("agent-a");
    const setup = await testRender(() => <AgentDetailOverlay />, { width: 120, height: 35 });
    try {
      await setup.renderOnce();
      expect(setup.captureCharFrame()).toContain("open session");
      setup.mockInput.pressKey("o");
      await setup.renderOnce();

      expect(workspace.subagentInspector?.sessionId).toBe("child-a");
      expect(workspace.subagentInspector?.agentId).toBe("agent-a");
      expect(workspace.agentDetailId).toBe("agent-a");
      expect(agentRuntime.agents).toHaveLength(1);

      expect(overlayCloseSweep()).toBe(true);
      expect(workspace.subagentInspector).toBeNull();
      expect(workspace.agentDetailId).toBe("agent-a");
      expect(workspace.paneView).toBeNull();

      expect(overlayCloseSweep()).toBe(true);
      expect(workspace.agentDetailId).toBeNull();
    } finally {
      setup.renderer.destroy();
    }
  });
});

describe("inspector rendering", () => {
  it("shows the child's own task, model, response and persisted tool event without a parent load", async () => {
    const calls: string[] = [];
    setCompanionBridgeForTests({
      request: async (method: string) => {
        calls.push(method);
        if (method !== "inspect_subagent") throw new Error(`live parent mutation attempted: ${method}`);
        return inspection();
      },
    } as unknown as CompanionBridgeClient);
    actions.openSubagentInspector({
      sessionId: "child-a",
      agentId: "agent-a",
      parentSessionId: "parent-session",
      parentAgentId: "parent-agent",
      venue: "local",
    });
    const setup = await testRender(() => <SubagentInspectorOverlay />, { width: 140, height: 48 });
    try {
      for (let index = 0; index < 5; index++) await setup.renderOnce();
      const frame = setup.captureCharFrame();
      expect(frame).toContain("read-only");
      expect(frame).toContain("child-model");
      expect(frame).toContain("read the actual child input");
      expect(frame).toContain("the actual child response");
      expect(frame).toContain("actual bounded tool output");
      expect(frame).toContain("b/Esc back");
      expect(calls).toEqual(["inspect_subagent"]);
      expect(workspace.sessionId).toBe("parent-session");
      expect(workspace.chatTabs).toHaveLength(1);
    } finally {
      setup.renderer.destroy();
    }
  });

  it("wires next/previous event pages and closes on an owner-tab switch", async () => {
    const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
    setCompanionBridgeForTests({
      request: async (method: string, params: Record<string, unknown>) => {
        calls.push({ method, params });
        return { ...inspection(), has_more: true, next_after_id: 1 };
      },
    } as unknown as CompanionBridgeClient);
    actions.openSubagentInspector({
      sessionId: "child-a",
      agentId: "agent-a",
      parentSessionId: "parent-session",
      parentAgentId: "parent-agent",
      venue: "local",
    });
    const setup = await testRender(() => <SubagentInspectorOverlay />, { width: 120, height: 42 });
    try {
      for (let index = 0; index < 4; index++) await setup.renderOnce();
      setup.mockInput.pressKey("n");
      for (let index = 0; index < 3; index++) await setup.renderOnce();
      expect(calls.at(-1)).toMatchObject({ method: "inspect_subagent", params: { after_id: 1 } });

      setup.mockInput.pressKey("p");
      for (let index = 0; index < 3; index++) await setup.renderOnce();
      expect(calls.at(-1)).toMatchObject({ method: "inspect_subagent", params: { after_id: 0 } });

      actions.tabCreate({ sessionId: "another-parent", title: "other" });
      await setup.renderOnce();
      expect(workspace.subagentInspector).toBeNull();
      expect(calls.filter((call) => call.method === "load_session")).toHaveLength(0);
    } finally {
      setup.renderer.destroy();
    }
  });

  it("tells the truth for a remote child instead of fabricating a local transcript", async () => {
    let calls = 0;
    setCompanionBridgeForTests({
      request: async () => {
        calls += 1;
        return inspection();
      },
    } as unknown as CompanionBridgeClient);
    actions.openSubagentInspector({
      sessionId: "remote-child",
      agentId: "remote-agent",
      parentSessionId: "parent-session",
      parentAgentId: "parent-agent",
      venue: "frontend",
    });
    const setup = await testRender(() => <SubagentInspectorOverlay />, { width: 120, height: 35 });
    try {
      for (let index = 0; index < 3; index++) await setup.renderOnce();
      const frame = setup.captureCharFrame();
      expect(frame).toContain("local-only");
      expect(frame).toContain("S5");
      expect(frame).not.toContain("the actual child response");
      expect(calls).toBe(0);
    } finally {
      setup.renderer.destroy();
    }
  });
});
