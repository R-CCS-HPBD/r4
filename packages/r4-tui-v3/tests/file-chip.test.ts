import { describe, expect, it } from "bun:test";
import { chipifyFileDisplayText, detectFileChips } from "../src/lib/file-chip";

describe("A5 file chips", () => {
  it("detects bare + @-mention tokens with kind glyphs", () => {
    const chips = detectFileChips("open @out/a.png and docs/b.pdf with notes");
    const paths = chips.map((c) => c.path).sort();
    expect(paths).toEqual(["docs/b.pdf", "out/a.png"]);
    expect(chips.find((c) => c.path.endsWith("png"))?.kind).toBe("Image");
    expect(chips.find((c) => c.path.endsWith("pdf"))?.kind).toBe("PDF");
  });

  it("numbers chips per kind in first-seen order for display", () => {
    expect(chipifyFileDisplayText("mix out/a.png, out/b.pdf, and out/c.svg?"))
      .toBe("mix [Image 1], [PDF 1], and [SVG 1]?");
  });

  it("passes prompts without file tokens through verbatim", () => {
    const prompt = "ls src/lib/*.ts then grep -n qwen";
    expect(chipifyFileDisplayText(prompt)).toBe(prompt);
  });
});
