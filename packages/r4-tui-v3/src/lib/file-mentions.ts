/**
 * File mention (@file) engine for the chat prompt (opencode autocomplete
 * paraphrase). Client-side walker + fuzzy scorer; no network round trips.
 *
 * Searches the current working directory for files whose path matches the
 * query subsequence, skipping noisy dirs and binary/no-go zones. Ranked so a
 * tight subsequence + short path + starting-on-boundary hits win.
 */

import { readdirSync, statSync } from "node:fs";
import { join as joinPath, relative as relPath } from "node:path";

export type MentionCandidate = {
  path: string;
  parentDir: string;
  score: number;
};

const SKIP_DIRS = new Set([
  ".git",
  "node_modules",
  ".venv",
  "dist",
  "build",
  "target",
  "__pycache__",
  ".kb_exports",
  ".ruff_cache",
  ".worktrees",
  "umrs/tmp",
  "tmp",
]);

const BINARY_RE = /\.(sif|whl|tar\.gz|tgz|zip|png|jpe?g|gif|webp|ico|so|dylib|a|o|pyc|lock)$/i;

const MAX_FILES = 500;
const DEPTH_LIMIT = 6;

/** Sync walker (fast enough per keystroke after a root-level warm pass). */
export function scanWorkspaceFiles(
  cwd: string,
  opts: { maxFiles?: number; maxDepth?: number } = {},
): string[] {
  const maxFiles = opts.maxFiles ?? MAX_FILES;
  const maxDepth = opts.maxDepth ?? DEPTH_LIMIT;
  const out: string[] = [];
  const stack: Array<{ dir: string; depth: number }> = [{ dir: cwd, depth: 0 }];
  while (stack.length && out.length < maxFiles) {
    const frame = stack.pop()!;
    if (frame.depth > maxDepth) continue;
    let entries: string[];
    try {
      entries = readdirSync(frame.dir).sort();
    } catch {
      continue;
    }
    for (const name of entries) {
      if (out.length >= maxFiles) break;
      if (name.startsWith(".") && name !== ".github" && name !== ".agents") continue;
      const abs = joinPath(frame.dir, name);
      const rel = relPath(cwd, abs);
      if (rel.split(/[\\/]/).some((seg) => SKIP_DIRS.has(seg))) continue;
      try {
        const info = statSync(abs);
        if (info.isDirectory()) {
          stack.push({ dir: abs, depth: frame.depth + 1 });
        } else if (info.isFile()) {
          if (BINARY_RE.test(name)) continue;
          if (info.size > 2_000_000) continue;
          out.push(rel);
        }
      } catch {
        // unreadable — skip
      }
    }
  }
  return out;
}

/** Subsequence scorer: chars of query must appear in path order. */
export function scoreMatch(path: string, query: string): number | null {
  const p = path.toLowerCase();
  const q = query.toLowerCase();
  if (!q) return 1;
  let qi = 0;
  let score = 0;
  let streak = 0;
  let lastIdx = -2;
  for (let i = 0; i < p.length && qi < q.length; i++) {
    if (p[i] === q[qi]) {
      streak = i === lastIdx + 1 ? streak + 1 : 1;
      score += 2 + streak;
      if (i === p.length - query.length + qi) score += 3; // suffix hit bonus
      lastIdx = i;
      qi++;
    }
  }
  if (qi !== q.length) return null;
  // shorter paths win ties; starting-on-boundary (after "/") bonus
  const startsOk = p.indexOf(q[0]) === 0 || p[p.indexOf(q[0]) - 1] === "/";
  if (startsOk) score += 4;
  return score - path.length / 500;
}

export function rankMentions(files: string[], query: string, limit = 8): MentionCandidate[] {
  const scored: MentionCandidate[] = [];
  for (const path of files) {
    const score = scoreMatch(path, query);
    if (score === null) continue;
    const idx = path.lastIndexOf("/");
    scored.push({
      path,
      parentDir: idx > 0 ? path.slice(0, idx) : "",
      score,
    });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit);
}

export { extractMentionQuery, type MentionQuery } from "./mention-query";
