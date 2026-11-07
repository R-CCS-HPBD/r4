/**
 * B8 — Code-fence token highlighting + diff header.
 */

import { describe, expect, test } from "bun:test";
import { fenceCodeLangs, tokenizeLine } from "../src/lib/code-highlight";
import { diffHeader } from "../src/lib/diff-highlight";

describe("fenceCodeLangs", () => {
  test("tracks the fence language over content lines", () => {
    const langs = fenceCodeLangs([
      "text before",
      "```python",
      "def f():",
      "    return 1",
      "```",
      "text after",
    ]);
    expect(langs).toEqual([null, null, "python", "python", null, null]);
  });

  test("unclosed fence leaves remaining lines as code", () => {
    const langs = fenceCodeLangs(["```ts", "const x = 1;", "let y = 2;"]);
    expect(langs[0]).toBe(null);
    expect(langs[1]).toBe("ts");
    expect(langs[2]).toBe("ts");
  });
});

describe("tokenizeLine", () => {
  test("js: keywords, strings, comments, numbers", () => {
    const tokens = tokenizeLine(`const x = "hi"; // 42 done`, "ts");
    const kinds = tokens.filter((t) => t.kind !== "default").map((t) => [t.text, t.kind]);
    expect(kinds).toContainEqual(["const", "keyword"]);
    expect(kinds).toContainEqual([`"hi"`, "string"]);
    expect(tokens.some((t) => t.kind === "comment" && t.text.includes("42 done"))).toBe(true);
  });

  test("py: comment starts at #, strings retained as strings", () => {
    const tokens = tokenizeLine(`def f(s="a"): # init`, "python");
    expect(tokens.some((t) => t.kind === "keyword" && t.text === "def")).toBe(true);
    expect(tokens.some((t) => t.kind === "string" && t.text === `"a"`)).toBe(true);
    expect(tokens.some((t) => t.kind === "comment" && t.text === "# init")).toBe(true);
  });

  test("sh: hash NOT a comment when embedded in a word (echo ab#c)", () => {
    const tokens = tokenizeLine("echo ab#c", "bash");
    expect(tokens.every((t) => t.kind !== "comment")).toBe(true);
  });

  test("unknown language returns the whole line as default", () => {
    const tokens = tokenizeLine("whatever lang", "cobol");
    expect(tokens).toEqual([{ text: "whatever lang", kind: "default" }]);
  });
});

describe("diffHeader", () => {
  test("counts add/del across hunks and dedupes files", () => {
    const diff = [
      "diff --git a/src/a.ts b/src/a.ts",
      "+++ b/src/a.ts",
      "@@ -1,2 +1,3 @@",
      "-old",
      "+new",
      "+also",
      " context",
    ].join("\n");
    const h = diffHeader(diff)!;
    expect(h.added).toBe(2);
    expect(h.removed).toBe(1);
    expect(h.files).toEqual(["src/a.ts"]);
  });

  test("returns null for non-diff content", () => {
    expect(diffHeader("just some text")).toBeNull();
  });

  test("skips +++ /dev/null (deletions)", () => {
    const diff = "+++ /dev/null\n+x\n-y";
    const h = diffHeader(diff)!;
    expect(h.files).toEqual([]);
    expect(h.added).toBe(1);
    expect(h.removed).toBe(1);
  });
});
