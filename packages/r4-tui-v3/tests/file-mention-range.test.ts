import { describe, expect, it } from "bun:test";
import { extractMentionQuery } from "../src/lib/file-mentions";

describe("A1 @file#L10-L20 range syntax", () => {
  it("parses a plain mention with no range", () => {
    const found = extractMentionQuery("open @src/app.ts");
    expect(found?.query).toBe("src/app.ts");
    expect(found?.range).toBe("");
  });

  it("captures #L10-L20 and strips it from the ranking query", () => {
    const found = extractMentionQuery("check @src/app.ts#L10-L20");
    expect(found?.query).toBe("src/app.ts");
    expect(found?.range).toBe("#L10-L20");
  });

  it("accepts single-line range variants", () => {
    expect(extractMentionQuery("@a.py#L7")?.range).toBe("#L7");
    expect(extractMentionQuery("@a.py#L7-9")?.range).toBe("#L7-9");
  });

  it("keeps the popup closed for incomplete range fragments", () => {
    expect(extractMentionQuery("@src/app.ts#L")).toBeNull();
    expect(extractMentionQuery("@src/app.ts#L10-")).toBeNull();
    expect(extractMentionQuery("@src/app.ts#Lx")).toBeNull();
  });

  it("still rejects email-like tokens", () => {
    expect(extractMentionQuery("mail @@x")).toBeNull();
  });
});
