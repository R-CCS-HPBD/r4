/**
 * D-3 (c3-tui 2026-10-21): every string the `r4 … --json` CLI adapter hands
 * the screen is terminal-safe (lib/terminal-safe).
 *
 * JSON escapes control characters (`\u001b`), so the raw stdout looked clean
 * and JSON.parse decoded them into live escapes: a job, endpoint or source
 * name carrying OSC 52 wrote the user's clipboard when the Infrastructure
 * pane or the /source overlay painted it, OSC 0 retitled the window.
 * The `r4` CLI runs against a fake interpreter (R4_TUI_PYTHON).
 */
import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { R4CliDataSource } from "../src/adapters/r4-cli";
import { fetchSources } from "../src/lib/sources";
import { resetResolvedPythonCache } from "../src/lib/resolve-python";

const OSC52 = "\u001b]52;c;UFdOQ0xJUA==\u0007";
const TITLE = "\u001b]0;PWNTITLE\u0007";
const hostile = (s: string) => `${s}${OSC52}${TITLE}\u001b[2J`;

const ENDPOINTS = {
  endpoints: [{ instance_id: hostile("ep1"), model: hostile("model"), status: "ready", _source: hostile("src") }],
  errors: [{ source: hostile("plat"), error: hostile("down"), hint: hostile("retry") }],
};
const JOBS = { jobs: [{ job_id: hostile("job1"), job_type: hostile("train"), status: "running", name: hostile("name") }] };
const MODELS = { models: [{ id: hostile("m1"), name: hostile("m1") }] };
const SOURCES = [{ name: hostile("evil-source"), kind: "public", url: hostile("http://x"), active: true, description: hostile("d") }];

const script = (fakeDir: string) => `#!/bin/sh
FAKE_DIR="${fakeDir}"
shift 2
case "$1 $2" in
  "endpoint list") cat "$FAKE_DIR/endpoints.json" ;;
  "model list") cat "$FAKE_DIR/models.json" ;;
  "job list") cat "$FAKE_DIR/jobs.json" ;;
  "skill list") echo '[]' ;;
  "source list") cat "$FAKE_DIR/sources.json" ;;
  "job cancel") printf 'cancelled \\033]52;c;QQ==\\007 ok\\n' ;;
  *) exit 1 ;;
esac
`;

const saved: Record<string, string | undefined> = {};
let dir = "";

beforeAll(() => {
  for (const key of ["R4_TUI_PYTHON", "PATH"]) saved[key] = process.env[key];
  dir = mkdtempSync(join(tmpdir(), "tui-c3-cli-safe-"));
  writeFileSync(join(dir, "endpoints.json"), JSON.stringify(ENDPOINTS));
  writeFileSync(join(dir, "models.json"), JSON.stringify(MODELS));
  writeFileSync(join(dir, "jobs.json"), JSON.stringify(JOBS));
  writeFileSync(join(dir, "sources.json"), JSON.stringify(SOURCES));
  const fake = join(dir, "fake-python");
  writeFileSync(fake, script(dir));
  chmodSync(fake, 0o755);
  process.env["R4_TUI_PYTHON"] = fake;
  // No PATH `r4` may win over the pinned interpreter.
  process.env["PATH"] = "/usr/bin:/bin";
  resetResolvedPythonCache();
});

afterAll(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  resetResolvedPythonCache();
  rmSync(dir, { recursive: true, force: true });
});

/** Every string inside a value, however deep. */
function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (value && typeof value === "object") return Object.values(value).flatMap(strings);
  return [];
}

const CONTROL = /[\x00-\x08\x0b-\x1f\x7f-\x9f]/;

describe("r4 CLI adapter output is terminal-safe", () => {
  it("endpoint, model and job names and source errors carry no live control character", async () => {
    const snap = await new R4CliDataSource().pollBackend();
    const all = strings(snap);
    expect(all.some((s) => s.includes("ep1"))).toBe(true);
    expect(all.some((s) => s.includes("train"))).toBe(true);
    expect(all.filter((s) => CONTROL.test(s))).toEqual([]);
    // Shown as visible pictures, never dropped silently.
    expect(all.some((s) => s.includes("␛]52;c;UFdOQ0xJUA==␇"))).toBe(true);
    expect((snap.problems ?? []).join(" ")).toContain("␛]0;PWNTITLE␇");
  });

  it("source names from `r4 source list --json` are terminal-safe", async () => {
    const rows = await fetchSources();
    expect(rows[0]!.name).toContain("evil-source");
    expect(strings(rows).filter((s) => CONTROL.test(s))).toEqual([]);
  });

  it("an action's plain-text output is terminal-safe", async () => {
    const result = await new R4CliDataSource().cancelJob("job1");
    expect(result.ok).toBe(true);
    expect(CONTROL.test(result.message)).toBe(false);
    expect(result.message).toContain("␛]52");
  });
});
