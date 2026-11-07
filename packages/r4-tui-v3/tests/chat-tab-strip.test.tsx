/**
 * ChatTabStrip live render pins: the overflow marquee must actually paint a
 * moved window on the ACTIVE tab after the dwell (the "scrolling is not
 * realized" report, 2026-09-30) — background tabs ellipsize instead.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { ChatTabStrip } from "../src/components/chat-tab-strip";
import { actions, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";

beforeEach(() => {
  resetTabsRegistryForTests();
  actions.resetWorkspace();
});

describe("ChatTabStrip live render", () => {
  it("active overflow title moves after the dwell; background ellipsizes", async () => {
    const longTitle = "alpha beta gamma delta epsilon zeta eta theta iota kappa";
    actions.tabCreate({ sessionId: "bg", title: longTitle });
    actions.tabCreate({ sessionId: "fg", title: longTitle });
    // Active = second tab. First tab is background → static ellipsis.
    const setup = await testRender(() => <ChatTabStrip />, { width: 80, height: 4 });
    await setup.renderOnce();
    const frame0 = setup.captureCharFrame();
    // Tab 1 = the boot tab (fallback title); tabs 2/3 carry the long name.
    expect(frame0).toContain("2:alpha beta");
    expect(frame0).toContain("3:alpha beta");
    // Background tab ellipsizes; active tab shows the window start.
    const cell = (frame: string, ordinal: number): string =>
      new RegExp(`${ordinal}:([^×\\n]+)`, "u").exec(frame)?.[1] ?? "";
    // Background tab ellipsizes; active tab shows the window start uncut.
    expect(cell(frame0, 2)).toContain("…");
    expect(cell(frame0, 3)).not.toContain("…");
    // Wait past the dwell (1.4 s) + a few ticks: the ACTIVE window must move.
    await Bun.sleep(1900);
    await setup.renderOnce();
    const frame1 = setup.captureCharFrame();
    expect(cell(frame1, 3)).not.toBe(cell(frame0, 3));
    // Background cell unchanged (still-tier: only the active tab animates).
    expect(cell(frame1, 2)).toBe(cell(frame0, 2));
    setup.renderer.destroy();
  }, 10_000);

  it("short titles never animate (stillness gate)", async () => {
    const setup = await testRender(() => <ChatTabStrip />, { width: 120, height: 4 });
    await setup.renderOnce();
    const frame0 = setup.captureCharFrame();
    await Bun.sleep(700);
    const frame1 = setup.captureCharFrame();
    expect(frame1).toBe(frame0);
    setup.renderer.destroy();
  });

  // tabs-sessions#9 (2026-10-21): the per-tab cap left out each cell's
  // paddingLeft and the row gaps, the row overflowed by ~2 columns per tab,
  // and the squeeze clipped the `N:` colon and the clickable ×.
  for (const [width, counts] of [[70, [3, 5, 8]], [120, [5, 8]]] as const) {
    for (const count of counts) {
      it(`every cell keeps N: and × at ${width} columns with ${count} tabs`, async () => {
        const names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"];
        actions.setTabMeta(workspace.chatTabs[0]!.id, { title: `${names[0]} topic question` });
        for (let i = 1; i < count; i++) actions.tabCreate({ title: `${names[i]} topic question` });
        const setup = await testRender(() => <ChatTabStrip />, { width, height: 3 });
        await setup.renderOnce();
        const row = setup.captureCharFrame().split("\n")[0]!;
        for (let i = 1; i <= count; i++) expect(row).toContain(`${i}:`);
        expect([...row].filter((c) => c === "×").length).toBe(count);
        expect(row).toContain("[+ new]");
        setup.renderer.destroy();
      });
    }
  }
});

// w3-tui item 2 (2026-10-21): the strip capped its cells from the DEFAULT
// dock estimate while the chat column follows the dragged pane width
// (paneWidthOverride) — with a wide dragged pane the row overflowed the
// column and the engine's squeeze cut titles mid-word with no ellipsis
// ("3:charl×" for "charlie topic question").
describe("ChatTabStrip in a dragged-pane chat column", () => {
  it("cells size to the dragged chat column: titles ellipsize, never cut silently", async () => {
    const { Shell } = await import("../src/layout/shell");
    const { setWorkspace } = await import("../src/stores");
    const names = ["alpha", "bravo", "charlie", "delta", "echo"];
    actions.setTabMeta(workspace.chatTabs[0]!.id, { title: `${names[0]} topic question` });
    for (let i = 1; i < names.length; i++) actions.tabCreate({ title: `${names[i]} topic question` });
    setWorkspace("paneView", "logs" as never);
    setWorkspace("paneWidthOverride", 80 as never);
    const setup = await testRender(() => <Shell />, { width: 140, height: 30 });
    await setup.renderOnce();
    await setup.renderOnce();
    await setup.renderOnce();
    const row = setup.captureCharFrame().split("\n").find((l) => l.includes("[+ new]")) ?? "";
    setup.renderer.destroy();
    expect([...row].filter((c) => c === "×").length).toBe(names.length);
    // Background tabs (the active, last one marquees its window instead).
    for (let i = 1; i < names.length; i++) {
      const cell = (new RegExp(`${i}:([^×]*)×`, "u").exec(row)?.[1] ?? "").trim();
      const full = `${names[i - 1]} topic question`;
      // Either the whole title or an honest ellipsis — never a silent cut.
      expect(cell === full || cell.endsWith("…")).toBe(true);
    }
  });
});

// c2-tui (2026-10-21): the marquee and the ellipsis cut titles by UTF-16
// code units. A Japanese title (two columns per glyph) came out twice the
// cell width; the text wrapped inside the one-row cell and the strip showed
// a random wrapped piece ("1:ください。", "1:：") padded with blanks. The
// window is measured in display columns now.
describe("ChatTabStrip wide-glyph titles", () => {
  const ja = "日本語のテキスト：地震波形を解析してください。漢字かなカナ混じり ECHO cjk";

  it("the active Japanese title shows its head, never a wrapped tail piece", async () => {
    actions.setTabMeta(workspace.chatTabs[0]!.id, { title: ja });
    const setup = await testRender(() => <ChatTabStrip />, { width: 140, height: 3 });
    await setup.renderOnce();
    const row = setup.captureCharFrame().split("\n")[0]!;
    setup.renderer.destroy();
    const cell = (/1:(.*?)\[\+ new\]/u.exec(row)?.[1] ?? "").replace(/\s+/g, "");
    expect(cell.startsWith("日本語のテキスト：地震")).toBe(true);
  });

  it("a background Japanese title ellipsizes from its head", async () => {
    actions.setTabMeta(workspace.chatTabs[0]!.id, { title: ja });
    actions.tabCreate({ title: "short" });
    const setup = await testRender(() => <ChatTabStrip />, { width: 140, height: 3 });
    await setup.renderOnce();
    const row = setup.captureCharFrame().split("\n")[0]!;
    setup.renderer.destroy();
    const cell = (/1:(.*?)×/u.exec(row)?.[1] ?? "").replace(/\s+/g, "");
    expect(cell.startsWith("日本語のテキスト")).toBe(true);
    expect(cell.endsWith("…")).toBe(true);
  });

  it("marquee and ellipsis windows never paint wider than the cap", async () => {
    const { marqueeSlice, ellipsisSlice } = await import("../src/lib/marquee");
    for (const cap of [1, 2, 5, 9, 20]) {
      expect(Bun.stringWidth(ellipsisSlice(ja, cap))).toBeLessThanOrEqual(cap);
      for (let tick = 0; tick < 20_000; tick += 250) {
        expect(Bun.stringWidth(marqueeSlice(ja, cap, tick))).toBeLessThanOrEqual(cap);
      }
    }
    // The window still travels to the tail and a fitting title is untouched.
    const tails = new Set<string>();
    for (let tick = 0; tick < 20_000; tick += 250) tails.add(marqueeSlice(ja, 20, tick));
    expect([...tails].some((w) => w.endsWith("cjk"))).toBe(true);
    expect(marqueeSlice("漢字", 4, 999)).toBe("漢字");
    expect(ellipsisSlice("漢字", 4)).toBe("漢字");
  });
});
