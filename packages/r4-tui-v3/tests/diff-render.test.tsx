import { describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { DiffBlock } from "../src/ui/DiffBlock";
import { ParallelDiffBlock } from "../src/ui/ParallelDiffBlock";

const DIFF = [
  "--- a/f.py",
  "+++ b/f.py",
  "@@ -1,3 +1,4 @@",
  " alpha",
  "-beta",
  "+BETA = 42",
  " ctx_tail",
].join("\n");

type Span = { text: string; fg?: unknown; bg?: unknown };
type Frame = { lines: { spans: Span[] }[] };

function spans(frame: Frame): Span[] {
  return frame.lines.flatMap((l) => l.spans);
}

function flat(frame: Frame): string {
  return spans(frame)
    .map((s) => s.text)
    .join("⏎");
}

describe("DiffBlock (unified opencode look)", () => {
  test("gutter numbers + tinted add/del rows + truncation marker", async () => {
    const setup = await testRender(
      () => <DiffBlock content={DIFF} width={60} filePath="f.py" />,
      { width: 60, height: 20 },
    );
    await setup.renderOnce();
    await setup.renderOnce();
    const frame = setup.captureSpans() as unknown as Frame;
    const text = flat(frame);
    // Gutter numbers for the changed rows (old 2 on del, new 2 on add).
    expect(text).toContain("   2");
    // add/del rows carry a background tint, ctx/meta rows carry none.
    const addSpans = spans(frame).filter((s) => s.text.includes("BETA"));
    const delSpans = spans(frame).filter((s) => s.text.includes("beta") && !s.text.includes("BETA"));
    expect(addSpans.some((s) => s.bg !== undefined && s.bg !== null)).toBe(true);
    expect(delSpans.some((s) => s.bg !== undefined && s.bg !== null)).toBe(true);
    // Sign column present.
    expect(text).toContain("+ ");
    expect(text).toContain("- ");
    setup.renderer.destroy();
  });

  test("long rows cut with … instead of vanishing mid-word", async () => {
    const longLine = `+${"x".repeat(200)}`;
    const setup = await testRender(
      () => <DiffBlock content={`--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n ${longLine.slice(1)}\n${longLine}`} width={40} />,
      { width: 40, height: 10 },
    );
    await setup.renderOnce();
    const frame = setup.captureSpans() as unknown as Frame;
    expect(flat(frame)).toContain("…");
    setup.renderer.destroy();
  });
});

describe("ParallelDiffBlock (split opencode look)", () => {
  test("both cells tinted per side with own gutters, width-bounded", async () => {
    const setup = await testRender(
      () => <ParallelDiffBlock content={DIFF} width={120} filePath="f.py" />,
      { width: 130, height: 20 },
    );
    await setup.renderOnce();
    await setup.renderOnce();
    const frame = setup.captureSpans() as unknown as Frame;
    const sps = spans(frame);
    const delCell = sps.filter((s) => s.text.includes("beta") && !s.text.includes("BETA"));
    const addCell = sps.filter((s) => s.text.includes("BETA"));
    expect(delCell.some((s) => s.bg !== undefined && s.bg !== null)).toBe(true);
    expect(addCell.some((s) => s.bg !== undefined && s.bg !== null)).toBe(true);
    // No whitespace sprawl: CONTENT spans (non-space chars) stay within the
    // cell budget — background fill spans are renderer paint, not content.
    const budget = Math.floor((120 - 1) / 2) + 4; // cell + gutter slack
    const content = sps.filter((s) => s.text.trim().length > 0);
    expect(content.every((s) => s.text.length <= budget)).toBe(true);
    setup.renderer.destroy();
  });
});
