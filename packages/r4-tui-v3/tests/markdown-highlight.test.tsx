import { test, expect, describe } from "bun:test";
import { testRender } from "@opentui/solid";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";
import { normalizeShikiLang } from "../src/lib/code-shiki";

const BT = String.fromCharCode(96);
const F = BT + BT + BT;
const PY = [
  "Here is code:",
  `${F}python`,
  'def f(s="a"): # init',
  "    return 42",
  F,
  "done.",
].join("\n");

interface SpanLike {
  text: string;
  fg?: string;
}

type FrameLike = { lines: { spans: SpanLike[] }[] };

function spansOf(frame: FrameLike): { text: string; fg?: string }[] {
  return frame.lines.flatMap((l) =>
    l.spans.map((s) => ({ text: s.text, fg: s.fg === undefined ? undefined : String(s.fg) })),
  );
}

/** fg values stringify as rgba(r,g,b,a); collect non-default (non-white) tokens. */
function paintedTokenTexts(frame: FrameLike): Map<string, string> {
  const painted = new Map<string, string>();
  for (const span of spansOf(frame)) {
    if (span.fg && !span.fg.startsWith("rgba(1.00, 1.00, 1.00")) {
      painted.set(span.text, span.fg);
    }
  }
  return painted;
}

describe("shiki fence highlighting (streaming + finalized share one path)", () => {
  for (const streaming of [false, true]) {
    test(`streaming=${streaming}: python fence paints keyword/string/comment/number`, async () => {
      const setup = await testRender(
        () => <MarkdownBlock content={PY} streaming={streaming} />,
        { width: 60, height: 20 },
      );
      await setup.renderOnce();
      await setup.renderOnce();
      const frame = setup.captureSpans() as unknown as FrameLike;
      const painted = paintedTokenTexts(frame);
      // Keyword colored and distinct from number color.
      expect(painted.has("def")).toBe(true);
      expect(painted.has("42")).toBe(true);
      expect(painted.get("def")).not.toBe(painted.get("42"));
      // Comment text visible and colored (not blank rows anymore).
      expect([...painted.keys()].some((t) => t.includes("# init"))).toBe(true);
      // Fence content actually rendered (was blank rows on the old native path).
      const chars = setup.captureCharFrame();
      expect(chars).toContain("def f(s=");
      expect(chars).toContain("return 42");
      setup.renderer.destroy();
    });
  }

  test("fence renders a language chip header", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content={PY} streaming={false} />,
      { width: 60, height: 20 },
    );
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("▍python");
    setup.renderer.destroy();
  });

  test("unknown language survives via regex fallback (no crash, text kept)", async () => {
    const content = `${F}cobol\nDISPLAY "HELLO".\n${F}`;
    const setup = await testRender(
      () => <MarkdownBlock content={content} streaming={true} />,
      { width: 60, height: 10 },
    );
    await setup.renderOnce();
    const chars = setup.captureCharFrame();
    expect(chars).toContain('DISPLAY "HELLO".');
    setup.renderer.destroy();
  });

  test("unclosed (still-streaming) fence keeps rows colored as code", async () => {
    const content = `${F}python\ndef f():\n    return 1`;
    const setup = await testRender(
      () => <MarkdownBlock content={content} streaming={true} />,
      { width: 60, height: 10 },
    );
    await setup.renderOnce();
    const frame = setup.captureSpans() as unknown as FrameLike;
    const painted = paintedTokenTexts(frame);
    expect(painted.has("def")).toBe(true);
    expect(setup.captureCharFrame()).toContain("return 1");
    setup.renderer.destroy();
  });

  test("regex fallback also paints when shiki is disabled (lang never loads)", async () => {
    // 'not-a-lang' never loads → RegexCodeLine path must still render text.
    const content = `${F}not-a-lang\nplain row\n${F}`;
    const setup = await testRender(
      () => <MarkdownBlock content={content} streaming={false} />,
      { width: 60, height: 10 },
    );
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("plain row");
    setup.renderer.destroy();
  });
});

describe("normalizeShikiLang", () => {
  test("aliases map to shiki ids", () => {
    expect(normalizeShikiLang("py")).toBe("python");
    expect(normalizeShikiLang("sh")).toBe("bash");
    expect(normalizeShikiLang("yml")).toBe("yaml");
    expect(normalizeShikiLang("ts")).toBe("typescript");
  });
  test("empty/absent → null", () => {
    expect(normalizeShikiLang("")).toBe(null);
    expect(normalizeShikiLang(null)).toBe(null);
    expect(normalizeShikiLang(undefined)).toBe(null);
  });
});
