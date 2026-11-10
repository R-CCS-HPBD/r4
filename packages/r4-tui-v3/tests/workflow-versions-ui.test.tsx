/**
 * ADR-113 WV4 (2026-11-09): picking and showing workflow versions in the
 * console. The work pane lists the default version and a count, `v`/`V`
 * pick another, Enter shows and `t` runs the picked one (the bridge verb
 * gets `version`); the definition card steps through versions with `v` and
 * its `r` line pins `name@X`; runs show `name@version · digest`;
 * `/workflow show name@X` asks for that version.
 */
import { describe, it, expect, beforeEach, afterEach, afterAll } from "bun:test";
import { testRender } from "@opentui/solid";
import { reconcile } from "solid-js/store";
import { WorkRoute } from "../src/routes/work";
import { WorkflowDefOverlay } from "../src/components/workflow-def-overlay";
import { WorkflowExecOverlay } from "../src/components/workflow-exec-overlay";
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

const SHA = "3f2a9c1d0b7e55aa66bb77cc88dd99ee00ff11aa22bb33cc44dd55ee66ff7788";
const VERSIONS = ["1.2.0", "1.1.0", "1.0.0"];
const ROW = { workflow_name: "relax", description: "relax a cell", version: "1.2.0", versions: VERSIONS };

/** A package per version: none needs a value, so `t` runs at once. */
function pkg(version: string) {
  return {
    workflow_name: "relax",
    version,
    versions: VERSIONS,
    source_sha256: SHA,
    steps: [{ name: "run", entrypoint: "run:Run" }],
    files: [],
  };
}

type Call = { method: string; params: Record<string, unknown> };
const WATCHED = new Set(["workflow.package", "workflow.run", "workflow_describe"]);

function fakeBridge() {
  const calls: Call[] = [];
  const answers: Record<string, (p: Record<string, unknown>) => unknown> = {
    list_slash_commands: () => ({ commands: [{ name: "workflow", help: "UMRS workflows", category: "workflow" }] }),
    "workflow.list": () => ({ workflows: [ROW] }),
    "workflow.package": (p) => pkg(String(p.version ?? "1.2.0")),
    "workflow.run": (p) => ({
      execution_id: "exec-relax-0001",
      workflow_name: "relax",
      workflow_version: String(p.version ?? "1.2.0"),
      source_sha256: SHA,
      status: "succeeded",
    }),
    workflow_describe: (p) => ({ ...pkg(String(p.version ?? "1.2.0")), code: "unavailable" }),
  };
  return {
    calls,
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      if (WATCHED.has(method)) calls.push({ method, params });
      return answers[method]?.(params) ?? {};
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
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  setUmrsWorkflowStore("definitions", reconcile({}));
  setUmrsWorkflowStore("definitionErrors", reconcile({}));
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  setLoopsState({ rows: [], error: null });
  setWorkspace("paneFocus", true);
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 120));
  setCompanionBridgeForTests(null);
  actions.setWorkflowDefId(null);
  actions.setExecDetailId(null);
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  resetWorkspace();
});

const settle = () => new Promise((r) => setTimeout(r, 50));

async function mountPane(bridge: ReturnType<typeof fakeBridge>, withCard = false) {
  setCompanionBridgeForTests(bridge as never);
  setUmrsWorkflowStore("catalog", [ROW as never]);
  actions.openPane("work");
  setWorkspace("paneFocus", true);
  const setup = await testRender(
    () => (
      <>
        <WorkRoute />
        {withCard ? <WorkflowDefOverlay /> : null}
      </>
    ),
    { width: 200, height: 40 },
  );
  await setup.renderOnce();
  return setup;
}

describe("work pane: version display and picker", () => {
  it("a catalog row shows the default version and how many others", async () => {
    const setup = await mountPane(fakeBridge());
    expect(setup.captureCharFrame()).toContain("trigger: relax · v1.2.0 (+2)");
    setup.renderer.destroy();
  });

  it("v picks the next version and t runs it: the bridge verb carries version", async () => {
    const bridge = fakeBridge();
    const setup = await mountPane(bridge);
    setup.mockInput.pressKey("v");
    await settle();
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("trigger: relax@1.1.0 · picked (+2)");
    setup.mockInput.pressKey("t");
    await settle();
    await setup.renderOnce();
    const pkgCall = bridge.calls.find((c) => c.method === "workflow.package");
    expect(pkgCall?.params).toEqual({ workflow_name: "relax", version: "1.1.0" });
    const run = bridge.calls.find((c) => c.method === "workflow.run");
    expect(run?.params["workflow_name"]).toBe("relax");
    expect(run?.params["version"]).toBe("1.1.0");
    // The run row shows what it pinned.
    expect(setup.captureCharFrame()).toContain("relax@1.1.0 · 3f2a9c1d0b7e");
    setup.renderer.destroy();
  });

  it("t without a pick runs the default the package names", async () => {
    const bridge = fakeBridge();
    const setup = await mountPane(bridge);
    setup.mockInput.pressKey("t");
    await settle();
    expect(bridge.calls.find((c) => c.method === "workflow.package")?.params).toEqual({ workflow_name: "relax" });
    expect(bridge.calls.find((c) => c.method === "workflow.run")?.params["version"]).toBe("1.2.0");
    setup.renderer.destroy();
  });

  it("Enter after a pick opens the card on that version", async () => {
    const bridge = fakeBridge();
    const setup = await mountPane(bridge, true);
    setup.mockInput.pressKey("v");
    await settle();
    setup.mockInput.pressEnter();
    await settle();
    await setup.renderOnce();
    expect(workspace.workflowDefId).toBe("relax@1.1.0");
    expect(umrsWorkflowStore.definitions["relax@1.1.0"]?.version).toBe("1.1.0");
    expect(setup.captureCharFrame()).toContain("workflow definition — relax@1.1.0");
    setup.renderer.destroy();
  });
});

describe("definition card: v steps through versions, r pins the shown one", () => {
  it("v fetches the next version's package; r drafts name@X", async () => {
    const bridge = fakeBridge();
    setCompanionBridgeForTests(bridge as never);
    setUmrsWorkflowStore("definitions", "relax", pkg("1.2.0") as never);
    actions.setWorkflowDefId("relax");
    const setup = await testRender(() => <WorkflowDefOverlay />, { width: 120, height: 40 });
    await setup.renderOnce();
    const first = setup.captureCharFrame();
    expect(first).toContain("workflow definition — relax@1.2.0");
    expect(first).toContain("versions  · 1.2.0 (default) · 1.1.0 · 1.0.0");
    expect(first).toContain("v/V version");
    setup.mockInput.pressKey("v");
    await settle();
    await setup.renderOnce();
    expect(workspace.workflowDefId).toBe("relax@1.1.0");
    expect(bridge.calls.at(-1)).toEqual({ method: "workflow.package", params: { workflow_name: "relax", version: "1.1.0" } });
    expect(setup.captureCharFrame()).toContain("/workflow trigger relax@1.1.0");
    setup.mockInput.pressKey("r");
    await setup.renderOnce();
    expect(workspace.pendingChatDraft).toBe("/workflow trigger relax@1.1.0");
    setup.renderer.destroy();
  });
});

describe("runs and /workflow show", () => {
  it("the execution overlay title is name@version · digest", async () => {
    setUmrsWorkflowStore("activeExecutions", {
      "exec-v": {
        execution_id: "exec-v",
        workflow_name: "relax",
        workflow_version: "1.1.0",
        source_sha256: SHA,
        status: "succeeded",
      } as never,
    });
    actions.setExecDetailId("exec-v");
    const setup = await testRender(() => <WorkflowExecOverlay />, { width: 100, height: 30 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("relax@1.1.0 · 3f2a9c1d0b7e · exec-v");
    setup.renderer.destroy();
  });

  it("/workflow show name@X asks for that version and the card marks gone code", async () => {
    const bridge = fakeBridge();
    setCompanionBridgeForTests(bridge as never);
    const result = await dispatchSlashCommand("/workflow show relax@1.0.0");
    expect(result.kind).toBe("handled");
    expect(bridge.calls.at(-1)).toEqual({ method: "workflow_describe", params: { workflow_name: "relax", version: "1.0.0" } });
    const card = workspace.chatMessages.at(-1)!.mgmtCard!;
    expect(card.type).toBe("wf-show");
    expect((card.payload as Record<string, unknown>)["workflow_name"]).toBe("relax");
  });
});
