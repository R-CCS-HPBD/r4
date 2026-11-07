/**
 * Bridge-level todo capture: an agent todowrite tool_call rewrites the
 * session todos mirror; a result-side payload works when the call's args
 * were not observed. The tab-focus side effect (open console, Todos tab)
 * follows the job-logs precedent.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { onBridgeEvent, resetCompanionRuntimeBridgeForTests } from "../src/bridge/companion-runtime-bridge";
import { resetWorkspace, workspace, actions } from "../src/stores";

const TODOS = {
  todos: [
    { content: "step one", status: "in_progress", priority: "high" },
    { content: "step two", status: "pending", priority: "low" },
  ],
};

beforeEach(() => {
  resetWorkspace();
  resetCompanionRuntimeBridgeForTests();
});

describe("bridge todowrite capture", () => {
  test("tool_call with todowrite args mirrors the full list", () => {
    onBridgeEvent({
      type: "tool_call",
      data: { name: "todowrite", arguments: TODOS, tool_call_id: "tc-1" },
    });
    expect(workspace.sessionTodos).toHaveLength(2);
    expect(workspace.sessionTodos[0]?.content).toBe("step one");
    expect(workspace.sessionTodos[0]?.status).toBe("in_progress");
  });

  test("a second write replaces the list, never appends", () => {
    onBridgeEvent({ type: "tool_call", data: { name: "todowrite", arguments: TODOS, tool_call_id: "tc-1" } });
    onBridgeEvent({
      type: "tool_call",
      data: {
        name: "todowrite",
        arguments: { todos: [{ content: "only one left", status: "completed", priority: "high" }] },
        tool_call_id: "tc-2",
      },
    });
    expect(workspace.sessionTodos).toHaveLength(1);
    expect(workspace.sessionTodos[0]?.content).toBe("only one left");
  });

  test("result-side payload works when the call was not seen", () => {
    onBridgeEvent({
      type: "tool_result",
      name: "TodoWrite",
      message: JSON.stringify(TODOS),
      tool_call_id: "tc-missing",
    });
    expect(workspace.sessionTodos).toHaveLength(2);
  });

  test("non-todo tools never touch the mirror", () => {
    onBridgeEvent({
      type: "tool_call",
      data: { name: "bash", arguments: { command: "ls", todos: TODOS.todos }, tool_call_id: "tc-3" },
    });
    expect(workspace.sessionTodos).toHaveLength(0);
  });

  test("capture focuses the Todos tab (job-logs precedent)", () => {
    expect(workspace.activityOpen).toBe(false);
    onBridgeEvent({ type: "tool_call", data: { name: "todowrite", arguments: TODOS, tool_call_id: "tc-4" } });
    expect(workspace.activityOpen).toBe(true);
    expect(workspace.activityTab).toBe("todos");
  });

  test("non-parseable todo payloads leave the mirror + console untouched", () => {
    onBridgeEvent({ type: "tool_call", data: { name: "todowrite", arguments: "not-json", tool_call_id: "tc-5" } });
    expect(workspace.sessionTodos).toHaveLength(0);
    expect(workspace.activityOpen).toBe(false);
    void actions;
  });
});
