/**
 * r4 source identity comparison (round-3 task 7).
 *
 * The ACP server reports its source identity (git commit + content digest
 * over the package tree) in the initialize handshake. The extension
 * recomputes the digest over the same source_dir and warns on mismatch —
 * the working-tree-edits-after-process-start case (stale ACP child).
 *
 * Digest rules mirror r4/src/r4/source_identity.py exactly:
 *   sha256 over sorted rows  "<relpath>|<size>|<mtime_s>\n"  (UTF-8).
 */

import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

function dirExists(p: string): boolean {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

export interface SourceIdentity {
  git_commit?: string | null;
  digest: string;
  source_dir: string;
}

export function computeSourceDigest(rootDir: string): string {
  const rows: string[] = [];
  walk(rootDir, rootDir, rows);
  const hash = createHash("sha256");
  for (const row of rows.sort()) {
    hash.update(row + "\n", "utf8");
  }
  return hash.digest("hex").slice(0, 16);
}

function walk(rootDir: string, dir: string, rows: string[]): void {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(rootDir, full, rows);
    } else if (entry.isFile()) {
      try {
        const st = fs.statSync(full);
        const rel = path.relative(rootDir, full).split(path.sep).join("/");
        rows.push(`${rel}|${st.size}|${Math.floor(st.mtimeMs / 1000)}`);
      } catch {
        // unreadable file — skip (matches python OSError branch)
      }
    }
  }
}

/** Compare server-reported identity with a fresh client-side digest. */
export function identityMismatch(reported: SourceIdentity): boolean {
  if (!dirExists(reported.source_dir)) return false;
  try {
    return computeSourceDigest(reported.source_dir) !== reported.digest;
  } catch {
    // Source dir unreadable (fresh install dir moved) — degrade gracefully.
    return false;
  }
}
