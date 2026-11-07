/**
 * K1 wave A integration (knowledge plan 2026-10-24): K1.4's passage preview
 * steps with [ / ] and closes with q; K1.5 bound [ / ] (and kept a / r) as
 * knowledge-pane proposal keys. The preview opens from the chat (`o` on a
 * reply) but did not claim Tab, so Tab moved the focus to the docked
 * knowledge pane underneath it: ] then moved the proposal selection, a
 * approved a proposal the person could not see, and the preview's own keys
 * went dead. The preview now holds Tab while it is open.
 */
import { describe, it, expect, beforeEach, afterEach, spyOn } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { scienceActions, scienceStore, selectedProposal } from "../src/stores/science";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { getCompanionBridge } from "../src/bridge";
import { closeCitationPreview, openCitationPreview } from "../src/lib/citation-preview";
import type { Citation } from "../src/types";

type Setup = Awaited<ReturnType<typeof testRender>>;

const CITES: Citation[] = [
  { artifactId: "art-1", kind: "passage", title: "[S1] paper-1.pdf", contentPreview: "first", sid: "S1" },
  { artifactId: "art-2", kind: "passage", title: "[S2] paper-2.pdf", contentPreview: "second", sid: "S2" },
];

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
  closeCitationPreview();
  scienceActions.reset();
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

/** The docked knowledge pane with two pending proposals, the chat focused,
 * and the passage preview open over both. */
async function mountPreviewOverInbox(decided: unknown[]): Promise<Setup> {
  const setup = await testRender(() => <App />, { width: 160, height: 50 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  setWorkspace("platform", {
    graphContent: async () => ({ graph_id: "gA", nodes: [{ id: "n1", label: "A node" }], edges: [] }),
    graphProposals: async () => ({
      proposals: ["p1", "p2"].map((id) => ({ proposal_id: id, graph_id: "gA", summary: `extend ${id}` })),
    }),
    decideGraphProposal: async (id: string, approved: boolean) => {
      decided.push([id, approved]);
      return {};
    },
  } as never);
  await scienceActions.loadGraphContent("gA");
  await scienceActions.refreshProposals("gA");
  actions.openPane("knowledge");
  setWorkspace("paneFocus", false);
  await openCitationPreview(CITES, 0);
  await settle(setup);
  return setup;
}

describe("the passage preview over the proposal inbox (K1.4 + K1.5)", () => {
  it("Tab keeps the keys in the preview: ] steps it, a decides nothing", async () => {
    const request = spyOn(getCompanionBridge(), "request").mockResolvedValue({ text: "passage", chunks: [] });
    const decided: unknown[] = [];
    try {
      const setup = await mountPreviewOverInbox(decided);
      expect(scienceStore.proposals.length).toBe(2);
      setup.mockInput.pressTab();
      await settle(setup);
      expect(workspace.paneFocus).toBe(false);
      setup.mockInput.pressKey("]");
      await settle(setup);
      expect(workspace.citationPreview?.index).toBe(1);
      expect(selectedProposal()?.proposalId).toBe("p1");
      setup.mockInput.pressKey("a");
      await settle(setup);
      expect(decided).toEqual([]);
      setup.renderer.destroy();
    } finally {
      request.mockRestore();
    }
  });

  it("a click on the pane under the preview does not reach the inbox (review of K1 part A)", async () => {
    // detail-pane onMouseDown sets paneFocus while the preview stays open:
    // ] moved the hidden selection to p2 and a approved it.
    const request = spyOn(getCompanionBridge(), "request").mockResolvedValue({ text: "passage", chunks: [] });
    const decided: unknown[] = [];
    try {
      const setup = await mountPreviewOverInbox(decided);
      setWorkspace("paneFocus", true);
      await settle(setup);
      setup.mockInput.pressKey("]");
      await settle(setup);
      expect(selectedProposal()?.proposalId).toBe("p1");
      setup.mockInput.pressKey("a");
      await settle(setup);
      setup.mockInput.pressKey("r");
      await settle(setup);
      expect(decided).toEqual([]);
      expect(workspace.citationPreview).not.toBeNull();
      setup.renderer.destroy();
    } finally {
      request.mockRestore();
    }
  });

  it("q closes the preview; then Tab and ] move the proposal selection again", async () => {
    const request = spyOn(getCompanionBridge(), "request").mockResolvedValue({ text: "passage", chunks: [] });
    const decided: unknown[] = [];
    try {
      const setup = await mountPreviewOverInbox(decided);
      setup.mockInput.pressKey("q");
      await settle(setup);
      expect(workspace.citationPreview).toBeNull();
      setup.mockInput.pressTab();
      await settle(setup);
      expect(workspace.paneFocus).toBe(true);
      setup.mockInput.pressKey("]");
      await settle(setup);
      expect(selectedProposal()?.proposalId).toBe("p2");
      setup.renderer.destroy();
    } finally {
      request.mockRestore();
    }
  });
});
