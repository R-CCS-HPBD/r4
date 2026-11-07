/**
 * Triggering a workflow that needs values (2026-10-02): the work pane's `t`
 * puts the trigger line in the composer instead of running it without them
 * (a workflow that needs none still runs at once), and a run typed in the
 * composer joins the work pane like a `t` run.
 */
import { describe, it, expect, beforeEach, afterEach, afterAll } from "bun:test";
import { testRender } from "@opentui/solid";
import { reconcile } from "solid-js/store";
import { WorkRoute } from "../src/routes/work";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { setUmrsWorkflowStore, umrsWorkflowStore } from "../src/stores/umrs-workflow";
import { setTeamsState } from "../src/stores/teams";
import { setLoopsState } from "../src/stores/loops";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

const NEEDS_VALUES = {
  workflow_name: "fig-read",
  parameter_schema: { type: "object", properties: { image: { type: "string" } }, required: ["image"] },
  steps: [{ name: "see", entrypoint: "see:See", depends_on: [] }],
  files: [],
};
const NEEDS_NONE = { workflow_name: "smoke", steps: [{ name: "hello", entrypoint: "hello:Hello" }], files: [] };

type Call = { method: string; params: Record<string, unknown> };

/** Calls the test cares about (the pane's mount polls teams/loops/assets too). */
const WORKFLOW_CALLS = new Set(["workflow.package", "workflow.run", "workflow.status", "dispatch_slash_command"]);

function fakeBridge(answers: Record<string, unknown>) {
  const calls: Call[] = [];
  const workflow = (answers["workflow.package"] as { workflow_name?: string } | undefined)?.workflow_name;
  answers = {
    list_slash_commands: { commands: [{ name: "workflow", help: "UMRS workflows", category: "workflow" }] },
    "workflow.list": { workflows: workflow ? [{ workflow_name: workflow }] : [] },
    ...answers,
  };
  return {
    calls,
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      if (WORKFLOW_CALLS.has(method)) calls.push({ method, params });
      const answer = answers[method];
      if (answer instanceof Error) throw answer;
      return typeof answer === "function" ? (answer as (p: unknown) => unknown)(params) : (answer ?? {});
    },
  };
}

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  resetSlashCatalogCache();
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  agentActions.resetAgentRuntime();
  setUmrsWorkflowStore("catalog", []);
  // A store path set to {} merges — reconcile replaces (rows from the
  // previous test would otherwise sit above the catalog row).
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  setUmrsWorkflowStore("definitions", reconcile({}));
  setUmrsWorkflowStore("definitionErrors", reconcile({}));
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  setLoopsState({ rows: [], error: null });
  // 2026-10-06 focus model: these pins drive the pane's key grammar (t on a
  // workflow row). In-app that means the docked pane OWNS the keys
  // (paneFocus=true post-Tab/click) — emulate the ownership explicitly or
  // the paneScoped trap stays blurred and the keystrokes land nowhere.
  setWorkspace("paneFocus", true);
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 120));
  setCompanionBridgeForTests(null);
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  resetWorkspace();
});

async function pressT(bridge: ReturnType<typeof fakeBridge>, workflow: string) {
  setCompanionBridgeForTests(bridge as never);
  setUmrsWorkflowStore("catalog", [{ workflow_name: workflow } as never]);
  actions.openPane("work");
  // 2026-10-06 focus model: openPane lands UNFOCUSED (chat-first); this pin
  // presses `t`, i.e. the pane's key grammar — hand it the keys the way the
  // app does (Tab/click in).
  setWorkspace("paneFocus", true);
  const setup = await testRender(() => <WorkRoute />, { width: 100, height: 30 });
  await setup.renderOnce();
  setup.mockInput.pressKey("t");
  await new Promise((r) => setTimeout(r, 50));
  await setup.renderOnce();
  setup.renderer.destroy();
}

describe("work pane t on a workflow row", () => {
  it("needs values: fetches the package and pre-fills the composer, no run", async () => {
    const bridge = fakeBridge({ "workflow.package": NEEDS_VALUES });
    await pressT(bridge, "fig-read");
    expect(bridge.calls.map((c) => c.method)).toEqual(["workflow.package"]);
    expect(workspace.pendingChatDraft).toBe("/workflow trigger fig-read --param image=");
    expect(workspace.paneView).toBeNull();
  });

  it("needs none: runs at once, as before", async () => {
    const bridge = fakeBridge({
      "workflow.package": NEEDS_NONE,
      "workflow.run": { execution_id: "exec-smoke-0001", status: "succeeded" },
    });
    await pressT(bridge, "smoke");
    expect(bridge.calls.map((c) => c.method)).toEqual(["workflow.package", "workflow.run"]);
    expect(workspace.pendingChatDraft).toBeNull();
    expect(umrsWorkflowStore.activeExecutions["exec-smoke-0001"]).toBeDefined();
  });

  it("package unavailable (older backend): runs at once, as before", async () => {
    const bridge = fakeBridge({
      "workflow.list": { workflows: [{ workflow_name: "fig-read" }] },
      "workflow.package": new Error("404 Not Found"),
      "workflow.run": { execution_id: "exec-old-0001", status: "succeeded" },
    });
    await pressT(bridge, "fig-read");
    expect(bridge.calls.map((c) => c.method)).toEqual(["workflow.package", "workflow.run"]);
  });
});

describe("/workflow delete (2026-10-19)", () => {
  it("is not a v3-local verb: the line forwards to the companion intact", async () => {
    // The shared registry (r4/tui/commands.py) owns delete; v3's manifest
    // deliberately keeps it out of localVerbs, like trigger/run/cancel.
    const bridge = fakeBridge({
      dispatch_slash_command: { handled: true, output: [{ kind: "system", text: "Workflow 'relax' deleted" }], state: {} },
    });
    setCompanionBridgeForTests(bridge as never);
    const result = await dispatchSlashCommand("/workflow delete relax");
    expect(result.kind).toBe("handled");
    expect(bridge.calls.map((c) => c.method)).toEqual(["dispatch_slash_command"]);
    expect(bridge.calls[0]!.params["command"]).toBe("/workflow delete relax");
  });
});

describe("a run typed in the composer", () => {
  it("joins the work pane, stamped with the active project", async () => {
    setWorkspace("project", { id: "proj-a", name: "A", description: "", status: "active", goalIds: [] });
    const bridge = fakeBridge({
      dispatch_slash_command: { handled: true, output: [{ kind: "system", text: "workflow triggered: exec-typed-0001" }], state: {} },
      "workflow.status": { execution_id: "exec-typed-0001", status: "succeeded" },
    });
    setCompanionBridgeForTests(bridge as never);
    const result = await dispatchSlashCommand("/workflow trigger fig-read --param image=s3://b/fig.png");
    expect(result.kind).toBe("handled");
    await new Promise((r) => setTimeout(r, 20));
    expect(bridge.calls.map((c) => c.method)).toEqual(["dispatch_slash_command", "workflow.status"]);
    expect(umrsWorkflowStore.activeExecutions["exec-typed-0001"]?._scopeProjectId).toBe("proj-a");
  });

  it("--project names the scope; an id-less answer adopts nothing", async () => {
    const bridge = fakeBridge({
      dispatch_slash_command: (p: unknown) => ({
        handled: true,
        output: [{ kind: "system", text: String((p as { command: string }).command).includes("smoke") ? "workflow triggered: ?" : "workflow triggered: exec-b-0001" }],
        state: {},
      }),
      "workflow.status": { execution_id: "exec-b-0001", status: "succeeded" },
    });
    setCompanionBridgeForTests(bridge as never);
    await dispatchSlashCommand("/workflow run fig-read --param image=x --project proj-b");
    await dispatchSlashCommand("/workflow trigger smoke");
    await new Promise((r) => setTimeout(r, 20));
    expect(umrsWorkflowStore.activeExecutions["exec-b-0001"]?._scopeProjectId).toBe("proj-b");
    expect(bridge.calls.filter((c) => c.method === "workflow.status").length).toBe(1);
  });
});
