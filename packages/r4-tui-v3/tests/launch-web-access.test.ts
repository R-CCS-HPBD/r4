/**
 * RS-1 fix W5 (ADR-099 D-2): `r4 --web` reaches the console's bridge as a
 * launch-only value, not as an inherited `R4_WEB_ACCESS`.
 *
 * Before the fix `r4 --web` wrote `R4_WEB_ACCESS` into the environment the
 * console, its bridge and every shell they start inherit, and the bridge
 * applied it silently. Now the console takes `R4_LAUNCH_WEB_ACCESS` out of
 * its environment once and hands it to every bridge start as `--web-access`.
 */

import { describe, it, expect, afterEach } from "bun:test";
import {
  LAUNCH_WEB_ACCESS_ENV,
  bridgeArgv,
  resetLaunchWebAccessForTest,
  takeLaunchWebAccess,
} from "../src/bridge/jsonl-client";

afterEach(() => resetLaunchWebAccessForTest());

describe("r4 --web reaches the bridge as a launch argument", () => {
  it("takes the flag out of the environment and keeps it for restarts", () => {
    resetLaunchWebAccessForTest();
    const env: Record<string, string | undefined> = { [LAUNCH_WEB_ACCESS_ENV]: "allowlist:.arxiv.org,pypi.org" };
    expect(takeLaunchWebAccess(env)).toBe("allowlist:.arxiv.org,pypi.org");
    expect(env[LAUNCH_WEB_ACCESS_ENV]).toBeUndefined();
    // a bridge restart after a crash still gets the flag
    expect(takeLaunchWebAccess(env)).toBe("allowlist:.arxiv.org,pypi.org");
  });

  it("passes the flag as --web-access, and nothing without one", () => {
    expect(bridgeArgv("py", "/w", "public")).toEqual([
      "py",
      "-m",
      "r4.companion.ui.bridge_server",
      "--fs-root",
      "/w",
      "--web-access",
      "public",
    ]);
    expect(bridgeArgv("py", "/w", null)).not.toContain("--web-access");
  });

  it("an ambient R4_WEB_ACCESS is not a launch flag", () => {
    resetLaunchWebAccessForTest();
    const env: Record<string, string | undefined> = { R4_WEB_ACCESS: "public" };
    expect(takeLaunchWebAccess(env)).toBeNull();
    expect(env.R4_WEB_ACCESS).toBe("public");
  });
});
