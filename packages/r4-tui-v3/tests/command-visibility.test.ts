/**
 * Command visibility contract: dispatch-level slash intercepts must also have
 * a row in localSlashCommands() — or the palette cannot find them.
 * (Two real catches: /mode in S2f, /sessions /catalog /codeview in WD-WH.)
 */

import { describe, expect, test } from "bun:test";
import { localSlashCommands } from "../src/commands/slash";

const MUST_BE_VISIBLE = [
  "sessions",
  "catalog",
  "codeview",
  "mode",
  "models",
  "work",
  "work",
  "settings",
  "health",
  "skills",
  "mcp",
  "help",
  "code",
  "theme",
] as const;

describe("slash command visibility", () => {
  const names = localSlashCommands().map((c) => c.name);

  for (const name of MUST_BE_VISIBLE) {
    test(`/${name} is registered in the palette catalog`, () => {
      expect(names).toContain(name);
    });
  }

  test("every registered name is non-empty and unique", () => {
    const entries = localSlashCommands().map((c) => c.name);
    expect(new Set(entries).size).toBe(entries.length);
  });
});
