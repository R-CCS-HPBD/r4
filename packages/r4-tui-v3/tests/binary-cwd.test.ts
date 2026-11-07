/**
 * Cwd-robustness guard for the compiled binary.
 *
 * Historical trap: bun applies the CWD's bunfig.toml at boot. When a root
 * `preload = ["@opentui/solid/preload"]` line existed, running the compiled
 * binary with this package as cwd crashed with "preload not found". The root
 * preload is gone (dev passes --preload explicitly); this test asserts the
 * binary never regresses — `--version` must succeed from any cwd.
 *
 * Skipped (honest pass) when no dist-bin binary exists for the host target.
 */

import { describe, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { mkdtempSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const pkgDir = dirname(dirname(fileURLToPath(import.meta.url)));
// Same host mapping as scripts/build-bin.mjs (`${platform}-${arch}`): an Intel
// Mac builds darwin-x64 (2026-11-07; this used to assume darwin-arm64).
const HOST_TARGETS: Record<string, string> = {
  "darwin-arm64": "darwin-arm64",
  "darwin-x64": "darwin-x64",
  "linux-arm64": "linux-arm64",
  "linux-x64": "linux-x64",
};
const hostTarget = HOST_TARGETS[`${process.platform}-${process.arch}`] ?? null;
const binPath = hostTarget ? join(pkgDir, "dist-bin", `r4-tui-${hostTarget}`) : null;
const haveBinary = binPath !== null && existsSync(binPath);

describe("compiled binary — cwd robustness", () => {
  if (!haveBinary) {
    test("host binary not built yet (cwd guard inactive in source-only runs)", () => {
      expect(binPath ?? "unsupported-host").toBeTruthy();
    });
    return;
  }

  const cwds = [
    pkgDir, // the historical trap: package dir containing bunfig.toml
    tmpdir(),
    realpathSync(mkdtempSync(join(tmpdir(), "r4-tui-cwd-"))),
  ];
  for (const cwd of cwds) {
    test(`--version works from ${cwd}`, () => {
      const res = spawnSync(binPath!, ["--version"], { cwd, timeout: 15000 });
      expect(res.status).toBe(0);
      expect(res.error).toBeUndefined();
      expect(String(res.stdout)).toMatch(/^\d+\.\d+\.\d+/);
      expect(String(res.stderr ?? "")).not.toContain("preload not found");
    });
  }
});
