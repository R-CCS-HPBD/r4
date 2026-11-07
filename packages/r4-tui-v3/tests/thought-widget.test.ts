import { describe, expect, it } from "bun:test";
import { thoughtBody, thoughtHeader, type ThoughtState } from "../src/lib/thought-widget";

const base: ThoughtState = {
  text: "Considering the evidence\nstep 2\nstep 3",
  open: false,
  expanded: false,
  startedAtMs: 1_000,
  elapsedMs: 2_345,
};

describe("B4 thought widget", () => {
  it("header shows first-line title and elapsed seconds", () => {
    expect(thoughtHeader(base)).toBe("Thought: Considering the evidence · 2.3s");
  });

  it("header carries the live marker while streaming", () => {
    const header = thoughtHeader({ ...base, open: true });
    expect(header.endsWith("⏳")).toBe(true);
  });

  it("empty thinking text renders an ellipsis title", () => {
    expect(thoughtHeader({ ...base, text: "", elapsedMs: 0 })).toBe("Thought: … · 0.0s");
  });

  it("long first lines are truncated", () => {
    const long = "x".repeat(80);
    const header = thoughtHeader({ ...base, text: long });
    expect(header.startsWith(`Thought: ${"x".repeat(41)}… · `)).toBe(true);
  });

  it("expanded body passes through under the cap", () => {
    expect(thoughtBody("a\nb\nc")).toEqual(["a", "b", "c"]);
  });

  it("expanded body is capped with an overflow marker", () => {
    const lines = Array.from({ length: 45 }, (_, i) => `line ${i}`);
    const body = thoughtBody(lines.join("\n"), 30);
    expect(body).toHaveLength(31);
    expect(body[30]).toBe("… (15 more lines)");
  });
});
