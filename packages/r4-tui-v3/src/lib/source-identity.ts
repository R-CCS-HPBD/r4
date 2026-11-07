/**
 * r4 source identity — client-side digest + mismatch check (round-3 task 7).
 * Mirrors r4/src/r4/source_identity.py: sha256 over sorted rows
 *   "<relpath>|<size>|<mtime_s>\n" (UTF-8).
 */

import { createHash } from "node:crypto";
import { existsSync, readdirSync, statSync, type Dirent } from "node:fs";
import { join, relative } from "node:path";

export interface SourceIdentity {
  git_commit?: string | null;
  digest: string;
  source_dir: string;
}

export function computeSourceDigest(rootDir: string): string {
  const rows: string[] = [];
  walk(rootDir, rootDir, rows);
  const hash = createHash("sha256");
  for (const row of rows.sort()) hash.update(row + "\n", "utf8");
  return hash.digest("hex").slice(0, 16);
}

function walk(rootDir: string, dir: string, rows: string[]): void {
  // Pin the string-name overload: bare { withFileTypes: true } resolves to
  // Dirent<NonSharedBuffer> under newer @types/node defaults and broke the CI
  // typecheck twice (2026-09). encoding: "utf8" pins Dirent<string> under both.
  let entries: Dirent<string>[];
  try {
    entries = readdirSync(dir, { withFileTypes: true, encoding: "utf8" });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(rootDir, full, rows);
    } else if (entry.isFile()) {
      try {
        const st = statSync(full);
        const rel = relative(rootDir, full).split("/").join("/");
        rows.push(`${rel}|${st.size}|${Math.floor(st.mtimeMs / 1000)}`);
      } catch {
        // unreadable — skip
      }
    }
  }
}

/** True when the bridge-reported tree digest no longer matches the tree.
 *  Degrades to false when the dir can't be measured at all. */
export function sourceTreeChanged(reported: SourceIdentity): boolean {
  if (!existsSync(reported.source_dir)) return false;
  try {
    return computeSourceDigest(reported.source_dir) !== reported.digest;
  } catch {
    return false;
  }
}
