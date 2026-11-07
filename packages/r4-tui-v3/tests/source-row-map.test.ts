/**
 * R2-6: TUI source row mapping (remote badges flow to the /source overlay).
 */

import { describe, expect, test } from "bun:test";
import { mapSourceRow } from "../src/lib/sources";

describe("mapSourceRow", () => {
  test("maps base fields + remote badge from r4 source list --remote", () => {
    const row = mapSourceRow({
      name: "openai-main",
      kind: "public",
      url: "https://api.openai.com",
      active: true,
      provider: null,
      description: "OpenAI main",
      has_api_key: true,
      remote: "pushed (47m)",
    });
    expect(row.name).toBe("openai-main");
    expect(row.active).toBe(true);
    expect(row.remote).toBe("pushed (47m)");
  });

  test("remote absent when the plain list shape is given", () => {
    const row = mapSourceRow({ name: "local-one", kind: "platform", url: "https://x", active: false });
    expect(row.remote).toBeUndefined();
  });

  test("stale/tombstoned/dash/question badges pass through unmodified", () => {
    for (const badge of ["stale", "tombstoned", "-", "?", "pushed (2h)"]) {
      expect(mapSourceRow({ name: "x", remote: badge }).remote).toBe(badge);
    }
  });

  test("api_key boolean variant of has_api_key still parses", () => {
    const row = mapSourceRow({ name: "x", has_api_key: false });
    expect(row.has_api_key).toBe(false);
    const legacy = mapSourceRow({ name: "x", api_key: true });
    expect(legacy.has_api_key).toBe(true);
  });
});
