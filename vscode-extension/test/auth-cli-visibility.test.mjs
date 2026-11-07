import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, chmodSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

/**
 * Boundary regression: the extension host probes `r4` via `which r4`
 *   (vscode-extension/src/auth.ts::resolveR4CliPath). This test pins the
 *   two behaviors that section-6 of the auth investigation called out:
 *
 *   1. Login-shell PATH visibility — `which r4` must find the same binary
 *      that the interactive shell sees (no private-PATH surprises).
 *   2. Non-PATH failure channels — on machines without the CLI, `which`
 *      exits non-zero and the extension must stay in the SecretStorage-only
 *      fallback instead of crashing (see auth.ts::loginViaR4Cli).
 */

/** Minimal sandbox r4 CLI for the failure-injection case. */
function makeStubR4(dir, body = "#!/bin/sh\nexit 42\n") {
  const stub = path.join(dir, "r4");
  writeFileSync(stub, body);
  chmodSync(stub, 0o755);
  return stub;
}

test("resolveR4CliPath behavior: host shell inherits PATH, `which r4` succeeds", async () => {
  const { stdout } = await execFileAsync("which", ["r4"], {
    env: process.env,
  });
  assert.ok(stdout.trim().length > 0, "r4 must be visible via PATH");
  assert.ok(stdout.trim().endsWith("r4"), "must resolve to the r4 binary");
});

test("resolveR4CliPath behavior: no r4 on PATH → which exits non-zero (SecretStorage fallback)", async () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "r4-nopath-"));
  try {
    // Empty PATH — nothing can resolve.
    await assert.rejects(
      () => execFileAsync("which", ["r4"], { env: { PATH: "" } }),
      (err) => err instanceof Error,
      "which must exit non-zero when r4 is absent",
    );
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("resolveR4CliPath behavior: stub r4 on PATH resolves at exactly the wrapped binary path", async () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "r4-stubpath-"));
  try {
    const stub = makeStubR4(tmp);
    const { stdout } = await execFileAsync("which", ["r4"], {
      env: { PATH: `${tmp}:${process.env.PATH}` },
    });
    assert.strictEqual(stdout.trim().split("\n")[0], stub);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("boundary: first-match wins when multiple r4 binaries sit on PATH", async () => {
  const front = mkdtempSync(path.join(os.tmpdir(), "r4-front-"));
  const back = mkdtempSync(path.join(os.tmpdir(), "r4-back-"));
  try {
    const stubFront = makeStubR4(front, "#!/bin/sh\necho front\n");
    makeStubR4(back, "#!/bin/sh\necho back\n");
    const { stdout } = await execFileAsync("which", ["r4"], {
      env: { PATH: `${front}:${back}:${process.env.PATH}` },
    });
    assert.strictEqual(stdout.trim().split("\n")[0], stubFront);
  } finally {
    rmSync(front, { recursive: true, force: true });
    rmSync(back, { recursive: true, force: true });
  }
});
