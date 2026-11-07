/**
 * paletteDirectLine — a query that already IS a full slash line with args
 * dispatches verbatim, resolved against the WHOLE catalog (not the selected
 * row). Regression: a pasted payload (JWT, URL) kills fuzzy matching → zero
 * rows → Enter used to dead-end and the token was never recognised.
 */

import { describe, expect, test } from "bun:test";
import { paletteDirectLine } from "../src/lib/slash-palette";
import type { SlashCommandEntry } from "../src/commands/slash";

const CATALOG: SlashCommandEntry[] = [
  {
    name: "login",
    help: "Authenticate with JWT / onboarding token: /login <token>",
    category: "endpoint",
    takesArgs: true,
  },
  {
    name: "source",
    help: "Manage inference sources",
    category: "endpoint",
    takesArgs: true,
  },
  {
    name: "code",
    help: "Toggle the builtin code skill",
    category: "session",
    takesArgs: true,
    aliases: ["coding"],
  },
  {
    name: "health",
    help: "Platform health",
    category: "status",
    takesArgs: false,
  },
];

describe("paletteDirectLine", () => {
  test("JWT payload passes through verbatim", () => {
    const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sigmoid";
    expect(paletteDirectLine(`login ${jwt}`, CATALOG)).toBe(`/login ${jwt}`);
    expect(paletteDirectLine(`/login ${jwt}`, CATALOG)).toBe(`/login ${jwt}`);
  });

  test("extra whitespace between name and args is normalised", () => {
    expect(paletteDirectLine("  source   add http://ola:11434 ", CATALOG)).toBe(
      "/source add http://ola:11434",
    );
  });

  test("aliases resolve to the canonical name", () => {
    expect(paletteDirectLine("coding on", CATALOG)).toBe("/code on");
  });

  test("bare name without args returns null (row seeding path handles it)", () => {
    expect(paletteDirectLine("login", CATALOG)).toBeNull();
    expect(paletteDirectLine("", CATALOG)).toBeNull();
    expect(paletteDirectLine("/", CATALOG)).toBeNull();
  });

  test("unknown first token returns null — never hijacks arbitrary text", () => {
    expect(paletteDirectLine("notacommand withargs", CATALOG)).toBeNull();
  });
});
