/**
 * Platform health summary for /health slash command.
 */

import { workspace } from "../stores";
import { fetchSources, type SourceRow } from "./sources";
import {
  healthState,
  probeSources,
  sourceBadge,
  type SourceHealth,
  type SourceLike,
} from "./source-health";

/** Public sources are provisioned outside UMRS — the /health report must
 * say whether they answer, not leave silent failures to chat time. */
function publicSources(rows: SourceRow[]): SourceRow[] {
  return rows.filter((row) => row.kind !== "platform");
}

function toSourceLike(row: SourceRow): SourceLike {
  return {
    name: row.name,
    kind: row.kind,
    url: row.url,
    api_key: row.has_api_key,
    provider: row.provider ?? null,
  };
}

/** Render the source availability section (pure — testable). */
export function formatSourceHealthLines(
  rows: SourceRow[],
  health: SourceHealth[],
): string[] {
  const publics = publicSources(rows);
  if (publics.length === 0) return [];
  const byName = new Map(health.map((h) => [h.source, h]));
  const okCount = publics.filter((row) => byName.get(row.name)?.ok).length;
  // G8: a keyed public source is not probed by the console (the key stays
  // with r4) — say so instead of counting it as down.
  const unknown = publics.filter((row) => {
    const h = byName.get(row.name);
    return h !== undefined && healthState(h) === "unknown";
  }).length;
  const unknownNote = unknown > 0 ? ` · ${unknown} unknown (key stays with r4)` : "";
  const lines = [`Sources (public): ${okCount}/${publics.length} available${unknownNote}`];
  for (const row of publics) {
    const h = byName.get(row.name);
    const glyph = sourceBadge(h).glyph;
    const detail = h ? h.detail : "not probed";
    const active = row.active ? " (active)" : "";
    lines.push(`  ${glyph} ${row.name}${active} — ${detail}`);
  }
  return lines;
}

/** Probe public sources (bounded: 5s per source, parallel). Never throws. */
export async function sourceAvailabilityLines(
  fetchRows: typeof fetchSources = fetchSources,
  prober: typeof probeSources = probeSources,
): Promise<string[]> {
  let rows: SourceRow[] = [];
  try {
    rows = await fetchRows();
  } catch {
    return [];
  }
  const publics = publicSources(rows);
  if (publics.length === 0) return [];
  const health = await prober(publics.map(toSourceLike));
  return formatSourceHealthLines(rows, health);
}

export async function buildHealthReportLines(): Promise<string[]> {
  const conn = workspace.connection;
  const lines = [
    `Backend: ${workspace.dataSource?.name ?? "—"} (${workspace.demoMode ? "demo/offline" : "live"})`,
    `GW: ${conn.gateway} | UMRS: ${conn.umrs} | HPC: ${conn.hpc}`,
  ];

  if (workspace.endpoints.length > 0) {
    const ready = workspace.endpoints.filter((e) => e.status === "ready").length;
    lines.push(`Endpoints: ${ready}/${workspace.endpoints.length} ready`);
  }

  // Public-source availability: the endpoint list covers UMRS-managed
  // endpoints only; a dead public source must surface here too.
  lines.push(...(await sourceAvailabilityLines()));

  if (workspace.platform) {
    const quality = await workspace.platform.healthCheck();
    lines.push(`Platform check: ${quality}`);
  }

  if (workspace.demoMode) {
    lines.push(
      "Demo fixtures — channels shown as connected for local UX. Set R4_TUI_BACKEND=cli for live gateway checks.",
    );
  } else if (workspace.backendError) {
    lines.push(`Backend note: ${workspace.backendError}`);
  }

  return lines;
}
