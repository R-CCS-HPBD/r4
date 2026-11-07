/**
 * Offline fixture mode for the pty-live driver (plan H-9): a scratch home
 * plus `python` / `r4` shims, so the dev TUI boots in its normal CLI mode
 * with every subprocess answered locally.
 *
 *   python -m r4.companion.ui.bridge_server …  → bun fixture-bridge/server.ts
 *   python -c <code> [r4 args…], r4 [args…]    → canned CLI answers below
 *
 * The TUI pins its interpreter through R4_TUI_PYTHON (as `r4 console` does)
 * and probes a PATH `r4`; both point here. Nothing reads the user's config:
 * HOME, R4_CONFIG_HOME and the XDG roots all live in the scratch directory,
 * removed again by cleanup().
 */

import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export interface FixtureHome {
  env: Record<string, string>;
  cleanup(): void;
}

/** One endpoint and one job for the Infrastructure pane journey. */
const ENDPOINTS = [{ instance_id: "fx-ep-1", model: "fx-endpoint-model", status: "ready", tier: "project" }];
const JOBS = [{ job_id: "fx-job-1", job_type: "fx-train-job", status: "running" }];

/** r4 CLI answers: `--version`, the infra listings, other JSON listings empty; the rest fail honestly. */
const CLI_CASES = [
  `case "$*" in`,
  `  "") exit 0 ;;`,
  `  --version) echo "r4 0.0.0+pty-fixture"; exit 0 ;;`,
  `  "endpoint list --json --all") echo '${JSON.stringify(ENDPOINTS)}'; exit 0 ;;`,
  `  "job list --json --limit 20") echo '${JSON.stringify(JOBS)}'; exit 0 ;;`,
  `  *--json*) echo "[]"; exit 0 ;;`,
  `  *) echo "pty fixture: 'r4 $*' is not scripted" >&2; exit 1 ;;`,
  `esac`,
];

function writeShim(path: string, lines: string[]): void {
  writeFileSync(path, ["#!/usr/bin/env bash", ...lines, ""].join("\n"));
  chmodSync(path, 0o755);
}

export function createFixtureHome(bunBin: string): FixtureHome {
  const root = mkdtempSync(join(tmpdir(), "r4-pty-fixture-"));
  const bin = join(root, "bin");
  const home = join(root, "home");
  for (const dir of [bin, home, join(home, ".config", "r4")]) mkdirSync(dir, { recursive: true });
  const server = new URL("./server.ts", import.meta.url).pathname;
  writeShim(join(bin, "python"), [
    `if [ "$1" = "-m" ]; then exec "${bunBin}" "${server}"; fi`,
    `if [ "$1" = "-c" ]; then shift 2; fi`,
    ...CLI_CASES,
  ]);
  writeShim(join(bin, "r4"), CLI_CASES);
  const env: Record<string, string> = {
    R4_TUI_BACKEND: "cli",
    R4_TUI_PYTHON: join(bin, "python"),
    PATH: `${bin}:${process.env.PATH ?? "/usr/bin:/bin"}`,
    HOME: home,
    R4_CONFIG_HOME: join(home, ".config", "r4"),
    XDG_CONFIG_HOME: join(home, ".config"),
    XDG_DATA_HOME: join(home, ".local", "share"),
    XDG_STATE_HOME: join(home, ".local", "state"),
    R4_TUI_WORKSPACE: home,
  };
  return {
    env,
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  };
}
