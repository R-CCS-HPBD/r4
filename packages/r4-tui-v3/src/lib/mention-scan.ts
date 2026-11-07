/**
 * Lazily-built workspace file index for @-mentions (warm on first `@` typed,
 * rebuilt if the cwd changes or the index is older than the TTL).
 */
import { scanWorkspaceFiles } from "./file-mentions";

const INDEX_TTL_MS = 30_000;

type Index = { cwd: string; files: string[]; builtAt: number };
let cached: Index | null = null;
let inflight: Promise<void> | null = null;

async function rebuild(cwd: string): Promise<void> {
  // The walker is sync; run it off the current tick so the input handler
  // stays responsive on the first keystroke that opens the browser.
  await Promise.resolve().then(() => {
    cached = { cwd, files: scanWorkspaceFiles(cwd), builtAt: Date.now() };
  });
}

export function workspaceFiles(): string[] {
  if (!cached) return [];
  return cached.files;
}

/** Start a warm pass if needed. Idempotent while a pass is in flight. */
export function ensureWorkspaceIndex(cwd: string): void {
  const stale = !cached || cached.cwd !== cwd || Date.now() - cached.builtAt > INDEX_TTL_MS;
  if (!stale || inflight) return;
  inflight = rebuild(cwd).finally(() => {
    inflight = null;
  });
}

export function refreshWorkspaceIndex(cwd: string): void {
  cached = null;
  ensureWorkspaceIndex(cwd);
}
