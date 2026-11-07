import { describe, expect, it } from "bun:test";
import { classifyDiffLines, isDiffShaped } from "../src/lib/diff-highlight";

const SAMPLE = `diff --git a/src/a.ts b/src/a.ts
index 111..222 100644
--- a/src/a.ts
+++ b/src/a.ts
@@ -1,3 +1,4 @@
 const a = 1;
-const b = 2;
+const b = 3;
+const c = 4;
`;

describe("B3 diff highlight", () => {
  it("flags a unified diff as diff-shaped", () => {
    expect(isDiffShaped(SAMPLE)).toBe(true);
  });

  it("classifies add/del/hunk/meta/ctx lines", () => {
    const kinds = classifyDiffLines(SAMPLE).map((l) => l.kind);
    expect(kinds.slice(0, 4)).toEqual(["meta", "meta", "meta", "meta"]);
    expect(kinds[4]).toBe("hunk");
    expect(kinds[5]).toBe("ctx");
    expect(kinds[6]).toBe("del");
    expect(kinds[7]).toBe("add");
    expect(kinds[8]).toBe("add");
  });

  it("does not flag plain prose with +/- bullets", () => {
    expect(isDiffShaped("Pros:\n+ fast\n+ small\nCons:\n- slow\n- big")).toBe(false);
  });

  it("does not flag single-sided +/- blocks", () => {
    expect(isDiffShaped("@@ -1 +1 @@\n+ only additions here")).toBe(false);
  });

  it("hunk header alone is not enough without both sides", () => {
    expect(isDiffShaped("@@\n- gone\n+ there")).toBe(true); // has hunk + both sides
    expect(isDiffShaped("- gone\n+ there")).toBe(false); // no structure
  });
});
