/**
 * TUI audit r2 (2026-10-17), glue lane — cross-lane deferrals outside the
 * keymap and turn protocol.
 *
 * #13     the Research approvals y/n on a tool-approval gate answer the
 *         companion card instead of only the local gate.
 * #58     a tab switch does not end the remote-start wait.
 * #25/#40 the loops poller steps aside while a (slash) turn holds the bridge.
 * #27     a new session starts with an empty Plan graph.
 * #36     a queued remote turn does not settle its tab when the RPC answers.
 * #31     a turn whose tab closed ends instead of parking forever.
 * #33     a new turn does not inherit the bridge's stale round lines.
 * #70     mounting the status bar never runs the statusline command inline.
 * #34/#46 tests run against a scratch config home; no layout writes there.
 * #55     an open Knowledge pane loads the new project's graph on a switch.
 * #66     the tour no longer promises a citations drill-down.
 * approval the readiness gate names `r4 project approve` and a lapse.
 */
import { EventEmitter } from "node:events";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, agentActions, agentRuntime, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { wireChatTabsOnce } from "../src/tabs/wiring";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import {
  resetApprovalMirrorsForTests,
  handleApprovalRequest,
} from "../src/bridge/approval-bridge";
import { onBridgeEvent, resetCompanionRuntimeBridgeForTests } from "../src/bridge/companion-runtime-bridge";
import { awaitRemoteRunning } from "../src/bridge/venue-bridge";
import { newSession } from "../src/bridge/session-bridge";
import { handleOpsApprovalKey } from "../src/lib/research-keyboard";
import { _loopPollTickForTests, setLoopsState } from "../src/stores/loops";
import { layoutWritesAllowed } from "../src/tabs/layout-persist";
import { TOUR_STEPS } from "../src/lib/tour-steps";
import { parseReadinessResult, readinessApprovalHint } from "../src/lib/readiness-sync";
import { setDemoMode } from "../src/lib/console-mode";
import { StatusBar } from "../src/layout/status-bar";
import { KnowledgeRoute } from "../src/routes/knowledge";
import { scienceActions } from "../src/stores/science";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A channel-aware fake bridge (an EventEmitter, like CompanionBridgeClient). */
class FakeBridge extends EventEmitter {
  alive = true;
  turnInFlight = false;
  readonly methods: string[] = [];
  readonly answers: Array<[string | number, string]> = [];
  answer: (method: string, params: Record<string, unknown>) => unknown = () => ({});
  private resolvers = new Map<string, (value: unknown) => void>();

  async ensureStarted(): Promise<void> {}
  abortInFlightSend(): void {}
  cancelTurn(): void {}
  respondApproval(id: string | number, decision: string): void {
    this.answers.push([id, decision]);
  }
  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    this.methods.push(method);
    if (method === "send_message") return new Promise((resolve) => this.resolvers.set(method, resolve));
    return Promise.resolve(this.answer(method, params));
  }
  resolve(method: string, value: unknown): void {
    this.resolvers.get(method)?.(value);
  }
}

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
  resetCompanionRuntimeBridgeForTests();
  agentActions.resetAgentRuntime();
  resetApprovalMirrorsForTests();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  setCompanionBridgeForTests(null);
  resetWorkspace();
  resetTabsRegistryForTests();
});

describe("r2 #13 — Research approvals answer the live tool card", () => {
  it("y on the mirror of a card open in another tab leaves the gate to that card", () => {
    wireChatTabsOnce();
    const bridge = new FakeBridge();
    setCompanionBridgeForTests(bridge as never);
    handleApprovalRequest({ id: 21, tool: "file_write", session_approval_allowed: true });
    const gate = () => agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:21");
    expect(gate()).toBeDefined();
    actions.tabCreate(); // the card stays open in the first tab
    actions.openPane("research");
    actions.setResearchBottomTab("ops");
    actions.setSelectedApprovalId(gate()!.id);
    expect(handleOpsApprovalKey("y")).toBe(true);
    // Was: the gate settled "approved" while the companion still waited.
    expect(gate()?.status).toBe("pending");
    expect(bridge.answers).toEqual([]);
    expect(workspace.chatMessages.some((m) => m.content.includes("still open in chat tab"))).toBe(true);
  });
});

describe("r2 #58 — the remote-start wait belongs to its tab", () => {
  it("a tab switch pauses the wait; it resumes and reports in its tab", async () => {
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    setWorkspace("sessionId", "S-A");
    const bridge = new FakeBridge();
    bridge.answer = (method) =>
      method === "session.venue_status" ? { venue: { state: "running" }, lease_remaining_sec: 60 } : {};
    setCompanionBridgeForTests(bridge as never);
    const waiting = awaitRemoteRunning();
    actions.tabCreate(); // tab B: another session id ("")
    await sleep(2_300); // one poll interval passes while A is in the background
    expect(bridge.methods).not.toContain("session.venue_status");
    actions.tabActivate(tabA);
    const result = await waiting;
    expect(result.kind).toBe("running");
    expect(workspace.chatMessages.some((m) => m.content.includes("remote session running"))).toBe(true);
  }, 15_000);
});

describe("r2 #25/#40 / plan H-8 — the loops poller polls during a turn", () => {
  it("loops.list goes out while a slash-dispatched turn holds the bridge", () => {
    // The bridge answers reads mid-turn from its read pool (plan H-8); the
    // poller used to wait the turn out.
    const bridge = new FakeBridge();
    bridge.turnInFlight = true;
    setCompanionBridgeForTests(bridge as never);
    setLoopsState({ rows: [{ loop_id: "L1", status: "running" } as never], error: null });
    _loopPollTickForTests();
    expect(bridge.methods).toContain("loops.list");
    setLoopsState({ rows: [], error: null });
  });
});

describe("r2 #27 — a new session starts from its own plan", () => {
  it("/new clears the Plan graph and refetches this session's", async () => {
    const bridge = new FakeBridge();
    bridge.answer = (method) => (method === "get_session_plan" ? { steps: [] } : { messages: [] });
    setCompanionBridgeForTests(bridge as never);
    actions.setSessionPlan([{ id: "old", title: "old step", status: "pending", progress: 0, depends_on: [] }]);
    expect(await newSession()).toBe(true);
    expect(workspace.sessionPlan).toEqual([]);
    await sleep(10);
    expect(bridge.methods).toContain("get_session_plan");
  });
});

describe("r2 #36 — a queued remote turn does not settle on the RPC answer", () => {
  it("emits turn_settled for a local turn only", async () => {
    const settled: unknown[] = [];
    const bridge = new FakeBridge();
    bridge.on("turn_settled", (p: unknown) => settled.push(p));
    const backend = new CompanionChatBackend(bridge as never);
    const ctl = new AbortController();
    const remote = (async () => {
      for await (const _chunk of backend.chatStream("go", [], undefined, ctl.signal)) {
        /* drain */
      }
    })();
    await sleep(5);
    bridge.resolve("send_message", { remote: true, queued: true, state: {} });
    await sleep(10);
    expect(settled).toEqual([]);
    ctl.abort();
    await remote;
  });
});

describe("r2 #31 — a closed tab's turn ends", () => {
  it("the generator releases its event listener once the tab closes", async () => {
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    const bridge = new FakeBridge();
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.sendChatMessage("long work");
    await sleep(10);
    expect(bridge.listenerCount("event")).toBe(1);
    actions.tabCreate();
    // The reply lands while A is in the background: the turn parks until
    // A is active again …
    bridge.resolve("send_message", { response: "late", state: {} });
    await sleep(20);
    expect(bridge.listenerCount("event")).toBe(1);
    // … and closing A must end it (it used to park forever).
    actions.tabClose(tabA);
    await sleep(30);
    expect(bridge.listenerCount("event")).toBe(0);
  });
});

describe("r2 #33 — a new turn starts with an empty round accumulator", () => {
  it("lines left by an interrupted turn are not flushed into the next one", () => {
    onBridgeEvent({ type: "tool_result", data: { name: "read_file", tool_call_id: "c1", result: "ok" } });
    actions.setChatBackend({ chat: () => new Promise<string>(() => undefined) });
    actions.sendChatMessage("next turn");
    onBridgeEvent({ type: "response_start" });
    expect(workspace.turnToolActivity).toEqual([]);
  });
});

describe("r2 #70 — the status bar runs the statusline off the render path", () => {
  it("mounting does not wait for the command", async () => {
    const saved = process.env.R4_CONFIG_HOME;
    const dir = mkdtempSync(join(tmpdir(), "tui-r2-glue-statusbar-"));
    process.env.R4_CONFIG_HOME = dir;
    writeFileSync(join(dir, "statusline"), "sleep 0.6; echo GLUE-LINE");
    try {
      const started = Date.now();
      const setup = await testRender(() => <StatusBar />, { width: 160, height: 3 });
      await setup.renderOnce();
      expect(Date.now() - started).toBeLessThan(450);
      await sleep(900);
      await setup.renderOnce();
      expect(setup.captureCharFrame()).toContain("GLUE-LINE");
      setup.renderer.destroy();
    } finally {
      process.env.R4_CONFIG_HOME = saved;
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("r2 #34/#46 — tests never touch the real config home", () => {
  it("the preload points R4_CONFIG_HOME at a scratch dir that takes no layout writes", () => {
    const home = process.env.R4_CONFIG_HOME ?? "";
    expect(home).not.toBe("");
    expect(home.startsWith(join(homedir(), ".config"))).toBe(false);
    expect(process.env.R4_TEST_DEFAULT_CONFIG_HOME).toBe(home);
    expect(layoutWritesAllowed()).toBe(false);
  });
});

describe("r2 #66 — the tour promises no citation drill-down", () => {
  it("no step tells the user to press Ctrl+G then k on a reply", () => {
    const text = TOUR_STEPS.map((s) => s.why).join("\n");
    expect(text).not.toContain("Ctrl+G");
    expect(text).not.toMatch(/with citations/);
  });
});

describe("readiness approval hint names the real command", () => {
  it("not approved, lapsed, and the default", () => {
    const base = parseReadinessResult({ readiness_level: "medium", can_run: false });
    expect(readinessApprovalHint({ ...base, userApproved: false })).toContain("r4 project approve");
    const lapsed = parseReadinessResult({ readiness_level: "low", user_approved: false, approval_stale: true });
    expect(lapsed.approvalStale).toBe(true);
    expect(readinessApprovalHint(lapsed)).toContain("Approval lapsed");
    expect(readinessApprovalHint(base)).not.toContain("--approve");
  });
});

describe("r2 #55 — the Knowledge pane follows a project switch", () => {
  it("loads the new project's graph without a remount", async () => {
    const loaded: string[] = [];
    const project = (id: string) => ({ id, name: id, description: "", status: "active", goalIds: [] }) as never;
    setWorkspace("project", project("P1"));
    setWorkspace("platform", {
      listGraphs: async () => ({
        graphs: [
          { graph_id: "g-p1", name: "one", project_id: "P1" },
          { graph_id: "g-p2", name: "two", project_id: "P2" },
        ],
      }),
      graphContent: async (graphId: string) => {
        loaded.push(graphId);
        return { graph_id: graphId, nodes: [], edges: [] };
      },
    } as never);
    actions.openPane("knowledge");
    const setup = await testRender(() => <KnowledgeRoute />, { width: 120, height: 40 });
    await setup.renderOnce();
    await sleep(30);
    expect(loaded).toEqual(["g-p1"]);
    setWorkspace("project", project("P2"));
    await sleep(30);
    expect(loaded).toEqual(["g-p1", "g-p2"]);
    setup.renderer.destroy();
    scienceActions.reset();
    setWorkspace("platform", null as never);
  });
});
