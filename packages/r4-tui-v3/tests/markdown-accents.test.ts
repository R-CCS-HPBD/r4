import { describe, expect, it } from "bun:test";
import { accentLineKinds } from "../src/lib/markdown-accents";
import { aurora, tokyoNight } from "../src/themes";

describe("D4 markdown accents", () => {
  it("classifies headings, fences, quotes and plain lines", () => {
    const kinds = accentLineKinds(["# Title", "text", "```js", "let x = 1;", "```", "> note"]);
    expect(kinds).toEqual(["heading", "plain", "code", "code", "code", "quote"]);
  });

  it("keeps code classification until the closing fence", () => {
    const kinds = accentLineKinds(["```", "### not-a-heading", "```", "## heading"]);
    expect(kinds).toEqual(["code", "code", "code", "heading"]);
  });

  it("unterminated fence leaves the tail as code", () => {
    expect(accentLineKinds(["```py", "x", "y"])).toEqual(["code", "code", "code"]);
  });

  it("does not classify heading without a space after #", () => {
    expect(accentLineKinds(["#nope"])).toEqual(["plain"]);
  });
});

describe("D4 semantic theme keys", () => {
  it("both palettes carry diff + markdown accent keys", () => {
    for (const theme of [aurora, tokyoNight]) {
      for (const key of ["diffAdd", "diffDel", "diffHunk", "diffMeta", "mdHeading", "mdCode", "mdQuote"] as const) {
        expect(typeof theme[key]).toBe("string");
        expect(theme[key]).toMatch(/^#/);
      }
    }
  });
});
