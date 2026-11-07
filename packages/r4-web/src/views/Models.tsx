/**
 * Models catalog view — LIVE rows only (web honesty contract; P10-C3 UI leg).
 *
 * Renders `/gw/models` (`ModelListResponse`) exactly as served. The
 * `visibility_source` chip is the WP-06 round-2 grant badge: a `grant`
 * value means the row is visible because a VERIFIED live grant pins it —
 * rendered as `[shared]` (same console language as the TUI v3 picker);
 * every other value marks its ownership lane. Rows without a computed
 * reason render no chip (never a fabricated badge).
 */

import { useEffect, useState } from "react";
import { gatewayFetch } from "../companion/gateway";
import { Shell } from "../components/Shell";

interface ModelRow {
  model_id: string;
  name: string;
  scope: string;
  model_type: string;
  status: string;
  project_id: string | null;
  visibility_source: string | null;
}

interface ModelListResponse {
  models: ModelRow[];
  total: number;
}

function VisibilityChip({ source }: { source: string | null }) {
  if (!source || source === "none") return null;
  const shared = source === "grant";
  return (
    <span
      style={{
        ...styles.chip,
        ...(shared ? styles.chipShared : styles.chipLane),
      }}
      title={
        shared
          ? "Visible via a verified live grant (P10-C3)"
          : `Ownership lane: ${source}`
      }
    >
      {shared ? "[shared]" : source}
    </span>
  );
}

export function Models() {
  const [rows, setRows] = useState<ModelRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      try {
        const resp = await gatewayFetch("/gw/models?limit=200", {
          signal: abort.signal,
        });
        if (!resp.ok) throw new Error(`models fetch failed (${resp.status})`);
        const payload = (await resp.json()) as ModelListResponse;
        setRows(payload.models ?? []);
      } catch (e) {
        if (!abort.signal.aborted) {
          setError(e instanceof Error ? e.message : "models fetch failed");
        }
      }
    })();
    return () => abort.abort();
  }, []);

  return (
    <Shell title="Models">
      <div style={styles.wrap}>
        <h2 style={styles.heading}>Model catalog</h2>
        {error && <div style={styles.errorBox}>{error}</div>}
        {rows === null && !error && <div style={styles.muted}>Loading…</div>}
        {rows !== null && rows.length === 0 && (
          <div style={styles.muted}>
            No models in scope — register one server-side (e.g. via
            `r4 model stage`).
          </div>
        )}
        {rows !== null && rows.length > 0 && (
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.th}>Name</th>
                <th style={styles.th}>Scope</th>
                <th style={styles.th}>Type</th>
                <th style={styles.th}>Status</th>
                <th style={styles.th}>Project</th>
                <th style={styles.th}>Badge</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.model_id}>
                  <td style={styles.td}>{row.name}</td>
                  <td style={styles.td}>{row.scope}</td>
                  <td style={styles.td}>{row.model_type}</td>
                  <td style={styles.td}>{row.status}</td>
                  <td style={styles.tdMuted}>{row.project_id ?? "—"}</td>
                  <td style={styles.td}>
                    <VisibilityChip source={row.visibility_source} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Shell>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { padding: "1.5rem" },
  heading: { marginTop: 0, fontSize: "1.1rem" },
  muted: { color: "#64748b", fontSize: "0.85rem" },
  errorBox: {
    padding: "0.5rem 0.75rem",
    border: "1px solid #f87171",
    borderRadius: 6,
    color: "#b91c1c",
    fontSize: "0.85rem",
    marginBottom: "0.75rem",
  },
  table: { borderCollapse: "collapse", fontSize: "0.85rem", minWidth: "42rem" },
  th: {
    textAlign: "left",
    padding: "0.4rem 0.75rem",
    borderBottom: "1px solid #334155",
    color: "#94a3b8",
    fontWeight: 500,
  },
  td: { padding: "0.4rem 0.75rem", borderBottom: "1px solid #1e293b" },
  tdMuted: { padding: "0.4rem 0.75rem", borderBottom: "1px solid #1e293b", color: "#64748b" },
  chip: {
    padding: "0.05rem 0.4rem",
    borderRadius: 4,
    fontSize: "0.72rem",
    whiteSpace: "nowrap" as const,
  },
  chipShared: {
    backgroundColor: "#134e4a",
    color: "#5eead4",
    border: "1px solid #2dd4bf",
  },
  chipLane: {
    backgroundColor: "#1e293b",
    color: "#94a3b8",
    border: "1px solid #334155",
  },
};
