/**
 * Visibility wave 2026-10-19 — transcript prose wraps instead of clipping.
 *
 * Before this wave, MarkdownBlock painted every source line as ONE terminal
 * row with overflow=hidden: a list item longer than the chat column was cut
 * at the right edge and its tail never appeared in the transcript (the
 * maintainer pasted exactly such an agent reply: "**4. Also note:** …" cut
 * mid-sentence). wrapProseLine() + the MarkdownBlock `wrapWidth` prop wrap
 * prose at word boundaries with a hanging indent under list/quote markers.
 *
 * Pins here fail on the pre-wave code (one clipped row; no wrapWidth prop).
 */

import { describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { wrapProseLine } from "../src/lib/markdown-wrap";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";

describe("wrapProseLine (pure)", () => {
  it("passes short lines through untouched", () => {
    expect(wrapProseLine("short line", 40)).toEqual(["short line"]);
    expect(wrapProseLine("", 40)).toEqual([""]);
  });

  it("wraps a long line at word boundaries, never mid-word", () => {
    const rows = wrapProseLine("alpha beta gamma delta epsilon", 12);
    expect(rows.length).toBeGreaterThan(1);
    for (const row of rows) {
      expect(row.length).toBeLessThanOrEqual(12);
      expect(row).not.toMatch(/^ | $/);
    }
    expect(rows.join(" ").replace(/ +/g, " ")).toContain("alpha beta gamma delta epsilon");
  });

  it("indents continuations under the bullet text (hanging indent)", () => {
    const rows = wrapProseLine("- one two three four five six", 14);
    expect(rows[0]).toBe("- one two");
    for (const row of rows.slice(1)) expect(row.startsWith("  ")).toBe(true);
  });

  it("indents continuations under the ordered-list marker width", () => {
    const rows = wrapProseLine("10. alpha beta gamma delta epsilon", 14);
    expect(rows[0]).toBe("10. alpha beta");
    for (const row of rows.slice(1)) expect(row.startsWith("    ")).toBe(true);
  });

  it("treats a leading blockquote marker as the hang position", () => {
    const rows = wrapProseLine("> quoted words that run long here", 16);
    expect(rows[0]).toBe("> quoted words");
    for (const row of rows.slice(1)) expect(row.startsWith("  ")).toBe(true);
  });

  it("hard-breaks a single token wider than the column (URL-ish)", () => {
    const rows = wrapProseLine("see https://example.com/abcdefgh now", 10);
    for (const row of rows) expect(row.length).toBeLessThanOrEqual(10);
    expect(rows.join(" ").replace(/ /g, "")).toContain("https://example.com/abcdefgh");
  });

  it("drops the hanging indent when the marker would eat half the column", () => {
    const rows = wrapProseLine("12345) alpha beta gamma delta", 12);
    for (const row of rows) expect(row.length).toBeLessThanOrEqual(12);
    expect(rows.some((r) => r.startsWith("       "))).toBe(false);
  });

  it("no-op below the wrap floor (width < 8)", () => {
    expect(wrapProseLine("some longer line here", 5)).toEqual(["some longer line here"]);
  });
});

describe("MarkdownBlock wrapWidth", () => {
  const LONG =
    "**4. Also note:** workflow_describe reports both as scope user — the project-scope re-registration is not reflected here. Worth verifying before acting on it.";

  it("wraps a long prose line instead of clipping it at the right edge", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content={LONG} streaming={false} wrapWidth={40} />,
      { width: 44, height: 12 },
    );
    await setup.renderOnce();
    const rows = setup
      .captureCharFrame()
      .split("\n")
      .map((l) => l.trimEnd())
      .filter(Boolean);
    // Old code: ONE row hard-clipped at ~44 cols, tail gone forever.
    expect(rows.length).toBeGreaterThan(1);
    for (const row of rows) expect(row.length).toBeLessThanOrEqual(41);
    // The whole sentence survives — especially its tail (was the lost part).
    const joined = rows.join(" ").replace(/ +/g, " ");
    expect(joined).toContain("Worth verifying before acting on it.");
    expect(joined).not.toContain("…");
    setup.renderer.destroy();
  });

  it("keeps the legacy one-row contract when wrapWidth is absent", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content={LONG} streaming={false} />,
      { width: 44, height: 12 },
    );
    await setup.renderOnce();
    const rows = setup
      .captureCharFrame()
      .split("\n")
      .map((l) => l.trimEnd())
      .filter(Boolean);
    expect(rows.length).toBe(1);
    setup.renderer.destroy();
  });

  it("never wraps fenced code rows (horizontal clip stays the code contract)", async () => {
    const bt = String.fromCharCode(96);
    const fence = `${bt.repeat(3)}js\nconst label = "a very long unbroken code line that must stay one row";\n${bt.repeat(3)}`;
    const setup = await testRender(
      () => <MarkdownBlock content={fence} streaming={false} wrapWidth={30} />,
      { width: 40, height: 12 },
    );
    await setup.renderOnce();
    const rows = setup
      .captureCharFrame()
      .split("\n")
      .map((l) => l.trimEnd())
      .filter(Boolean);
    // lang chip + exactly ONE (clipped) code row — no wrapped continuation.
    expect(rows.length).toBe(2);
    setup.renderer.destroy();
  });
});
