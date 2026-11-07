import { test, expect, describe } from "bun:test";
import { testRender } from "@opentui/solid";
import { ModifiedFilesCard } from "../src/components/chat";
import type { ModifiedFile } from "../src/lib/modified-files";

const DIFF = [
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,2 +1,2 @@",
  "-old_line",
  "+new_line",
  " ctx",
].join("\n");

const EDIT_FILE: ModifiedFile = {
  path: "src/a.py",
  tool: "file_edit",
  diffText: DIFF,
  summary: "Edited src/a.py (replaced 1 occurrence)",
};

const WRITE_FILE: ModifiedFile = {
  path: "src/new.py",
  tool: "file_write",
  diffText: null,
  summary: "Written 42 bytes to src/new.py",
  content: "def hello():\n    return 1\n",
};

describe("ModifiedFilesCard — code/diff visible WITHOUT an expand click", () => {
  test("edit diff lines render by default", async () => {
    const setup = await testRender(
      () => <ModifiedFilesCard msgId="m1" files={[EDIT_FILE]} width={80} />,
      { width: 80, height: 30 },
    );
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("✎~ src/a.py");
    expect(frame).toContain("+1 −1");
    // New diff grammar: sign column + line-number gutter, content unclipped.
    expect(frame).toContain("old_line");
    expect(frame).toContain("new_line");
    expect(frame).toContain("│");
    setup.renderer.destroy();
  });

  test("add/del rows carry distinct row tints (bg is the add/del signal)", async () => {
    const setup = await testRender(
      () => <ModifiedFilesCard msgId="m2" files={[EDIT_FILE]} width={80} />,
      { width: 80, height: 30 },
    );
    await setup.renderOnce();
    type Span = { text: string; bg?: unknown };
    const frame = setup.captureSpans() as unknown as { lines: { spans: Span[] }[] };
    const bgOf = (needle: string) =>
      frame.lines
        .flatMap((l) => l.spans)
        .find((s) => s.text.includes(needle))
        ?.bg?.toString();
    // opencode grammar: add/del rows tint their BACKGROUND; fg stays
    // syntax-colored, so the add/del signal must never be fg-compared.
    expect(bgOf("old_line")).toBeTruthy();
    expect(bgOf("new_line")).toBeTruthy();
    expect(bgOf("old_line")).not.toBe(bgOf("new_line"));
    setup.renderer.destroy();
  });

  test("write without diff shows styled full code content", async () => {
    const setup = await testRender(
      () => <ModifiedFilesCard msgId="m3" files={[WRITE_FILE]} width={80} />,
      { width: 80, height: 30 },
    );
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("✎~ src/new.py");
    expect(frame).toContain("▍py");
    expect(frame).toContain("def hello():");
    expect(frame).toContain("return 1");
    setup.renderer.destroy();
  });

  test("long diffs cap at the preview budget with a show-more verb", async () => {
    const longDiff = [
      "--- a/big.py",
      "+++ b/big.py",
      "@@ -1,40 +1,40 @@",
      ...Array.from({ length: 40 }, (_, i) => `+added_${i}`),
    ].join("\n");
    const setup = await testRender(
      () => (
        <ModifiedFilesCard
          msgId="m4"
          files={[{ path: "big.py", tool: "file_edit", diffText: longDiff, summary: "" }]}
          width={80}
        />
      ),
      { width: 80, height: 60 },
    );
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("more diff lines");
    setup.renderer.destroy();
  });
});
