/** D-70 S4: console inspection is read-only, child-scoped and lifecycle-aware. */
import { beforeEach, describe, expect, it } from "bun:test";
import { setCompanionBridgeForTests, type CompanionBridgeClient } from "../src/bridge/jsonl-client";
import type { EventLogRow, InspectSubagentResult, SubagentSessionRecord } from "../src/bridge/rpc-types.gen";
import type { RpcRail } from "../src/bridge/rpc";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { fetchSessions, mapSessionRecord } from "../src/bridge/session-bridge";
import { openSessionRecord } from "../src/lib/subagent-navigation";
import {
  createSubagentInspector,
  inspectorRows,
  type InspectorTarget,
} from "../src/lib/subagent-inspector";

const PARENT = "parent-session";
const TAB = "owner-tab";

function target(sessionId = "child-a", agentId = "agent-a", venue = "local"): InspectorTarget {
  return {
    sessionId,
    agentId,
    parentSessionId: PARENT,
    parentAgentId: "parent-agent",
    venue,
    ownerTabId: TAB,
  };
}

function session(sessionId: string, agentId: string, status: string): SubagentSessionRecord {
  return {
    id: sessionId,
    name: `Subagent ${agentId}`,
    created_at: "2026-11-10T00:00:00Z",
    updated_at: "2026-11-10T00:00:02Z",
    model: `${agentId}-model`,
    session_kind: "subagent",
    parent_session_id: PARENT,
    parent_agent_id: "parent-agent",
    agent_id: agentId,
    task: `actual task for ${agentId}`,
    system_prompt: `verbatim prompt for ${agentId}`,
    declared_tools: ["file_read"],
    execution_kind: "async",
    status,
    started_at: "2026-11-10T00:00:00Z",
    ended_at: status === "running" ? "" : "2026-11-10T00:00:02Z",
    terminal_error: status === "failed" ? "actual child failure" : "",
    follow_up_count: 1,
    venue: "local",
    event_log_error: "",
    messages: [
      { role: "user", content: `actual task for ${agentId}`, metadata: { subagent_turn: 0 } },
      { role: "assistant", content: `first answer for ${agentId}`, metadata: { subagent_turn: 0 } },
      { role: "user", content: `follow-up for ${agentId}`, metadata: { subagent_turn: 1 } },
      { role: "assistant", content: `actual response for ${agentId}`, metadata: { subagent_turn: 1 } },
    ],
  };
}

function event(id: number, sessionId: string, agentId: string, message = "tool output"): EventLogRow {
  return {
    id,
    session_id: sessionId,
    event_type: id % 2 ? "tool_call" : "tool_result",
    timestamp: `2026-11-10T00:00:0${id}Z`,
    message,
    data: {
      name: "file_read",
      arguments: { path: `${agentId}.txt` },
      result: "[REDACTED] bounded result [+9000 chars]",
      agent_id: agentId,
      parent_session_id: PARENT,
    },
    tool_name: "file_read",
    tool_status: id % 2 ? null : "success",
    tool_duration_ms: id % 2 ? null : 12,
    agent_id: agentId,
    parent_agent_id: "parent-agent",
    task_id: null,
    progress_pct: null,
    intent: null,
    rationale: null,
  };
}

function inspection(sessionId: string, agentId: string, status = "completed"): InspectSubagentResult {
  return {
    session: session(sessionId, agentId, status),
    session_id: sessionId,
    events: [event(1, sessionId, agentId, "read actual child file"), event(2, sessionId, agentId)],
    next_after_id: 2,
    has_more: false,
    total_events: 2,
    limit: 100,
    read_only: true,
    event_payload_cap: 8000,
    event_payloads_may_be_abridged: true,
  };
}

function bridge(handler: (method: string, params: Record<string, unknown>) => unknown): RpcRail & {
  calls: Array<{ method: string; params: Record<string, unknown> }>;
} {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  return {
    calls,
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push({ method, params });
      return handler(method, params);
    },
  };
}

function scheduler() {
  let nextId = 1;
  const pending = new Map<number, () => void>();
  return {
    pending,
    schedule(fn: () => void): number {
      const id = nextId++;
      pending.set(id, fn);
      return id;
    },
    cancel(id: unknown): void {
      pending.delete(id as number);
    },
    async fire(): Promise<void> {
      const first = pending.entries().next().value as [number, () => void] | undefined;
      if (!first) return;
      pending.delete(first[0]);
      await first[1]();
    },
  };
}

beforeEach(() => {
  resetWorkspace();
  setCompanionBridgeForTests(null);
});

describe("durable child inventory and navigation", () => {
  it("maps lineage and opens a child read-only without loading or switching the parent", async () => {
    const row = mapSessionRecord({ ...session("child-a", "agent-a", "running"), subagent_status: "running" });
    expect(row?.session_kind).toBe("subagent");
    expect(row?.parent_session_id).toBe(PARENT);
    expect(row?.agent_id).toBe("agent-a");
    expect(row?.state).toBe("running");
    actions.setSessionId(PARENT);
    const parentBefore = workspace.sessionId;
    const tabsBefore = workspace.chatTabs.map((tab) => tab.id);

    expect(row && (await openSessionRecord(row))).toBe(true);

    expect(workspace.subagentInspector?.sessionId).toBe("child-a");
    expect(workspace.subagentInspector?.agentId).toBe("agent-a");
    expect(workspace.sessionId).toBe(parentBefore);
    expect(workspace.chatTabs.map((tab) => tab.id)).toEqual(tabsBefore);
  });

  it("rebuilds two distinct child rows after a fresh bridge, scoped to the active parent", async () => {
    actions.setSessionId(PARENT);
    const ordinary = {
      id: PARENT,
      name: "Parent",
      model: "parent-model",
      created_at: "",
      updated_at: "",
      pinned: false,
      archived_at: "",
      turn_count: 2,
      cost_usd: 0,
      venue: "local",
      state: "ongoing",
      sync: "local-only",
    };
    const rows = [
      { ...session("child-a", "agent-a", "running"), subagent_status: "running" },
      { ...session("child-b", "agent-b", "completed"), subagent_status: "completed" },
    ];
    const rail = bridge((method, params) => {
      if (method !== "list_sessions") throw new Error(`unexpected writer/navigation RPC: ${method}`);
      return params.session_kind === "subagent"
        ? { sessions: rows, remote_available: false, remote_reason: "", sync_mode: "" }
        : { sessions: [ordinary], remote_available: false, remote_reason: "", sync_mode: "local" };
    });
    setCompanionBridgeForTests(rail as unknown as CompanionBridgeClient);

    expect(await fetchSessions({ includeChildren: true })).toBe(3);
    expect(workspace.sessionsList.map((row) => row.id)).toEqual([PARENT, "child-a", "child-b"]);
    expect(rail.calls.filter((call) => call.params.session_kind === "subagent")[0]?.params.parent_session_id).toBe(PARENT);

    setCompanionBridgeForTests(null);
    actions.setSessionsList([]);
    setCompanionBridgeForTests(rail as unknown as CompanionBridgeClient);
    expect(await fetchSessions({ includeChildren: true })).toBe(3);
    expect(workspace.sessionsList.slice(1).map((row) => [row.id, row.agent_id, row.subagent_status])).toEqual([
      ["child-a", "agent-a", "running"],
      ["child-b", "agent-b", "completed"],
    ]);
    expect(rail.calls.some((call) => call.method === "load_session")).toBe(false);
  });
});

describe("read-only inspector controller", () => {
  it("opens either actual child and reopens its own transcript after restart", async () => {
    const records = new Map([
      ["child-a", inspection("child-a", "agent-a")],
      ["child-b", inspection("child-b", "agent-b")],
    ]);
    const rail = bridge((method, params) => {
      if (method !== "inspect_subagent") throw new Error(`forbidden live-session RPC: ${method}`);
      return records.get(String(params.session_id));
    });
    const first = createSubagentInspector({ target: target(), rail, isActive: () => true, schedule: scheduler().schedule });
    await first.start();
    expect(first.state().snapshot?.session.task).toBe("actual task for agent-a");
    expect(first.state().snapshot?.session.messages.at(-1)?.content).toBe("actual response for agent-a");
    first.stop();

    const other = createSubagentInspector({ target: target("child-b", "agent-b"), rail, isActive: () => true });
    await other.start();
    expect(other.state().snapshot?.session.task).toBe("actual task for agent-b");
    expect(other.state().snapshot?.session.model).toBe("agent-b-model");
    expect(other.state().snapshot?.session.task).not.toContain("agent-a");
    other.stop();

    const restarted = createSubagentInspector({ target: target(), rail, isActive: () => true });
    await restarted.start();
    expect(restarted.state().snapshot?.session.messages.map((row) => row.content)).toEqual([
      "actual task for agent-a",
      "first answer for agent-a",
      "follow-up for agent-a",
      "actual response for agent-a",
    ]);
    restarted.stop();
    expect(rail.calls.every((call) => call.method === "inspect_subagent")).toBe(true);
    expect(rail.calls.some((call) => call.method === "load_session")).toBe(false);
  });

  it("pages forward and back by durable after_id while retaining follow-up turns", async () => {
    const base = inspection("child-a", "agent-a");
    const rail = bridge((_method, params) => {
      const after = Number(params.after_id ?? 0);
      return after === 0
        ? { ...base, events: base.events.slice(0, 1), next_after_id: 1, has_more: true, total_events: 2 }
        : { ...base, events: base.events.slice(1), next_after_id: 2, has_more: false, total_events: 2 };
    });
    const inspector = createSubagentInspector({ target: target(), rail, isActive: () => true });
    await inspector.start();
    expect(inspector.state().snapshot?.events.map((row) => row.id)).toEqual([1]);

    await inspector.nextPage();
    expect(inspector.state().snapshot?.events.map((row) => row.id)).toEqual([2]);
    expect(rail.calls.at(-1)?.params.after_id).toBe(1);
    expect(inspector.state().snapshot?.session.follow_up_count).toBe(1);

    await inspector.previousPage();
    expect(inspector.state().snapshot?.events.map((row) => row.id)).toEqual([1]);
    inspector.stop();
  });

  for (const terminal of ["completed", "failed", "cancelled"] as const) {
    it(`polls a running child and stops on ${terminal}`, async () => {
      let status = "running";
      let requests = 0;
      const rail = bridge(() => {
        requests += 1;
        return inspection("child-a", "agent-a", status);
      });
      const timers = scheduler();
      const inspector = createSubagentInspector({
        target: target(),
        rail,
        isActive: () => true,
        schedule: timers.schedule,
        cancelSchedule: timers.cancel,
      });
      await inspector.start();
      expect(inspector.state().snapshot?.session.status).toBe("running");
      expect(timers.pending.size).toBe(1);

      status = terminal;
      await timers.fire();

      expect(requests).toBe(2);
      expect(inspector.state().snapshot?.session.status).toBe(terminal);
      expect(timers.pending.size).toBe(0);
      inspector.stop();
    });
  }

  it("stops polling on close or owner-tab switch", async () => {
    let active = true;
    let requests = 0;
    const rail = bridge(() => {
      requests += 1;
      return inspection("child-a", "agent-a", "running");
    });
    const timers = scheduler();
    const inspector = createSubagentInspector({
      target: target(),
      rail,
      isActive: () => active,
      schedule: timers.schedule,
      cancelSchedule: timers.cancel,
    });
    await inspector.start();
    expect(timers.pending.size).toBe(1);
    active = false;

    await timers.fire();
    expect(requests).toBe(1);
    expect(timers.pending.size).toBe(0);
    inspector.stop();
  });

  it("rejects wrong-child lineage and reports remote children as local-only unavailable", async () => {
    const wrong = bridge(() => inspection("child-b", "agent-b"));
    const inspector = createSubagentInspector({ target: target(), rail: wrong, isActive: () => true });
    await inspector.start();
    expect(inspector.state().snapshot).toBeNull();
    expect(inspector.state().error).toContain("lineage");

    const remote = createSubagentInspector({ target: target("remote-child", "remote-agent", "frontend"), rail: wrong, isActive: () => true });
    const callsBefore = wrong.calls.length;
    await remote.start();
    expect(remote.state().error).toContain("local-only");
    expect(remote.state().error).toContain("S5");
    expect(wrong.calls.length).toBe(callsBefore);
    remote.stop();
  });

  it("projects actual prompt/messages and bounded redacted chronological tool history", () => {
    const rows = inspectorRows(inspection("child-a", "agent-a"), 2);
    const text = rows.join("\n");
    expect(text).toContain("actual task for agent-a");
    expect(text).toContain("verbatim prompt for agent-a");
    expect(text).toContain("agent-a-model");
    expect(text).toContain("actual response for agent-a");
    expect(text).toContain("turn 1");
    expect(text.indexOf("read actual child file")).toBeLessThan(text.indexOf("[REDACTED] bounded result"));
    expect(text).toContain("[+9000 chars]");
    expect(text).toContain("redacted/bounded by the bridge");
    expect(text).not.toContain("agent-b-model");
  });
});
