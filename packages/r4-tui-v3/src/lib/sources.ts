/**
 * R4 source inventory + switch helper (r4 CLI, no bridge round-trip).
 * The active source decides the platform gateway; switching re-syncs the
 * umrs profile server-side before the bridge gets reconnected.
 */

import { resolveR4Command } from "./resolve-python";
import { parseTerminalSafe, terminalSafe } from "./terminal-safe";

export interface SourceRow {
  name: string;
  kind: string;
  url: string;
  active: boolean;
  provider?: string | null;
  description?: string | null;
  has_api_key?: boolean;
  /** R1/R2 sealed-remote badge ("pushed (12m)" | "stale" | "tombstoned" | "-" | "?" | undefined when not requested). */
  remote?: string;
}

async function runR4(args: string[], timeoutMs = 20_000) {
  const proc = Bun.spawn([...resolveR4Command(), ...args], {
    stdout: "pipe",
    stderr: "pipe",
    signal: AbortSignal.timeout(timeoutMs),
  });
  const stdout = await new Response(proc.stdout).text();
  const stderr = await new Response(proc.stderr).text();
  await proc.exited;
  // CLI output reaches the screen (c3-tui D-3): no live control characters.
  return { stdout: terminalSafe(stdout), stderr: terminalSafe(stderr), exitCode: proc.exitCode ?? 0 };
}

/** Tolerant mapper for one `r4 source list` row (exported for tests). */
export function mapSourceRow(row: Record<string, unknown>): SourceRow {
  return {
    name: String(row.name ?? ""),
    kind: String(row.kind ?? "?"),
    url: String(row.url ?? ""),
    active: Boolean(row.active),
    provider: row.provider == null ? null : String(row.provider),
    description: row.description == null ? null : String(row.description),
    has_api_key:
      typeof row.has_api_key === "boolean"
        ? row.has_api_key
        : typeof row.api_key === "boolean"
          ? row.api_key
          : undefined,
    remote: typeof row.remote === "string" ? row.remote : undefined,
  };
}

/** Parse `r4 source list --json --remote` (badges for pushed sources).
 *  A failed listing THROWS with r4's own line: returning [] made the /source
 *  overlay show an empty state (and another feature's message) instead of
 *  the error (connect#7, 2026-10-21). */
export async function fetchSources(): Promise<SourceRow[]> {
  const out = await runR4(["source", "list", "--json", "--remote"]);
  if (out.exitCode !== 0) {
    const said = (out.stderr || out.stdout).split("\n").map((l) => l.trim()).find(Boolean);
    throw new Error(`r4 source list failed: ${said ?? `exit ${out.exitCode}`}`);
  }
  let data: unknown;
  try {
    data = parseTerminalSafe(out.stdout);
  } catch {
    throw new Error("r4 source list printed no JSON — is this r4 too old for --json?");
  }
  const list = Array.isArray(data) ? data : ((data as { sources?: unknown })?.sources ?? []);
  return (list as Record<string, unknown>[]).map(mapSourceRow);
}

/** `r4 source use <name>` — persistent active-source switch (+ profile sync). */
export async function useSource(name: string): Promise<{ ok: boolean; stderr: string }> {
  const out = await runR4(["source", "use", name], 30_000);
  return { ok: out.exitCode === 0, stderr: out.stderr || out.stdout };
}
