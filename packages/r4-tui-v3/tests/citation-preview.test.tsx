/**
 * Knowledge plan K1.4 (2026-10-24): a reply's citation chip opens a preview
 * of its passage. `o` on the focused reply opens the first source; the
 * preview asks the bridge for the passage (`citation.open`, by the session
 * label) and falls back to the row's preview, saying why, when it cannot.
 */
import { afterEach, beforeEach, describe, expect, it, spyOn } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { citationChunk } from "../src/adapters/companion-chat";
import { getCompanionBridge } from "../src/bridge";
import {
  citationBlockLabel,
  citationChip,
  citationSid,
  closeCitationPreview,
  openCitationPreview,
  stepCitationPreview,
} from "../src/lib/citation-preview";
import {
  previewFooter,
  previewHeader,
  previewLines,
  previewWidth,
  previewWindow,
  scrolledTop,
} from "../src/components/citation-preview-overlay";
import { STORE_LAYERS } from "../src/keymap/store-claims";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { chatInputFocused } from "../src/lib/chat-input-focus";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { Citation } from "../src/types";

const CITES: Citation[] = [
  { artifactId: "art-1", kind: "passage", title: "[S1] paper-1.pdf", contentPreview: "first preview", sid: "S1" },
  { artifactId: "art-2", kind: "passage", title: "[S2] paper-2.pdf", contentPreview: "second preview", sid: "S2" },
];

const savedBackend = process.env.R4_TUI_BACKEND;

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  delete process.env.R4_TUI_BACKEND;
  resetWorkspace();
});

afterEach(() => {
  closeCitationPreview();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
  resetWorkspace();
});

describe("citation rows", () => {
  it("carry the session label from the bridge", () => {
    const chunk = citationChunk([{ sid: "S3", artifact_id: "a", title: "x.pdf", kind: "retrieved", preview: "p" }]);
    expect(chunk?.citations?.[0]).toEqual({
      artifactId: "a",
      kind: "retrieved",
      title: "[S3] x.pdf",
      contentPreview: "p",
      sid: "S3",
    });
  });

  it("a row without a sid takes the label its title starts with", () => {
    expect(citationSid({ artifactId: "a", kind: "passage", title: "[S12] y.pdf", contentPreview: "" })).toBe("S12");
    expect(citationSid({ artifactId: "a", kind: "passage", title: "y.pdf", contentPreview: "" })).toBe("");
  });

  it("an uncited turn's rows are headed as retrieved, not cited", () => {
    expect(citationBlockLabel(CITES)).toBe("Citations:");
    expect(citationBlockLabel(CITES.map((c) => ({ ...c, kind: "retrieved" })))).toBe("Retrieved (not cited):");
  });

  it("the reply header counts only what the answer cited (review of K1 part A)", () => {
    expect(citationChip(CITES)).toBe("cited 2");
    expect(citationChip(CITES.map((c) => ({ ...c, kind: "retrieved" })))).toBe("retrieved 2");
    expect(citationChip([CITES[0]!, { ...CITES[1]!, kind: "retrieved" }])).toBe("cited 1");
    expect(citationChip(undefined)).toBe("");
  });

  it("carry the passage handle, so a chip opens after the label is gone", () => {
    const chunk = citationChunk([
      { sid: "S3", artifact_id: "a", title: "x.pdf", preview: "p", source_id: "node-3", project_id: "proj-B" },
    ]);
    expect(chunk?.citations?.[0]?.sourceId).toBe("node-3");
    expect(chunk?.citations?.[0]?.projectId).toBe("proj-B");
  });
});

describe("the preview", () => {
  it("fetches the passage by its label and shows it with its chunk range", async () => {
    const request = spyOn(getCompanionBridge(), "request").mockResolvedValue({
      sid: "S1",
      title: "paper-1.pdf",
      artifact_id: "art-1",
      source_id: "node-1",
      text: "the whole passage",
      window: 1,
      chunks: [2, 4],
    });
    try {
      await openCitationPreview(CITES, 0);
      expect(request).toHaveBeenCalledWith("citation.open", { sid: "S1", window: 1 }, 15_000);
      const state = workspace.citationPreview!;
      expect([state.index, state.text, state.note, [...state.chunks]]).toEqual([0, "the whole passage", "", [2, 4]]);
      expect(previewHeader(workspace.citationPreview!)).toBe("Source 1/2 · [S1] paper-1.pdf · cited · chunks 2–4");
    } finally {
      request.mockRestore();
    }
  });

  it("sends the passage handle with the label", async () => {
    const request = spyOn(getCompanionBridge(), "request").mockResolvedValue({ text: "t", chunks: [] });
    try {
      const cite = { ...CITES[0]!, sourceId: "node-1", projectId: "proj-B" };
      await openCitationPreview([cite], 0);
      expect(request).toHaveBeenCalledWith(
        "citation.open",
        { sid: "S1", window: 1, source_id: "node-1", title: "[S1] paper-1.pdf", project_id: "proj-B" },
        15_000,
      );
    } finally {
      request.mockRestore();
    }
  });

  it("steps to the next source and asks for its passage", async () => {
    const request = spyOn(getCompanionBridge(), "request").mockImplementation(async (_m, params) => ({
      text: `passage ${(params as { sid: string }).sid}`,
      chunks: [],
    }));
    try {
      await openCitationPreview(CITES, 0);
      await stepCitationPreview(1);
      expect(workspace.citationPreview?.index).toBe(1);
      expect(workspace.citationPreview?.text).toBe("passage S2");
      await stepCitationPreview(1); // stays at the end
      expect(request).toHaveBeenCalledTimes(2);
    } finally {
      request.mockRestore();
    }
  });

  it("a failed fetch shows the row's preview and says why", async () => {
    const request = spyOn(getCompanionBridge(), "request").mockRejectedValue(new Error("not visible"));
    try {
      await openCitationPreview(CITES, 1);
      expect([workspace.citationPreview?.text, workspace.citationPreview?.note]).toEqual([
        "second preview",
        "preview only: not visible",
      ]);
    } finally {
      request.mockRestore();
    }
  });

  it("offline it shows the row's preview without asking the bridge", async () => {
    process.env.R4_TUI_BACKEND = "offline";
    const request = spyOn(getCompanionBridge(), "request");
    try {
      await openCitationPreview(CITES, 0);
      expect(request).not.toHaveBeenCalled();
      expect(workspace.citationPreview?.text).toBe("first preview");
      expect(workspace.citationPreview?.note).toContain("needs a live backend");
    } finally {
      request.mockRestore();
    }
  });

  it("an answer for a source the user left is ignored", async () => {
    let release: (v: unknown) => void = () => {};
    const request = spyOn(getCompanionBridge(), "request").mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    );
    try {
      const first = openCitationPreview(CITES, 0);
      closeCitationPreview();
      release({ text: "late", chunks: [] });
      await first;
      expect(workspace.citationPreview).toBeNull();
    } finally {
      request.mockRestore();
    }
  });

  it("long text is wrapped to the overlay's width, at word boundaries, every row kept", () => {
    // Review of K1 part A: a fixed 96-column wrap inside an 80%-wide overlay
    // lost each line's tail below ~124 columns, and rows past 16 were cut.
    expect(previewWidth(100)).toBe(78);
    expect(previewWidth(160)).toBe(126);
    const lines = previewLines(`${"word ".repeat(40)}END`, 30);
    expect(lines.every((l) => l.length <= 30)).toBe(true);
    expect(lines.at(-1)?.endsWith("END")).toBe(true);
    expect(lines.some((l) => /wor$|^d /.test(l))).toBe(false);
    expect(previewLines("x\n".repeat(40), 30).length).toBe(41);
  });

  it("scrolls through a passage longer than the body", () => {
    const lines = Array.from({ length: 40 }, (_, i) => `row ${i + 1}`);
    expect(scrolledTop(0, -1, 40)).toBe(0);
    expect(scrolledTop(0, 15, 40)).toBe(15);
    expect(scrolledTop(20, 15, 40)).toBe(24);
    expect(previewWindow(lines, 24)).toEqual(lines.slice(24, 40));
    expect(previewFooter(24, 40)).toContain("lines 25–40 of 40");
    expect(previewFooter(0, 3)).not.toContain("scroll");
  });

  it("holds the keyboard while open and Esc closes it", async () => {
    process.env.R4_TUI_BACKEND = "offline";
    await openCitationPreview(CITES, 0);
    expect(chatInputFocused()).toBe(false);
    const layer = STORE_LAYERS.find((l) => l.owner === "citation-preview")!;
    expect(layer.open()).toBe(true);
    expect(layer.onEscape?.()).toBe(true);
    expect(workspace.citationPreview).toBeNull();
  });
});

describe("the chip in the transcript", () => {
  it("o on a reply with sources opens the preview over the chat", async () => {
    process.env.R4_TUI_BACKEND = "offline";
    setWorkspace("chatMessages", [
      { id: "u1", role: "user", content: "does vitamin D help?", timestamp: "2026-10-24T10:00:00Z" },
      {
        id: "a1",
        role: "assistant",
        content: "It lowers fracture risk [S1].",
        timestamp: "2026-10-24T10:00:01Z",
        citations: CITES,
      },
    ]);
    const setup = await testRender(() => <App />, { width: 160, height: 50 });
    try {
      await setup.renderOnce();
      actions.closeAllOverlays();
      actions.enterMessageCursor();
      await setup.renderOnce();
      expect(setup.captureCharFrame()).toContain("o open source");
      setup.mockInput.pressKey("o");
      for (let i = 0; i < 4; i++) await setup.renderOnce();
      const frame = setup.captureCharFrame();
      expect(workspace.citationPreview?.index).toBe(0);
      expect(frame).toContain("Source 1/2 · [S1] paper-1.pdf · cited");
      expect(frame).toContain("first preview");
    } finally {
      setup.renderer.destroy();
    }
  });
});

describe("the preview at a common terminal width (review of K1 part A)", () => {
  const LONG = `ALPHA ${"lorem ".repeat(14)}OMEGA_END_OF_LINE`;

  async function previewAt(width: number, text: string) {
    process.env.R4_TUI_BACKEND = "offline";
    setWorkspace("chatMessages", [
      { id: "u1", role: "user", content: "q", timestamp: "2026-10-24T10:00:00Z" },
      {
        id: "a1",
        role: "assistant",
        content: "nothing cited here",
        timestamp: "2026-10-24T10:00:01Z",
        citations: [
          { artifactId: "a", kind: "retrieved", title: "[S1] p.pdf", contentPreview: text, sid: "S1" },
          { artifactId: "b", kind: "retrieved", title: "[S2] q.pdf", contentPreview: "y", sid: "S2" },
        ],
      },
    ]);
    const setup = await testRender(() => <App />, { width, height: 50 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    return setup;
  }

  it("shows the whole line at 100 columns and says retrieved in the header", async () => {
    const setup = await previewAt(100, LONG);
    try {
      expect(setup.captureCharFrame()).toContain("retrieved 2");
      expect(setup.captureCharFrame()).not.toContain("cited 2");
      actions.enterMessageCursor();
      setup.mockInput.pressKey("o");
      for (let i = 0; i < 4; i++) await setup.renderOnce();
      expect(setup.captureCharFrame()).toContain("OMEGA_END_OF_LINE");
    } finally {
      setup.renderer.destroy();
    }
  });

  it("PgDn shows the rows past the first screen", async () => {
    const rows = Array.from({ length: 30 }, (_, i) => `passage row ${i + 1}`).join("\n");
    const setup = await previewAt(100, rows);
    try {
      actions.enterMessageCursor();
      setup.mockInput.pressKey("o");
      for (let i = 0; i < 4; i++) await setup.renderOnce();
      expect(setup.captureCharFrame()).not.toContain("passage row 25");
      setup.mockInput.pressKey("\u001B[6~"); // PgDn (not in the mock's KeyCodes)
      for (let i = 0; i < 3; i++) await setup.renderOnce();
      expect(setup.captureCharFrame()).toContain("passage row 25");
    } finally {
      setup.renderer.destroy();
    }
  });
});
