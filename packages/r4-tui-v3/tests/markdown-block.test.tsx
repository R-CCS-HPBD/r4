import { test, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";

const PARA = "**bold** and `code` plain";
const BT = String.fromCharCode(96);
const FENCE = BT + BT + BT;
const FENCED = `${FENCE}js\nconst x = 1;\n${FENCE}`;
const MIXED = `${PARA}\n\n${FENCED}`;

function flat(frame: string): string {
  return frame
    .split("\n")
    .map((l) => l.trimEnd())
    .filter(Boolean)
    .join(" | ");
}

test("streaming content renders paragraphs through the markdown engine", async () => {
  const setup = await testRender(
    () => <MarkdownBlock content={MIXED} streaming={true} />,
    { width: 60, height: 20 },
  );
  // top-level block mode: the first pass builds child renderables, the
  // second paints them (live app paints continuously, so this is test-only).
  await setup.renderOnce();
  await setup.renderOnce();
  const frame = flat(setup.captureCharFrame());
  expect(frame).toContain("bold");
  expect(frame).toContain("plain");
  setup.renderer.destroy();
});

test("finalized content keeps every line via per-line rendering (no content loss)", async () => {
  const setup = await testRender(
    () => <MarkdownBlock content={MIXED} streaming={false} />,
    { width: 60, height: 20 },
  );
  await setup.renderOnce();
  const frame = flat(setup.captureCharFrame());
  // per-line legacy path preserves every line verbatim (markers included)
  expect(frame).toContain("**bold** and `code` plain");
  expect(frame).toContain("const x = 1;");
  setup.renderer.destroy();
});

test("markdown engine can be disabled to keep the plain per-line path while streaming", async () => {
  const setup = await testRender(
    () => (
      <MarkdownBlock content={PARA} streaming={true} streamingMarkdown={false} />
    ),
    { width: 60, height: 10 },
  );
  await setup.renderOnce();
  const frame = flat(setup.captureCharFrame());
  expect(frame).toContain("**bold** and `code` plain");
  setup.renderer.destroy();
});

test("empty content renders no leaked text (dim placeholder row only)", async () => {
  const setup = await testRender(() => <MarkdownBlock content="" streaming dimFg="#8a87a8" />, {
    width: 40,
    height: 10,
  });
  await setup.renderOnce();
  // Contract: no markdown engine output and no leftover content text; the
  // single-space row keeps transcript rhythm in production palettes.
  expect(flat(setup.captureCharFrame())).toBe("");
  setup.renderer.destroy();
});

test("cached syntax style is reused across blocks", async () => {
  const { markdownSyntaxStyle } = await import("../src/ui/MarkdownBlock");
  const a = markdownSyntaxStyle();
  const b = markdownSyntaxStyle();
  expect(a).toBe(b);
});
