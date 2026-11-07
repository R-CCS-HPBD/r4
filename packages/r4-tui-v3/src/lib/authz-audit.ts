/**
 * Wave E — /health authz: the client verifies the boundary (it never IS the
 * boundary). Probes read-only RPCs and flags:
 *   - rpc_failed: transport/auth failure at the edge
 *   - unscoped_response: asset-bearing response without any scope field
 * Canary-id negative probes need server support (reported honestly).
 */

export interface AuthzFlag {
  rpc: string;
  kind: "rpc_failed" | "unscoped_response";
  detail: string;
}

export const READ_ONLY_PROBE_RPCS = [
  "list_sessions",
  "list_models",
  "list_skills",
  "list_mcp_servers",
  "list_slash_commands",
];

/** Asset-bearing response arrays should carry some scope-adjacent field. */
const SCOPE_MARKERS = ["project", "project_id", "scope", "owner", "owner_id"];

/** One probe's payload → flags (pure). */
export function classifyProbeResult(rpc: string, payload: unknown): AuthzFlag[] {
  const flags: AuthzFlag[] = [];
  const arrays = findArrays(payload);
  for (const { key, rows } of arrays) {
    if (rows.length === 0) continue;
    const scoped = rows.some((row) =>
      row && typeof row === "object"
        ? Object.keys(row as Record<string, unknown>).some((k) => SCOPE_MARKERS.includes(k))
        : false,
    );
    if (!scoped) {
      flags.push({
        rpc,
        kind: "unscoped_response",
        detail: `${key || "result"}: ${rows.length} rows without any scope field`,
      });
    }
  }
  return flags;
}

function findArrays(payload: unknown): Array<{ key: string; rows: unknown[] }> {
  if (Array.isArray(payload)) return [{ key: "", rows: payload }];
  if (!payload || typeof payload !== "object") return [];
  const out: Array<{ key: string; rows: unknown[] }> = [];
  for (const [key, value] of Object.entries(payload as Record<string, unknown>)) {
    if (Array.isArray(value)) out.push({ key, rows: value });
  }
  return out;
}

/** Full probe run over the read-only RPC list (each flagged = audit debt). */
export async function probeAuthz(
  request: (method: string, params: Record<string, unknown>, timeoutMs: number) => Promise<unknown>,
): Promise<{ flags: AuthzFlag[]; probed: number; canaryNote: string }> {
  const flags: AuthzFlag[] = [];
  for (const rpc of READ_ONLY_PROBE_RPCS) {
    try {
      const payload = await request(rpc, {}, 10_000);
      flags.push(...classifyProbeResult(rpc, payload));
    } catch (err) {
      flags.push({
        rpc,
        kind: "rpc_failed",
        detail: err instanceof Error ? err.message : String(err),
      });
    }
  }
  return {
    flags,
    probed: READ_ONLY_PROBE_RPCS.length,
    canaryNote: "canary-id negative probes require server support (not probed)",
  };
}

/** Summary for the /health authz report. */
export function authzReportLines(result: {
  flags: AuthzFlag[];
  probed: number;
  canaryNote: string;
}): string[] {
  const lines = [
    `authz audit — probed ${result.probed} read-only RPCs`,
    ...(result.flags.length === 0
      ? ["  ✓ no flags (all responses scope-aware or empty)"]
      : result.flags.map((f) => `  ⚠︎ ${f.rpc}: ${f.kind} — ${f.detail}`)),
    `  note: ${result.canaryNote}`,
  ];
  return lines;
}
