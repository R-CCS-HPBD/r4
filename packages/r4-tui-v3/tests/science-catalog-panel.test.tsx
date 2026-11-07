/**
 * /catalog pane (stabilization 2026-10-21, panes-overlays#5/#6, slash-b#6):
 * the header and asset rows lay their texts side by side (a column box of
 * height 1 overprinted them: "sc0eassetstalog"), and a focused pane owns
 * its keys even with zero rows — `/` opens the advertised search row, the
 * typed query reaches it, Enter runs the search.
 */
import { afterEach, beforeEach, describe, expect, it, spyOn } from "bun:test";
import { testRender } from "@opentui/solid";
import { ScienceCatalogPanel } from "../src/components/science-catalog-panel";
import { resetWorkspace, setWorkspace } from "../src/stores";
import { scienceActions } from "../src/stores/science";

type Setup = Awaited<ReturnType<typeof testRender>>;
let setup: Setup | null = null;

async function mount(width = 60): Promise<Setup> {
  setup = await testRender(() => <ScienceCatalogPanel />, { width, height: 16 });
  await setup.renderOnce();
  return setup;
}

async function settle(s: Setup): Promise<void> {
  await new Promise((r) => setTimeout(r, 20));
  await s.renderOnce();
}

beforeEach(() => {
  resetWorkspace();
  scienceActions.reset();
  setWorkspace("paneFocus", true);
});

afterEach(() => {
  setup?.renderer.destroy();
  setup = null;
  scienceActions.reset();
  resetWorkspace();
});

describe("catalog pane header", () => {
  it("reads 'science catalog  0 assets' on one line, not overprinted", async () => {
    const s = await mount();
    const frame = s.captureCharFrame();
    expect(frame).toContain("science catalog  0 assets");
    expect(frame).not.toContain("sc0eassetstalog");
  });

  it("an asset row keeps its badge state after the row text", async () => {
    setWorkspace("platform", {
      catalogSearch: async () => ({
        query: "cell",
        results: [{ s3_key: "raw/cells.tif", state: "ready" }],
      }),
    } as never);
    await scienceActions.searchCatalog("cell");
    const s = await mount();
    const row = s.captureCharFrame().split("\n").find((l) => l.includes("raw/cells.tif")) ?? "";
    expect(row).toMatch(/raw\/cells\.tif.*NO_MINT/);
  });
});

describe("catalog pane keys with zero rows", () => {
  it("'/' opens the search, typed text lands in it, Enter searches", async () => {
    const search = spyOn(scienceActions, "searchCatalog").mockResolvedValue(undefined);
    try {
      const s = await mount();
      expect(s.captureCharFrame()).toContain("/ search catalogue…");
      s.mockInput.pressKey("/");
      await settle(s);
      for (const ch of "cell") s.mockInput.pressKey(ch);
      await settle(s);
      expect(s.captureCharFrame()).toContain("› cell█");
      s.mockInput.pressEnter();
      await settle(s);
      expect(search).toHaveBeenCalledWith("cell");
    } finally {
      search.mockRestore();
    }
  });
});
