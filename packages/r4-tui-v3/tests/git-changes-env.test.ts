/**
 * Code view git helpers act on the WORKSPACE repository, never on one named
 * by an inherited GIT_DIR & co. (2026-10-21). Started from a git hook, the
 * TUI's code view listed — and its `r` revert would have checked out — files
 * of the hook's repository. The leak only shows in a child's real environ
 * (Bun children ignore process.env edits), so the helpers run in a
 * subprocess whose GIT_DIR / GIT_INDEX_FILE point at a decoy repository.
 */

import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const cleanEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")));
let root = "";
let workspace = "";
let decoy = "";

function git(cwd: string, ...args: string[]): void {
  const res = spawnSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd, env: cleanEnv });
  if (res.status !== 0) throw new Error(String(res.stderr));
}

function seedRepo(dir: string, file: string): void {
  git(dir, "init", "-q");
  writeFileSync(join(dir, file), "base\n");
  git(dir, "add", file);
  git(dir, "commit", "-q", "-m", "base");
  writeFileSync(join(dir, file), "edited\n");
}

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "r4-gitenv-"));
  workspace = join(root, "workspace");
  decoy = join(root, "decoy");
  for (const dir of [workspace, decoy]) spawnSync("mkdir", ["-p", dir]);
  seedRepo(workspace, "mine.txt");
  seedRepo(decoy, "theirs.txt");
});

afterAll(() => rmSync(root, { recursive: true, force: true }));

describe("git-changes under an inherited GIT_DIR", () => {
  it("lists and reverts the workspace repository, never the inherited one", () => {
    const helpers = new URL("../src/lib/git-changes.ts", import.meta.url).pathname;
    const script = [
      `const g = await import(${JSON.stringify(helpers)});`,
      `const ws = ${JSON.stringify(workspace)};`,
      "console.log(JSON.stringify({ changes: g.listChanges(ws), revert: g.revertPath('mine.txt', ws) }));",
    ].join("\n");
    const env = { ...cleanEnv, GIT_DIR: join(decoy, ".git"), GIT_INDEX_FILE: join(decoy, ".git", "index") };
    const res = spawnSync(process.execPath, ["-e", script], { env });
    const out = JSON.parse(String(res.stdout).trim().split("\n").pop() ?? "{}");

    expect(out.changes).toEqual([{ path: "mine.txt", status: "M" }]);
    expect(out.revert).toEqual({ ok: true });
    expect(readFileSync(join(workspace, "mine.txt"), "utf8")).toBe("base\n");
    // The decoy's uncommitted edit survives: nothing ran against it.
    expect(readFileSync(join(decoy, "theirs.txt"), "utf8")).toBe("edited\n");
  });
});
