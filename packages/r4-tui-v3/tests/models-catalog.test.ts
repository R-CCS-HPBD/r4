/**
 * P10-C3 visibility chips (WP-06 r2) under G8 (onboarding plan 2026-10-15
 * §5 B): the catalog is an authenticated fact and the console holds no key,
 * so opening /models sends no request and spawns no r4 — the rows will come
 * from the r4 CLI later. The row → chip mapping is kept for that wave.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import { resolveSlashPicker } from "../src/commands/slash-pickers";
import { catalogRows, visibilityByName, type ModelCatalogRow } from "../src/lib/models-catalog";
import { makeFakeR4, restoreR4, useFakeR4 } from "./helpers/fake-r4";

describe("models-catalog rows (P10-C3)", () => {
  test("rows arrive verbatim; visibility map extracts chips", () => {
    const rows: ModelCatalogRow[] = [
      { name: "qwen3-8b", scope: "system", visibility_source: "project" },
      { name: "mdl-shared", scope: "user", visibility_source: "grant" },
      { name: "no-chip-here", visibility_source: null },
    ];
    const out = catalogRows({ models: rows });
    expect(out).toEqual(rows);
    expect(catalogRows({ data: rows })).toEqual(rows);
    const chips = visibilityByName(out);
    expect(chips.get("mdl-shared")).toBe("grant");
    expect(chips.get("qwen3-8b")).toBe("project");
    expect(chips.has("no-chip-here")).toBe(false);
  });

  test("a body without rows stays empty (never fabricate chips)", () => {
    expect(catalogRows(null)).toEqual([]);
    expect(catalogRows({ models: "down" })).toEqual([]);
    expect(catalogRows("<html>401</html>")).toEqual([]);
  });

  test("grant chip never decorative: null visibility stays out of the map", () => {
    const out = visibilityByName([
      { name: "m1", visibility_source: null },
      { name: "m2", visibility_source: "" },
      { name: "m3", visibility_source: "grant" },
    ]);
    expect(out.size).toBe(1);
    expect(out.get("m3")).toBe("grant");
  });
});

describe("/models picker — no keyless catalog read (G8)", () => {
  const originalFetch = globalThis.fetch;
  const savedBackend = process.env.R4_TUI_BACKEND;
  const fetched: string[] = [];

  beforeEach(() => {
    process.env.R4_TUI_BACKEND = "offline";
    fetched.length = 0;
    globalThis.fetch = (async (input: string | URL | Request) => {
      fetched.push(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      return new Response("unauthorized", { status: 401 });
    }) as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = savedBackend;
    restoreR4();
  });

  test("opening /models with a keyed platform source sends nothing and spawns no r4", async () => {
    const fake = makeFakeR4("sources"); // `r4 source list` would name a platform gateway
    useFakeR4(fake.python);
    await resolveSlashPicker("/models");
    expect(fetched).toEqual([]);
    expect(fake.spawned()).toBe(false);
  });
});
