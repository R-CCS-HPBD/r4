/**
 * paletteTypedLine — full typed lines ("/mode chat") dispatch verbatim;
 * bare names fall through to the row's run() (takesArgs seeding path).
 */

import { describe, expect, test } from "bun:test";
import { paletteTypedLine } from "../src/lib/slash-palette";

describe("paletteTypedLine", () => {
  test("name + args returns the full slash line", () => {
    expect(paletteTypedLine("mode chat", "mode")).toBe("/mode chat");
    expect(paletteTypedLine("/mode plan", "mode")).toBe("/mode plan");
    expect(paletteTypedLine("  mode   autopilot  ", "mode")).toBe("/mode autopilot");
  });

  test("bare name returns null (run path seeds/just runs)", () => {
    expect(paletteTypedLine("mode", "mode")).toBeNull();
    expect(paletteTypedLine("", "mode")).toBeNull();
  });

  test("different command in the query returns null", () => {
    expect(paletteTypedLine("sessions resume abc123", "mode")).toBeNull();
  });

  test("name inside the args isn't re-parsed", () => {
    expect(paletteTypedLine("mode", "sessions")).toBeNull();
  });
});
