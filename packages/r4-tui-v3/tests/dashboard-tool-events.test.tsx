/**
 * Regression guard for the 2026-08-31 Dashboard crash:
 * 'TextNodeRenderable only accepts strings…' fired while the agent used tools.
 * Renders the App on the Dashboard space while the runtime bridge fans
 * tool_call / tool_result events through the timeline/activity panels.
 */
import { test, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions, setWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

async function teardownHarness() {
  stopBackendPoller();
  stopKnowledgePoller();
  await new Promise((r) => setTimeout(r, 0));
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});
afterEach(teardownHarness);

test("dashboard renders tool events without the TextNodeRenderable crash", async () => {
  const setup = await testRender(() => <App />, { width: 160, height: 60 });

  // Same shapes the runtime bridge emits for a denied-then-ok pair.
  const id = agentActions.createAgent({
    role: "companion",
    goal: "browsing-agent",
    state: "running",
    budget: { maxTokens: 100000, maxIterations: 50 },
    permissions: { egress: "controlled", filesystem: "read_only", compute: "none" },
  });
  agentActions.addTimelineEntry("hypothesis_created", "🧰 Tool: shell_exec", undefined, id);
  agentActions.addTimelineEntry("finding_registered", "✅ Result: shell_exec", "done", id);
  actions.addEvent({
    id: "e-1",
    timestamp: new Date().toISOString(),
    type: "tool",
    actor: "companion",
    action: "tool_call",
    detail: "shell_exec",
  });
  actions.addEvent({
    id: "e-2",
    timestamp: new Date().toISOString(),
    type: "tool",
    actor: "companion",
    action: "tool_result",
    detail: "⛔ blocked: shell_exec",
  });
  setWorkspace("chatStreaming", true);
  actions.setChatTurnStatus("🧰 tool: shell_exec");

  // Streaming assistant bubble + typing indicator + approval overlay + C1
  // request-op card — the full live-burst set the agent produces when tools
  // begin firing mid-turn.
  setWorkspace("isTyping", true);
  setWorkspace("chatMessages", [{
    id: "m-1",
    role: "assistant",
    content: "I will list the files first.\n\n",
    streaming: true,
    timestamp: new Date().toISOString(),
  }]);
  actions.openToolApproval({ id: 1, tool: "shell_exec", description: "Run shell command: ls -la", sessionApprovalAllowed: true });
  actions.sendMgmtCard("request-op", { tool: "shell_exec", decision: "yes", at: new Date().toISOString() });

  await setup.renderOnce();
  const frame = setup.captureCharFrame();

  expect(frame).not.toContain("Dashboard error");
  expect(frame).not.toContain("TextNodeRenderable only accepts strings");
  setup.renderer.destroy();
});

test("mgmt card rows render as styled text (two-tone badge + value)", async () => {
  const setup = await testRender(() => <App />, { width: 160, height: 60 });
  actions.sendMgmtCard("wf-run", {
    execution: {
      status: "running",
      workflow_name: "kb-build",
      steps: [{ name: "collect", status: "succeeded", duration_ms: 2000 }],
    },
  });
  actions.sendMgmtCard("loop-list", {
    loops: [{ loop_id: "L1", status: "running", current_iteration: 1, max_iterations: 5, research_goal: "probe stability" }],
    active_count: 1,
  });
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  expect(frame).not.toContain("Dashboard error");
  expect(frame).not.toContain("TextNodeRenderable only accepts strings");
  expect(frame).toMatch(/workflow run — kb-build|kb-build/);
  setup.renderer.destroy();
});
