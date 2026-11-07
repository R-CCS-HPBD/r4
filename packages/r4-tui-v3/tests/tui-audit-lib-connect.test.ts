/**
 * TUI audit 2026-10-17 (ts-lib lane, #61) — the /connect identity line runs
 * `r4 auth status` with flags the CLI accepts. `--json` does not exist there
 * (r4/_cli/auth.py), so argparse exited 2 and every user read "not signed in".
 * A fake interpreter stands in for r4 (tests/helpers/fake-r4.ts).
 */
import { afterEach, describe, expect, test } from "bun:test";

import { authStatus } from "../src/lib/connect";
import { makeFakeR4, restoreR4, useFakeR4 } from "./helpers/fake-r4";

afterEach(restoreR4);

describe("authStatus", () => {
  test("runs `r4 auth status` without the unsupported --json flag", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const status = await authStatus();
    expect(fake.argv()).toEqual(["auth", "status"]);
    expect(status.ok).toBe(true);
    expect(status.raw).toContain("Added source");
  });

  test("JWT-identity text names the user and expiry (receipt stops saying bare 'signed in')", async () => {
    // Real `r4 auth status` text (no JSON anywhere in the CLI — 2026-10-21
    // receipt follow-up): parse the Username/Expires lines.
    const fake = makeFakeR4("authstatus");
    useFakeR4(fake.python);
    const status = await authStatus();
    expect(status.ok).toBe(true);
    expect(status.identity).toBe("mnagaso");
    expect(status.expires).toBe("719h 12m");
  });

  test("text without an identity block keeps the receipt honest (no invented name)", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const status = await authStatus();
    expect(status.ok).toBe(true);
    expect(status.identity).toBeUndefined();
    expect(status.expires).toBeUndefined();
  });
});
