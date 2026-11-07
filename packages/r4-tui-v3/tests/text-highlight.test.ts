/**
 * Semantic line colorizer tests — opencode-parity spans for paths, flags,
 * statuses, durations, URLs, $VARS, and backticks.
 */

import { describe, expect, it } from "bun:test";
import { highlightLine } from "../src/lib/text-highlight";
import { palette } from "../src/themes";

function fgOf(spans: { text: string; fg?: string }[], needle: string): string | undefined {
  return spans.find((s) => s.text.includes(needle))?.fg;
}

describe("highlightLine", () => {
  it("colors absolute and relative paths", () => {
    const spans = highlightLine("reading /tmp/proj/src/app.py then ./tests/x");
    expect(fgOf(spans, "/tmp/proj/src/app.py")).toBe(palette.knowledge);
    expect(fgOf(spans, "./tests/x")).toBe(palette.knowledge);
  });

  it("colors flags but never words starting with dashes mid-sentence weirdly", () => {
    const spans = highlightLine("run git log --oneline -n 5");
    expect(fgOf(spans, "--oneline")).toBe(palette.statusWarn);
    expect(fgOf(spans, "-n")).toBe(palette.statusWarn);
  });

  it("colors status words by severity", () => {
    const err = highlightLine("build failed with error: permission denied");
    expect(fgOf(err, "failed")).toBe(palette.statusErr);
    expect(fgOf(err, "error")).toBe(palette.statusErr);
    expect(fgOf(err, "denied")).toBe(palette.statusErr);
    const ok = highlightLine("tests passed — ready");
    expect(fgOf(ok, "passed")).toBe(palette.statusOk);
    expect(fgOf(ok, "ready")).toBe(palette.statusOk);
  });

  it("colors durations and sizes", () => {
    const spans = highlightLine("took 3.2s, read 12MB, 450ms latency");
    expect(fgOf(spans, "3.2s")).toBe(palette.accent);
    expect(fgOf(spans, "12MB")).toBe(palette.accent);
    expect(fgOf(spans, "450ms")).toBe(palette.accent);
  });

  it("colors URLs without splitting them into path chunks", () => {
    const spans = highlightLine("see https://data0.ai.r-ccs.riken.jp/bdrt/status now");
    expect(fgOf(spans, "https://data0.ai.r-ccs.riken.jp/bdrt/status")).toBe(palette.statusInfo);
  });

  it("colors $VARS and backtick code", () => {
    const spans = highlightLine("set $HOME, then run `r4 status`");
    expect(fgOf(spans, "$HOME")).toBe(palette.accent2);
    expect(fgOf(spans, "`r4 status`")).toBe(palette.mdCode);
  });

  it("keeps plain text as base-colored spans and never drops content", () => {
    const line = "quick brown fox jumps over some lazy dogs";
    const spans = highlightLine(line);
    expect(spans.map((s) => s.text).join("")).toBe(line);
    expect(spans.some((s) => s.fg === undefined)).toBe(true); // plain runs stay uncolored
  });

  it("round-trips arbitrary input (no content loss)", () => {
    for (const line of ["", " ", "a-b_c pair", "100% complete", "//nested//slashes//"]) {
      expect(highlightLine(line).map((s) => s.text).join("")).toBe(line);
    }
  });
});
