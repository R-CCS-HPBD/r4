/** Unit pins for team-live pure helpers (2026-09-29 conclusion wave). */
import { describe, it, expect } from "bun:test";
import { finalMessageFor, wrapLines, type TeamEventLine } from "../src/lib/team-live";

function msg(role: string, preview: string): TeamEventLine {
  return { session_id: "s", kind: "agent_message", iteration: 1, role, preview };
}

describe("finalMessageFor", () => {
  it("returns the role's LATEST agent_message preview", () => {
    const ring = [msg("pm", "early plan"), msg("pg", "code done"), msg("pm", "revised plan")];
    expect(finalMessageFor(ring, "pm")).toBe("revised plan");
    expect(finalMessageFor(ring, "pg")).toBe("code done");
  });

  it("absent role → undefined (never invented)", () => {
    expect(finalMessageFor([msg("pm", "x")], "pg")).toBeUndefined();
    expect(finalMessageFor([], "pm")).toBeUndefined();
  });

  it("ignores non-message kinds for the same role", () => {
    const ring: TeamEventLine[] = [
      { session_id: "s", kind: "agent_status", iteration: 1, role: "pm", status: "error" },
      msg("pm", "the real answer"),
    ];
    expect(finalMessageFor(ring, "pm")).toBe("the real answer");
  });
});

describe("wrapLines", () => {
  it("wraps whole content with nothing cut", () => {
    const text = "abcdefghij";
    expect(wrapLines(text, 4)).toEqual(["abcd", "efgh", "ij"]);
  });

  it("keeps explicit newlines and never interpolates", () => {
    expect(wrapLines("ab\ncd", 10)).toEqual(["ab", "cd"]);
  });

  it("empty input yields no rows", () => {
    expect(wrapLines("", 8)).toEqual([]);
  });
});
