/**
 * Agent work-state animation tests — phase priority and status text.
 */

import { describe, expect, it, beforeEach } from "bun:test";
import { workspace, setWorkspace } from "../src/stores";
import { agentPhase, phaseStatusText, workStatusLine } from "../src/components/agent-activity";

describe("agentPhase", () => {
  beforeEach(() => {
    setWorkspace("toolApproval", null);
    setWorkspace("chatTurnStatus", null);
    setWorkspace("chatStreaming", false);
    setWorkspace("isTyping", false);
    setWorkspace("thinking", {
      text: "",
      open: false,
      expanded: false,
      startedAtMs: null,
      elapsedMs: 0,
    });
  });

  it("is idle by default", () => {
    expect(agentPhase()).toBe("idle");
  });

  it("running beats thinking and streaming", () => {
    setWorkspace("chatTurnStatus", "Running ls …");
    setWorkspace("chatStreaming", true);
    expect(agentPhase()).toBe("running");
  });

  it("awaiting approval beats tool execution", () => {
    setWorkspace("chatTurnStatus", "Running ls …");
    setWorkspace("toolApproval", { id: 1, tool: "shell_exec" });
    expect(agentPhase()).toBe("awaiting");
  });

  it("thinking from an open thinking block", () => {
    setWorkspace("thinking", { text: "x", open: true, expanded: false, startedAtMs: 1, elapsedMs: 0 });
    expect(agentPhase()).toBe("thinking");
  });

  it("thinking from streaming alone", () => {
    setWorkspace("chatStreaming", true);
    expect(agentPhase()).toBe("thinking");
  });
});

describe("phaseStatusText", () => {
  beforeEach(() => {
    setWorkspace("toolApproval", null);
    setWorkspace("chatTurnStatus", null);
    setWorkspace("thinking", {
      text: "",
      open: false,
      expanded: false,
      startedAtMs: null,
      elapsedMs: 0,
    });
  });

  it("names the awaited tool", () => {
    setWorkspace("toolApproval", { id: 1, tool: "shell_exec" });
    expect(phaseStatusText("awaiting")).toContain("shell_exec");
    expect(phaseStatusText("awaiting")).toContain("awaiting approval");
  });

  it("keeps the tool label while running and appends elapsed seconds", () => {
    setWorkspace("chatTurnStatus", "Running find …");
    setWorkspace("thinking", { text: "", open: true, expanded: false, startedAtMs: 1, elapsedMs: 12_000 });
    expect(phaseStatusText("running")).toBe("Running find …… ⏱ 12s");
  });

  it("shows thinking with ticking seconds (rounded)", () => {
    setWorkspace("thinking", { text: "", open: true, expanded: false, startedAtMs: 1, elapsedMs: 3500 });
    expect(phaseStatusText("thinking")).toBe("thinking… ⏱ 4s");
  });

  it("no timer before the first second", () => {
    expect(phaseStatusText("thinking")).toBe("thinking…");
  });
});

describe("workStatusLine (status-bar work indicator)", () => {
  beforeEach(() => {
    setWorkspace("toolApproval", null);
    setWorkspace("chatTurnStatus", null);
    setWorkspace("thinking", {
      text: "",
      open: false,
      expanded: false,
      startedAtMs: null,
      elapsedMs: 0,
    });
  });

  it("appends tool and agent counts to the phase text", () => {
    setWorkspace("chatTurnStatus", "Running shell_exec …");
    const line = workStatusLine("running", { tools: 3, agentsActive: 1, agentsTotal: 2 });
    expect(line).toBe("Running shell_exec …… · tools 3 · agents 1/2");
  });

  it("omits both counts when zero", () => {
    expect(workStatusLine("thinking", { tools: 0, agentsActive: 0, agentsTotal: 0 })).toBe(
      "thinking…",
    );
  });

  it("shows agents only when a roster exists", () => {
    expect(workStatusLine("thinking", { tools: 0, agentsActive: 1, agentsTotal: 3 })).toBe(
      "thinking… · agents 1/3",
    );
  });

  it("shows tools without agents", () => {
    expect(workStatusLine("thinking", { tools: 2, agentsActive: 0, agentsTotal: 0 })).toBe(
      "thinking… · tools 2",
    );
  });

  it("carries the elapsed timer through", () => {
    setWorkspace("thinking", { text: "", open: true, expanded: false, startedAtMs: 1, elapsedMs: 9000 });
    expect(workStatusLine("thinking", { tools: 1, agentsActive: 0, agentsTotal: 0 })).toBe(
      "thinking… ⏱ 9s · tools 1",
    );
  });
});
