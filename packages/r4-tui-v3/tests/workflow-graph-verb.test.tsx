/**
 * Wave 72 (2026-10-24): `/workflow graph [id|latest]` opens the live
 * workflow-execution DAG overlay directly, the status-bar badge counts the
 * running executions with the latest one's step progress, and Alt+W opens
 * that execution's overlay. Before this wave `/workflow graph` went to the
 * companion (no such verb there) and the overlay opened only from Enter on
 * a Work-pane row; the bar showed no workflow state.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { reconcile } from "solid-js/store";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { NO_EXECUTIONS_MESSAGE } from "../src/commands/workflow-graph";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import {
  _clearExecutionPollsForTests,
  _fireExecutionPollForTests,
  setUmrsWorkflowStore,
  umrsWorkflowActions,
  type UmrsWorkflowExecution,
} from "../src/stores/umrs-workflow";
import { statusForStep } from "../src/lib/workflow-graph-projection";
import { costSummary, mapExecutionSteps } from "../src/components/workflow-exec-overlay";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { WORKFLOW_BADGE_MIN_COLS, stepProgress, workflowBadgeCells, workflowBadgeLabel } from "../src/lib/workflow-badge";
import { defaultGraphTarget, openRunningWorkflowGraph } from "../src/lib/workflow-graph-open";
import { StatusBar } from "../src/layout/status-bar";
import { defaultBindings, workflowGraphBlocker } from "../src/keymap";
import { HELP_CHORDS } from "../src/lib/help-rows";
import { activeWorkProjectId } from "../src/lib/work-scope";

interface Recorder {
  methods: string[];
  slashLines: string[];
}

/** A bridge that knows `known` execution records (workflow.detail) and
 * records every call; unknown ids answer like UMRS (not found). */
function installBridge(known: Record<string, Record<string, unknown>> = {}): Recorder {
  const rec: Recorder = { methods: [], slashLines: [] };
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      rec.methods.push(method);
      if (method === "dispatch_slash_command") {
        rec.slashLines.push(String(params.command ?? ""));
        return { handled: true, output: ["companion"] };
      }
      if (method === "workflow.detail") {
        const record = known[String(params.execution_id)];
        if (!record) throw new Error(`execution not found: ${String(params.execution_id)}`);
        return { execution: record };
      }
      if (method === "workflow.package") throw new Error("no package route");
      return {};
    },
  } as never);
  return rec;
}

function exec(id: string, status: string, extra: Record<string, unknown> = {}): UmrsWorkflowExecution {
  return { execution_id: id, workflow_name: `wf-${id}`, status, ...extra } as UmrsWorkflowExecution;
}

function track(...rows: UmrsWorkflowExecution[]): void {
  // reconcile: a plain object set MERGES into a Solid store path.
  setUmrsWorkflowStore(
    "activeExecutions",
    reconcile(Object.fromEntries(rows.map((row) => [row.execution_id, row]))),
  );
}

function clearStore(): void {
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  setUmrsWorkflowStore("definitions", reconcile({}));
  setUmrsWorkflowStore("definitionErrors", reconcile({}));
}

/** UMRS's own step words (workflow_runner.py): "succeeded" when a step
 * finishes, "skipped" after an earlier wave failed. */
const STEPS_3_OF_7 = {
  results: {
    steps: {
      a: { status: "succeeded" },
      b: { status: "succeeded" },
      c: { status: "skipped" },
      d: { status: "running" },
      e: { status: "pending" },
      f: { status: "pending" },
      g: { status: "pending" },
    },
  },
};

const savedBackend = process.env["R4_TUI_BACKEND"];

beforeAll(() => resetSlashCatalogCache());

afterAll(() => {
  resetSlashCatalogCache();
  if (savedBackend === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackend;
});

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
  clearStore();
});

afterEach(async () => {
  await Bun.sleep(5);
  _clearExecutionPollsForTests();
  setCompanionBridgeForTests(null);
  actions.setExecDetailId(null);
  clearStore();
  resetWorkspace();
});

describe("/workflow graph [id|latest]", () => {
  test("no executions: a message pointing to /work, nothing forwarded", async () => {
    const rec = installBridge();
    const result = await dispatchSlashCommand("/workflow graph");
    expect(result).toEqual({ kind: "error", message: NO_EXECUTIONS_MESSAGE });
    expect(NO_EXECUTIONS_MESSAGE).toContain("/work");
    expect(rec.slashLines).toEqual([]);
    expect(workspace.execDetailId).toBeNull();
  });

  test("bare verb opens the latest RUNNING execution over a newer finished one", async () => {
    installBridge();
    track(
      exec("run-old", "running", { started_at: 100 }),
      exec("run-new", "running", { started_at: 200 }),
      exec("done-newest", "completed", { started_at: 300 }),
    );
    expect((await dispatchSlashCommand("/workflow graph")).kind).toBe("handled");
    expect(workspace.execDetailId).toBe("run-new");
  });

  test("`latest` is the default spelled out; with nothing running it opens the latest of any status", async () => {
    const rec = installBridge();
    track(exec("f-1", "failed", { started_at: 10 }), exec("c-2", "completed", { started_at: 20 }));
    expect((await dispatchSlashCommand("/workflow graph latest")).kind).toBe("handled");
    expect(workspace.execDetailId).toBe("c-2");
    expect(rec.slashLines).toEqual([]);
  });

  test("without start stamps the later-tracked execution is the latest", () => {
    track(exec("first", "running"), exec("second", "running"));
    expect(defaultGraphTarget()?.execution_id).toBe("second");
  });

  test("an id or a unique prefix of a tracked execution opens it", async () => {
    installBridge();
    track(exec("abc123456789xyz", "running"), exec("def987", "completed"));
    expect((await dispatchSlashCommand("/workflow graph def987")).kind).toBe("handled");
    expect(workspace.execDetailId).toBe("def987");
    expect((await dispatchSlashCommand("/workflow graph abc1234")).kind).toBe("handled");
    expect(workspace.execDetailId).toBe("abc123456789xyz");
  });

  test("an ambiguous prefix names the candidates", async () => {
    installBridge();
    track(exec("same-1", "running"), exec("same-2", "running"));
    const result = await dispatchSlashCommand("/workflow graph same");
    expect(result.kind).toBe("error");
    expect((result as { message: string }).message).toContain("ambiguous execution id same");
    expect(workspace.execDetailId).toBeNull();
  });

  test("an unknown id gets a clear message, never a blank overlay", async () => {
    const rec = installBridge();
    track(exec("known", "running"));
    const result = await dispatchSlashCommand("/workflow graph nope-42");
    expect(result.kind).toBe("error");
    const message = (result as { message: string }).message;
    expect(message).toContain("unknown workflow execution: nope-42");
    expect(message).toContain("not found");
    expect(workspace.execDetailId).toBeNull();
    expect(rec.slashLines).toEqual([]);
  });

  test("an untracked id UMRS knows is read once, joins the store and opens", async () => {
    const rec = installBridge({ "remote-9": { execution_id: "remote-9", workflow_name: "pipe", status: "running" } });
    expect((await dispatchSlashCommand("/workflow graph remote-9")).kind).toBe("handled");
    expect(workspace.execDetailId).toBe("remote-9");
    expect(rec.methods).toContain("workflow.detail");
  });
});

describe("status-bar workflow badge", () => {
  test("hidden at zero running (finished runs do not count)", () => {
    expect(workflowBadgeLabel(200)).toBeNull();
    track(exec("x", "completed"), exec("y", "failed"));
    expect(workflowBadgeLabel(200)).toBeNull();
  });

  test("counts running executions and shows the latest one's done/total steps", () => {
    track(
      exec("old", "running", { started_at: 1 }),
      exec("new", "queued", { started_at: 2, ...STEPS_3_OF_7 }),
      exec("gone", "completed", { started_at: 3 }),
    );
    // No definition cached: 7 reported rows are a lower bound ("7+").
    expect(workflowBadgeLabel(200)).toBe("▶ 2 workflows · ◐ 3/7+");
  });

  test("one running execution without step rows shows the count alone", () => {
    track(exec("solo", "running"));
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow");
  });

  test("the cached definition's step count is the total when fewer steps reported", () => {
    const row = exec("p", "running", { workflow_name: "pipe", steps: [{ name: "a", status: "succeeded" }] });
    setUmrsWorkflowStore("definitions", "pipe", {
      workflow_name: "pipe",
      steps: [{ name: "a" }, { name: "b" }, { name: "c" }],
    } as never);
    expect(stepProgress(row)).toEqual({ done: 1, total: 3, totalKnown: true });
  });

  test("dropped below the width threshold", () => {
    track(exec("solo", "running"));
    expect(workflowBadgeLabel(WORKFLOW_BADGE_MIN_COLS)).toBe("▶ 1 workflow");
    expect(workflowBadgeLabel(WORKFLOW_BADGE_MIN_COLS - 1)).toBeNull();
  });

  async function footer(width: number): Promise<string> {
    const setup = await testRender(() => <StatusBar />, { width, height: 1 });
    try {
      await setup.renderOnce();
      return setup.captureCharFrame().split("\n")[0] ?? "";
    } finally {
      setup.renderer.destroy();
    }
  }

  test("renders in the bar on a wide terminal, not on a narrow one", async () => {
    setWorkspace("connection", { gateway: "connected", umrs: "connected", hpc: "connected" } as never);
    track(exec("live", "running", STEPS_3_OF_7));
    expect(await footer(180)).toContain("▶ 1 workflow · ◐ 3/7+");
    expect(await footer(90)).not.toContain("▶");
  });
});

describe("Alt+W jump", () => {
  test("alt+w is bound to workflow.graph, and no other default binding uses it", () => {
    const hits = defaultBindings().filter((b) => b.key === "alt+w");
    expect(hits.map((b) => b.cmd)).toEqual(["workflow.graph"]);
    expect(hits[0]?.idle).toBeFalsy();
  });

  test("opens the most recent running execution", () => {
    installBridge();
    track(exec("r1", "running", { started_at: 5 }), exec("r2", "running", { started_at: 9 }), exec("d", "completed", { started_at: 20 }));
    openRunningWorkflowGraph();
    expect(workspace.execDetailId).toBe("r2");
  });

  test("no running execution: a notice, the overlay stays closed", () => {
    track(exec("d", "completed"));
    openRunningWorkflowGraph();
    expect(workspace.execDetailId).toBeNull();
    const notice = workspace.chatMessages.at(-1)?.content ?? "";
    expect(notice).toContain("no workflow execution is running");
  });

  test("the help legend lists Alt+W and /workflow graph", () => {
    const row = HELP_CHORDS.find((chord) => chord.key === "Alt+W");
    expect(row?.meaning).toContain("/workflow graph");
  });
});

describe("Alt+W through the full App (global keymap in play)", () => {
  test("the keypress opens the running execution's overlay", async () => {
    const { App } = await import("../src/app");
    const setup = await testRender(() => <App forceOffline />, { width: 160, height: 48 });
    try {
      await setup.renderOnce();
      actions.closeAllOverlays();
      // Stamped into the active (demo) project: the badge follows /work's scope.
      const scope = activeWorkProjectId() ?? undefined;
      track(exec("live-1", "running", { workflow_name: "pipe", _scopeProjectId: scope, ...STEPS_3_OF_7 }));
      await setup.renderOnce();
      setup.mockInput.pressKey("w", { meta: true });
      await Bun.sleep(30);
      await setup.renderOnce();
      expect(workspace.execDetailId).toBe("live-1");
      const frame = setup.captureCharFrame();
      expect(frame).toContain("workflow execution — Esc closes");
      expect(frame).toContain("▶ 1 workflow · ◐ 3/7+");
    } finally {
      setup.renderer.destroy();
    }
  });
});

/**
 * Review fixes (2026-10-24). A bridge whose answers the test changes as it
 * goes: `records` are the executions UMRS knows (workflow.detail), `defs`
 * the definitions (workflow.package); `detailError` fails every read.
 */
interface LiveBridge {
  methods: string[];
  records: Record<string, Record<string, unknown>>;
  defs: Record<string, Record<string, unknown>>;
  detailError?: string;
}

function installLiveBridge(): LiveBridge {
  const state: LiveBridge = { methods: [], records: {}, defs: {} };
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      state.methods.push(method);
      if (method === "workflow.detail") {
        if (state.detailError) throw new Error(state.detailError);
        const record = state.records[String(params.execution_id)];
        if (!record) throw new Error(`workflow.not_found: execution ${String(params.execution_id)} not found`);
        return { execution: record };
      }
      if (method === "workflow.package") {
        const def = state.defs[String(params.workflow_name)];
        if (!def) throw new Error("workflow.not_found");
        return def;
      }
      return {};
    },
  } as never);
  return state;
}

const count = (methods: string[], name: string): number => methods.filter((m) => m === name).length;

describe("review 1: UMRS step words (succeeded / skipped)", () => {
  test("succeeded is completed, skipped is blocked (not pending)", () => {
    expect(statusForStep("succeeded")).toBe("completed");
    expect(statusForStep("SUCCESS")).toBe("completed");
    expect(statusForStep("skipped")).toBe("blocked");
  });

  test("the badge counts succeeded steps as done", () => {
    track(
      exec("u", "running", {
        workflow_name: "umrs-pipe",
        steps: [
          { name: "a", status: "succeeded" },
          { name: "b", status: "succeeded" },
          { name: "c", status: "running" },
        ],
      }),
    );
    setUmrsWorkflowStore("definitions", "umrs-pipe", {
      workflow_name: "umrs-pipe",
      steps: [{ name: "a" }, { name: "b" }, { name: "c" }],
    } as never);
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow · ◐ 2/3");
  });

  test("the overlay's cost line counts a succeeded step without sacct as unmeasured", () => {
    const rows = mapExecutionSteps({ results: { steps: { a: { status: "succeeded" }, b: { status: "running" } } } });
    expect(costSummary(rows).unmeasured).toBe(1);
  });
});

describe("review 2: the step total before the overlay is ever opened", () => {
  test("a live run's definition is read once when the run is tracked", async () => {
    const bridge = installLiveBridge();
    bridge.defs.pipe = { workflow_name: "pipe", steps: [{ name: "a" }, { name: "b" }, { name: "c" }, { name: "d" }] };
    bridge.records.run = { execution_id: "run", workflow_name: "pipe", status: "running", steps: [{ name: "a", status: "running" }] };
    await umrsWorkflowActions.refreshStatus("run");
    await Bun.sleep(10);
    expect(workspace.execDetailId).toBeNull();
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow · ◐ 0/4");
    // The poll's next reads do not fetch it again.
    await umrsWorkflowActions.refreshStatus("run");
    await umrsWorkflowActions.refreshStatus("run");
    await Bun.sleep(10);
    expect(count(bridge.methods, "workflow.package")).toBe(1);
  });

  test("without a definition the total is a lower bound, shown n+", () => {
    track(exec("r", "running", { steps: [{ name: "a", status: "succeeded" }, { name: "b", status: "running" }] }));
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow · ◐ 1/2+");
  });
});

describe("review 3: an execution UMRS no longer knows", () => {
  test("a not-found poll ends the badge and Alt+W's target; a later read restores them", async () => {
    const bridge = installLiveBridge();
    bridge.records.gone = { execution_id: "gone", workflow_name: "pipe", status: "running" };
    await umrsWorkflowActions.refreshStatus("gone");
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow");
    delete bridge.records.gone;
    await _fireExecutionPollForTests("gone");
    expect(workflowBadgeLabel(200)).toBeNull();
    openRunningWorkflowGraph();
    expect(workspace.execDetailId).toBeNull();
    expect(workspace.chatMessages.at(-1)?.content ?? "").toContain("no workflow execution is running");
    bridge.records.gone = { execution_id: "gone", workflow_name: "pipe", status: "running" };
    await umrsWorkflowActions.refreshStatus("gone");
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow");
  });
});

describe("review 4: the badge follows the Work pane's project scope", () => {
  test("foreign and unstamped runs hide (as in /work) until all-projects is on", async () => {
    installBridge();
    actions.setIdentityState({ projectId: "proj-a" });
    track(exec("foreign", "running", { _scopeProjectId: "proj-b" }), exec("unstamped", "running"));
    expect(workflowBadgeLabel(200)).toBeNull();
    openRunningWorkflowGraph();
    expect(workspace.execDetailId).toBeNull();
    actions.toggleWorkScopeAll();
    expect(workflowBadgeLabel(200)).toBe("▶ 2 workflows");
    actions.toggleWorkScopeAll();
    track(exec("mine", "running", { _scopeProjectId: "proj-a" }), exec("foreign", "running", { _scopeProjectId: "proj-b" }));
    expect(workflowBadgeLabel(200)).toBe("▶ 1 workflow");
    openRunningWorkflowGraph();
    expect(workspace.execDetailId).toBe("mine");
    // An explicit id still opens a run outside the scope.
    expect((await dispatchSlashCommand("/workflow graph foreign")).kind).toBe("handled");
    expect(workspace.execDetailId).toBe("foreign");
  });
});

describe("review 5: /workflow graph <id> reads once and names failures honestly", () => {
  test("a timeout is 'could not read', not 'unknown'", async () => {
    const bridge = installLiveBridge();
    bridge.detailError = "request workflow.detail timed out after 30000ms";
    const result = await dispatchSlashCommand("/workflow graph slow-1");
    expect(result.kind).toBe("error");
    const message = (result as { message: string }).message;
    expect(message).toContain("could not read workflow execution: slow-1");
    expect(message).not.toContain("unknown workflow execution");
  });

  test("an adopted id costs one workflow.detail read", async () => {
    const bridge = installLiveBridge();
    bridge.records["remote-1"] = { execution_id: "remote-1", workflow_name: "pipe", status: "running" };
    expect((await dispatchSlashCommand("/workflow graph remote-1")).kind).toBe("handled");
    await Bun.sleep(10);
    expect(workspace.execDetailId).toBe("remote-1");
    expect(count(bridge.methods, "workflow.detail")).toBe(1);
  });
});

describe("review 6: the badge yields to the mid-turn identity chip", () => {
  async function footerLine(width: number): Promise<string> {
    const setup = await testRender(() => <StatusBar />, { width, height: 1 });
    try {
      await setup.renderOnce();
      return setup.captureCharFrame().split("\n")[0] ?? "";
    } finally {
      setup.renderer.destroy();
    }
  }

  test("during a turn the identity chip stays at every width the badge shows", async () => {
    setWorkspace("connection", { gateway: "connected", umrs: "connected", hpc: "connected" } as never);
    actions.setIdentityState({ projectId: "proj-ident", profile: "acct" });
    setWorkspace("chatStreaming", true);
    // Every width at which the identity chip shows with no workflow running…
    const shown: number[] = [];
    for (let width = WORKFLOW_BADGE_MIN_COLS; width <= 180; width += 4) {
      if ((await footerLine(width)).includes("proj-ident")) shown.push(width);
    }
    expect(shown.length).toBeGreaterThan(0);
    // …still shows it once one runs (the badge drops first).
    track(exec("live", "running", { _scopeProjectId: "proj-ident", ...STEPS_3_OF_7 }));
    for (const width of shown) expect([width, await footerLine(width)]).toEqual([width, expect.stringContaining("proj-ident")]);
    expect(await footerLine(240)).toContain("▶ 1 workflow");
  });

  test("the badge budgets its ambiguous-width glyphs as wide", () => {
    const label = "▶ 1 workflow · ◐ 3/7";
    expect(workflowBadgeCells(label)).toBe(label.length + 3);
  });
});

describe("review 7: Alt+W never draws over an open dialog", () => {
  test("a pending tool approval, the help overlay or a definition card blocks it", () => {
    installBridge();
    track(exec("r", "running"));
    expect(workflowGraphBlocker()).toBeUndefined();
    setWorkspace("toolApproval", { id: "t1", tool: "bash", description: "ls", sessionApprovalAllowed: true } as never);
    expect(workflowGraphBlocker()).toBe("tool-approval");
    setWorkspace("toolApproval", null as never);
    actions.toggleHelp();
    expect(workflowGraphBlocker()).toBe("help");
    actions.closeAllOverlays();
    actions.setWorkflowDefId("pipe");
    expect(workflowGraphBlocker()).toBe("workflow-def");
    actions.setWorkflowDefId(null);
    // Its own overlay is not a blocker: Alt+W retargets it.
    actions.setExecDetailId("r");
    expect(workflowGraphBlocker()).toBeUndefined();
  });

  test("the keypress with an approval pending: notice, overlay stays closed", async () => {
    const { App } = await import("../src/app");
    const setup = await testRender(() => <App forceOffline />, { width: 160, height: 48 });
    try {
      await setup.renderOnce();
      actions.closeAllOverlays();
      track(exec("live-2", "running", { workflow_name: "pipe", _scopeProjectId: activeWorkProjectId() ?? undefined }));
      setWorkspace("toolApproval", { id: "t1", tool: "bash", description: "ls", sessionApprovalAllowed: true } as never);
      await setup.renderOnce();
      setup.mockInput.pressKey("w", { meta: true });
      await Bun.sleep(30);
      await setup.renderOnce();
      expect(workspace.execDetailId).toBeNull();
      expect(workspace.toolApproval?.id).toBe("t1");
      expect(workspace.chatMessages.at(-1)?.content ?? "").toContain("close the tool-approval first");
    } finally {
      setWorkspace("toolApproval", null as never);
      setup.renderer.destroy();
    }
  });
});
