import { describe, expect, it } from "bun:test";
import { welcomeChatContent } from "../src/lib/chat-welcome";

describe("welcomeChatContent", () => {
  it("live welcome is short and not plumbing-heavy", () => {
    const text = welcomeChatContent({ demoMode: false });
    expect(text).toContain("Hi");
    expect(text.toLowerCase()).toContain("/help");
    // Function keys are not advertised (many terminals cannot deliver them).
    expect(text).not.toMatch(/\bF\d+\b/i);
    expect(text.toLowerCase()).toContain("ctrl+p");
    expect(text.toLowerCase()).not.toContain("r4-cli");
    expect(text.toLowerCase()).not.toContain("companion");
    expect(text.toLowerCase()).not.toContain("needs a live backend");
  });

  it("demo welcome is explicit about fixtures", () => {
    const text = welcomeChatContent({ demoMode: true });
    expect(text).toMatch(/demo/i);
    expect(text.toLowerCase()).toContain("won't create plans");
  });
});
