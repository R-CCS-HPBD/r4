/**
 * /health public-source availability section.
 * Endpoints cover UMRS-managed inference only; the report must also say
 * whether each public (external) source answers — bounded 5s probes,
 * one line per source, active source marked.
 */
import { describe, test, expect } from "bun:test";
import {
  formatSourceHealthLines,
  sourceAvailabilityLines,
} from "../src/lib/health-report";
import type { SourceRow } from "../src/lib/sources";
import type { SourceHealth, SourceLike } from "../src/lib/source-health";

const ROWS: SourceRow[] = [
  {
    name: "rccs",
    kind: "platform",
    url: "https://gw.example/rccs",
    active: true,
    provider: null,
    has_api_key: false,
  },
  {
    name: "rikyu",
    kind: "public",
    url: "https://rikyu.example/v1",
    active: false,
    provider: "openai",
    has_api_key: true,
  },
  {
    name: "local-ollama",
    kind: "ollama",
    url: "http://127.0.0.1:11434",
    active: false,
    provider: "ollama",
    has_api_key: false,
  },
  {
    name: "canary-v1",
    kind: "public",
    url: "https://canary.example/v1",
    active: false,
    provider: "openai",
    has_api_key: false,
  },
];

const HEALTH: SourceHealth[] = [
  { source: "rikyu", ok: true, latencyMs: 142, detail: "ok · 142ms" },
  { source: "local-ollama", ok: true, latencyMs: 12, detail: "ok · 12ms" },
  { source: "canary-v1", ok: false, latencyMs: 88, detail: "http 503 · 88ms" },
];

describe("formatSourceHealthLines", () => {
  test("platform sources are excluded from the section", () => {
    const lines = formatSourceHealthLines(ROWS, HEALTH);
    expect(lines.join("\n")).not.toContain("rccs");
    expect(lines[0]).toBe("Sources (public): 2/3 available");
  });

  test("one line per public source with probe outcome and latency", () => {
    const lines = formatSourceHealthLines(ROWS, HEALTH);
    expect(lines).toContain("  ● rikyu — ok · 142ms");
    expect(lines).toContain("  ◐ canary-v1 — http 503 · 88ms");
    expect(lines).toContain("  ● local-ollama — ok · 12ms");
  });

  test("active source is marked", () => {
    const rows = ROWS.map((r) => (r.name === "rikyu" ? { ...r, active: true } : r));
    const lines = formatSourceHealthLines(rows, HEALTH);
    expect(lines).toContain("  ● rikyu (active) — ok · 142ms");
  });

  test("unprobed source says so honestly", () => {
    const lines = formatSourceHealthLines(ROWS, HEALTH.slice(0, 1));
    expect(lines).toContain("  · canary-v1 — not probed");
    expect(lines[0]).toBe("Sources (public): 1/3 available");
  });

  test("no public sources → no section at all", () => {
    expect(formatSourceHealthLines([ROWS[0]!], HEALTH)).toEqual([]);
  });
});

describe("sourceAvailabilityLines", () => {
  test("fetch failure returns no section (never throws)", async () => {
    const lines = await sourceAvailabilityLines(
      async () => {
        throw new Error("r4 source list down");
      },
      async () => [],
    );
    expect(lines).toEqual([]);
  });

  test("prober receives every public source with api_key mapped", async () => {
    let probed: SourceLike[] = [];
    const lines = await sourceAvailabilityLines(
      async () => ROWS,
      async (sources) => {
        probed = sources;
        return sources.map((s) => ({
          source: s.name,
          ok: true,
          latencyMs: 1,
          detail: "ok · 1ms",
        }));
      },
    );
    expect(probed.map((s) => s.name)).toEqual(["rikyu", "local-ollama", "canary-v1"]);
    expect(probed[0]!.api_key).toBe(true);
    expect(lines[0]).toBe("Sources (public): 3/3 available");
  });
});
