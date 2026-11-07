/**
 * D-1 (c3-tui 2026-10-21): a huge transcript block renders a head + tail
 * preview around one marker row instead of one renderable per row.
 *
 * On the base, a 2,000-line pasted turn followed by a 10,000-line paste left
 * the console frozen (the draft chip never cleared, typed keys never showed,
 * a resize then failed to allocate a frame buffer and the process exited):
 * MarkdownBlock created a native text renderable per row and OpenTUI walks
 * every renderable each frame. One 580 KB line without a newline took 11 s
 * to wrap (the wrap re-measured the whole rest of the line per row).
 */

import { describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";
import { wrapProseLine } from "../src/lib/markdown-wrap";
import {
  EXPAND_PAGE_ROWS,
  PREVIEW_HEAD_ROWS,
  PREVIEW_TAIL_ROWS,
  RENDER_ROW_CAP,
  RENDER_ROW_HARD_CAP,
  exceedsRowCap,
  hiddenRowsLabel,
  windowRows,
  type RowSlicer,
} from "../src/lib/render-window";

const ONE_ROW: RowSlicer<string> = { rows: () => 1, take: (s) => s };
const lines = (n: number) => Array.from({ length: n }, (_, i) => `row-${i}`);

describe("windowRows (pure)", () => {
  it("renders a block up to the cap in full", () => {
    const w = windowRows(lines(RENDER_ROW_CAP), ONE_ROW);
    expect(w.head.length).toBe(RENDER_ROW_CAP);
    expect(w.hiddenRows).toBe(0);
    expect(w.tail).toEqual([]);
  });

  it("collapses a longer block to head + tail around the hidden middle", () => {
    const w = windowRows(lines(10_000), ONE_ROW);
    expect(w.head.length).toBe(PREVIEW_HEAD_ROWS);
    expect(w.tail.length).toBe(PREVIEW_TAIL_ROWS);
    expect(w.head[0]).toBe("row-0");
    expect(w.tail.at(-1)).toBe("row-9999");
    expect(w.hiddenRows).toBe(10_000 - PREVIEW_HEAD_ROWS - PREVIEW_TAIL_ROWS);
    expect(w.canExpand).toBe(true);
  });

  it("pages more rows in, never past the hard cap", () => {
    const one = windowRows(lines(10_000), ONE_ROW, 1);
    expect(one.head.length).toBe(PREVIEW_HEAD_ROWS + EXPAND_PAGE_ROWS);
    const many = windowRows(lines(10_000), ONE_ROW, 99);
    expect(many.head.length + many.tail.length).toBe(RENDER_ROW_HARD_CAP);
    expect(many.canExpand).toBe(false);
    expect(hiddenRowsLabel(many.hiddenRows, false)).toContain("at most 4,000 rows");
  });

  it("shows everything once a page covers the rest", () => {
    const w = windowRows(lines(900), ONE_ROW, 1);
    expect(w.hiddenRows).toBe(0);
    expect(w.head.length).toBe(900);
  });

  it("cuts a multi-row item at the boundary", () => {
    const code: RowSlicer<string[]> = { rows: (b) => b.length, take: (b, from, to) => b.slice(from, to) };
    const w = windowRows([lines(1000)], code);
    expect(w.head[0]!.length).toBe(PREVIEW_HEAD_ROWS);
    expect(w.tail[0]!.length).toBe(PREVIEW_TAIL_ROWS);
    expect(w.tail[0]!.at(-1)).toBe("row-999");
  });

  it("exceedsRowCap counts source lines", () => {
    expect(exceedsRowCap(lines(RENDER_ROW_CAP).join("\n"))).toBe(false);
    expect(exceedsRowCap(lines(RENDER_ROW_CAP + 1).join("\n"))).toBe(true);
  });
});

describe("MarkdownBlock with a huge block", () => {
  it("renders the head, one marker row and the tail of a 10,000-line paste", async () => {
    const content = lines(10_000).join("\n");
    const setup = await testRender(() => <MarkdownBlock content={content} wrapWidth={60} />, {
      width: 100,
      height: 200,
    });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("row-0");
    expect(frame).toContain(`row-${PREVIEW_HEAD_ROWS - 1}`);
    expect(frame).not.toContain(`row-${PREVIEW_HEAD_ROWS} `);
    expect(frame).toContain("9,840 rows hidden");
    expect(frame).toContain("row-9999");
    // Clicking the marker pages the next rows in.
    const markerY = frame.split("\n").findIndex((l) => l.includes("rows hidden"));
    await setup.mockMouse.click(5, markerY);
    await setup.renderOnce();
    const after = setup.captureCharFrame();
    expect(after).toContain(`row-${PREVIEW_HEAD_ROWS + 10}`);
    setup.renderer.destroy();
  });

  it("keeps a short block whole (no marker)", async () => {
    const setup = await testRender(() => <MarkdownBlock content={lines(50).join("\n")} wrapWidth={60} />, {
      width: 100,
      height: 60,
    });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("row-49");
    expect(frame).not.toContain("rows hidden");
    setup.renderer.destroy();
  });

  it("windows a huge fenced code block too", async () => {
    const content = ["```text", ...lines(5_000), "```"].join("\n");
    const setup = await testRender(() => <MarkdownBlock content={content} wrapWidth={60} />, {
      width: 100,
      height: 200,
    });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("rows hidden");
    expect(frame).toContain("row-4999");
    setup.renderer.destroy();
  });
});

describe("wrapProseLine on one huge unbroken line", () => {
  it("wraps 580 KB without a newline in linear time", () => {
    const started = Date.now();
    const rows = wrapProseLine("x".repeat(580_000), 110);
    expect(Date.now() - started).toBeLessThan(2_000);
    expect(rows.length).toBe(Math.ceil(580_000 / 110));
    expect(rows.every((r) => r.length <= 110)).toBe(true);
  });
});
