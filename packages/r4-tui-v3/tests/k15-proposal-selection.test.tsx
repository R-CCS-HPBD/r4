/**
 * K1.5 minimal review (knowledge plan 2026-10-24): the knowledge-pane
 * proposal keys act on the SELECTED proposal. `a` / `r` always decided
 * `proposals[0]` (keymap/index.ts decideFirstProposal), whichever row the
 * person meant. `]` / `[` move the selection; the panel marks it with ▸.
 * Driven through the full <App/> so the live keymap is in play.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace } from "../src/stores";
import { scienceActions, scienceStore, selectedProposal } from "../src/stores/science";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { proposalHeader, proposalKeysHint } from "../src/components/server-graph-panel";

type Setup = Awaited<ReturnType<typeof testRender>>;

const savedBackend = process.env.R4_TUI_BACKEND;

function reset(): void {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  reset();
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 50));
  setCompanionBridgeForTests(null);
  scienceActions.reset();
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

/** The App with a server graph and three pending proposals in the focused
 * knowledge pane; `decided` records each decision the keys send. */
async function mountInbox(decided: unknown[]): Promise<Setup> {
  const setup = await testRender(() => <App />, { width: 160, height: 50 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  let pending = ["p1", "p2", "p3"];
  setWorkspace("platform", {
    graphContent: async () => ({ graph_id: "gA", nodes: [{ id: "n1", label: "A node" }], edges: [] }),
    graphProposals: async () => ({
      proposals: pending.map((id) => ({ proposal_id: id, graph_id: "gA", summary: `extend ${id}` })),
    }),
    decideGraphProposal: async (id: string, approved: boolean) => {
      decided.push([id, approved]);
      pending = pending.filter((p) => p !== id);
      return {};
    },
  } as never);
  await scienceActions.loadGraphContent("gA");
  await scienceActions.refreshProposals("gA");
  actions.openPane("knowledge");
  setWorkspace("paneFocus", true);
  await settle(setup);
  return setup;
}

describe("knowledge proposal selection (K1.5)", () => {
  it("a on the second proposal approves the second, not the first", async () => {
    const decided: unknown[] = [];
    const setup = await mountInbox(decided);
    expect(scienceStore.proposals.length).toBe(3);
    setup.mockInput.pressKey("]");
    await settle(setup);
    expect(selectedProposal()?.proposalId).toBe("p2");
    setup.mockInput.pressKey("a");
    await settle(setup);
    expect(decided).toEqual([["p2", true]]);
    setup.renderer.destroy();
  });

  it("r declines the selected proposal; [ wraps to the last one", async () => {
    const decided: unknown[] = [];
    const setup = await mountInbox(decided);
    setup.mockInput.pressKey("[");
    await settle(setup);
    setup.mockInput.pressKey("r");
    await settle(setup);
    expect(decided).toEqual([["p3", false]]);
    setup.renderer.destroy();
  });

  it("after a decision the selection falls back to the first remaining row", async () => {
    const decided: unknown[] = [];
    const setup = await mountInbox(decided);
    setup.mockInput.pressKey("]");
    await settle(setup);
    setup.mockInput.pressKey("a");
    await settle(setup);
    expect(scienceStore.proposals.map((p) => p.proposalId)).toEqual(["p1", "p3"]);
    expect(scienceStore.selectedProposalId).toBeNull();
    expect(selectedProposal()?.proposalId).toBe("p1");
    setup.renderer.destroy();
  });

  it("the inbox header names the position and the keys (review of K1 part A)", async () => {
    const decided: unknown[] = [];
    const setup = await mountInbox(decided);
    expect(proposalHeader()).toBe("1/3 agent proposals");
    expect(proposalKeysHint()).toBe("[ ] select · a/r decide");
    scienceActions.selectProposal("p2");
    await settle(setup);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("2/3 agent");
    expect(frame).toContain("[ ] select");
    setup.renderer.destroy();
  });

  it("the panel marks the selected row", async () => {
    const decided: unknown[] = [];
    const setup = await mountInbox(decided);
    scienceActions.selectProposal("p3");
    await settle(setup);
    // Each inbox row starts with its marker and ends with its id's digit
    // (the narrow lane clips the middle of "? extend p1 [a]pprove [r]eject").
    const markers = Object.fromEntries(
      setup
        .captureCharFrame()
        .split("\n")
        .map((line) => /┃ ([?▸])\S*([123])\s*$/.exec(line))
        .filter((m): m is RegExpExecArray => m !== null)
        .map((m) => [m[2], m[1]]),
    );
    expect(markers).toEqual({ "1": "?", "2": "?", "3": "▸" });
    setup.renderer.destroy();
  });
});
