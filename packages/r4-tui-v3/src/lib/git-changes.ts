/**
 * Wave H — git change-set helpers for the code view.
 * Computed from the workspace root (R4_TUI_WORKSPACE || launch cwd);
 * nothing ever touches git from inside the bridge.
 */

import { spawnSync } from "node:child_process";
import { resolveWorkspace } from "./resolve-python";

export interface GitChange {
  path: string;
  /** A added · M modified · D deleted · R renamed · ? untracked. */
  status: "A" | "M" | "D" | "R" | "?";
}

/** Variables that point git at a repository other than `cwd`'s. */
const REPO_LOCATORS = [
  "GIT_DIR",
  "GIT_WORK_TREE",
  "GIT_INDEX_FILE",
  "GIT_COMMON_DIR",
  "GIT_OBJECT_DIRECTORY",
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_PREFIX",
  "GIT_NAMESPACE",
];

/** git in the workspace `cwd`, never in a repository named by an inherited
 *  GIT_DIR & co. (2026-10-21: a TUI started from a git hook showed, and its
 *  `r` revert would have checked out, files of ANOTHER repository). The env
 *  is explicit: Bun children otherwise inherit the original environ. */
function gitAt(cwd: string, args: string[]) {
  const env = { ...process.env };
  for (const name of REPO_LOCATORS) delete env[name];
  return spawnSync("git", args, { cwd, env });
}

export function inGitWorkTree(cwd = resolveWorkspace()): boolean {
  const res = gitAt(cwd, ["rev-parse", "--is-inside-work-tree"]);
  return res.status === 0 && String(res.stdout).trim() === "true";
}

/** Extract the display path (rename targets win, quotes stripped). */
function porcelainPath(line: string): string {
  const raw = line.slice(3).trim().replace(/^"|"$/g, "");
  const parts = raw.split(" -> ");
  return parts[parts.length - 1]!;
}

/** XY status codes → our compact kind (staged wins; untracked = A-ish). */
function porcelainStatus(x: string, y: string): GitChange["status"] {
  const code = [x, y].find((c) => c !== " " && c !== "?") ?? (x === "?" ? "?" : "M");
  if (code === "A" || code === "?") return "A";
  if (code === "D") return "D";
  if (code === "R") return "R";
  return "M";
}

/** porcelain XY → our compact kind (staged flag wins; untracked = A-ish). */
export function parsePorcelainLine(line: string): GitChange | null {
  if (line.length < 4) return null;
  return {
    path: porcelainPath(line),
    status: porcelainStatus(line[0] ?? " ", line[1] ?? " "),
  };
}

/** `git status --porcelain` rows → A/M/D list (failures → null = not a repo). */
export function listChanges(cwd = resolveWorkspace()): GitChange[] | null {
  if (!inGitWorkTree(cwd)) return null;
  const res = gitAt(cwd, ["status", "--porcelain"]);
  if (res.status !== 0) return null;
  return String(res.stdout)
    .split("\n")
    .map(parsePorcelainLine)
    .filter((c): c is GitChange => c !== null);
}

/** `git diff HEAD -- <path>` (staged+unstaged); untracked → synthetic header. */
export function fileDiff(path: string, status: GitChange["status"], cwd = resolveWorkspace()): string {
  if (status === "A") {
    return `--- /dev/null\n+++ b/${path}\n(untracked -> diff shows on first stage)`;
  }
  const res = gitAt(cwd, ["diff", "HEAD", "--", path]);
  if (res.status !== 0) return String(res.stderr) || "diff failed";
  return String(res.stdout) || `(no diff content for ${path})`;
}

/** Revert one path to HEAD (destructive — arm/confirm upstream). */
export function revertPath(path: string, cwd = resolveWorkspace()): { ok: boolean; error?: string } {
  const res = gitAt(cwd, ["checkout", "HEAD", "--", path]);
  if (res.status !== 0) {
    return { ok: false, error: String(res.stderr).trim() || "git checkout failed" };
  }
  return { ok: true };
}
