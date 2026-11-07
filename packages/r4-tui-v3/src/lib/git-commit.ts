/**
 * Git commit tool with verification — prevents false commit claims.
 * Returns the real commit hash from `git log`, test/lint status, and the diff.
 */

import { execSync } from "child_process";
import { buildGitCommitResult, type GitCommitResult } from "./tool-result";

export interface GitCommitOptions {
  message: string;
  verify?: boolean; // run tests + lint before committing
  cwd?: string;
}

/**
 * Commit changes with verification.
 * Returns the real commit hash from `git log -1 --format=%H`.
 */
export async function gitCommit(options: GitCommitOptions): Promise<GitCommitResult> {
  const { message, verify = true, cwd = process.cwd() } = options;

  try {
    // Stage all changes
    execSync("git add -A", { cwd, stdio: "pipe" });

    // Run verification if requested
    let testsPassed = true;
    let lintPassed = true;

    if (verify) {
      try {
        // Check if this is the r4-tui-v3 package
        if (cwd.includes("r4-tui-v3")) {
          execSync("bun run check", { cwd, stdio: "pipe" });
        } else if (cwd.includes("/r4")) {
          execSync("bash scripts/ci-lint-local.sh", { cwd, stdio: "pipe" });
        }
      } catch (err) {
        testsPassed = false;
        lintPassed = false;
        return buildGitCommitResult(
          false,
          "",
          false,
          false,
          "",
          `Verification failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    // Commit
    execSync(`git commit -m "${message.replace(/"/g, '\\"')}"`, { cwd, stdio: "pipe" });

    // Get the real commit hash
    const commitHash = execSync("git log -1 --format=%H", { cwd, encoding: "utf-8" }).trim();

    // Get the diff
    const diff = execSync("git show --format= --patch", { cwd, encoding: "utf-8" });

    return buildGitCommitResult(true, commitHash, testsPassed, lintPassed, diff);
  } catch (err) {
    return buildGitCommitResult(
      false,
      "",
      false,
      false,
      "",
      `Commit failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}
