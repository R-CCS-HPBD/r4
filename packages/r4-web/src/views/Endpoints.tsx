/**
 * Endpoints live view (G-P9-4 FR-2, WP-08 round 2).
 *
 * Renders `/gw/endpoints` rows exactly as served. Honest color mapping per
 * endpoint status; the backend_job_id column surfaces the ADR-058 WP-22
 * identity when the row carries one (additive: absent rows just render
 * blank, never fabricated).
 */

import { useEffect, useState } from "react";
import { gatewayFetch } from "../companion/gateway";
import { Shell } from "../components/Shell";

interface EndpointRow {
  job_id: string;
  backend_job_id?: string | null;
  project_id: string;
  model: string;
  host: string;
  port: number;
  status: string;
  ready_at?: string | null;
  expires_at?: string | null;
  backend?: string;
}

const STATUS_COLOR: Record<string, string> = {
  ready: "#34d399",
  degraded: "#fbbf24",
  provisioning: "#38bdf8",
  starting: "#38bdf8",
  pending: "#fbbf24",
  failed: "#f87171",
  expired: "#a3a3a3",
  terminated: "#a3a3a3",
};

export function Endpoints() {
  const [rows, setRows] = useState<EndpointRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      try {
        const resp = await gatewayFetch("/gw/endpoints?include_stopped=true&limit=200", {
          signal: abort.signal,
        });
        if (!resp.ok) {
          setError(`/gw/endpoints answered ${resp.status}`);
          return;
        }
        const body = (await resp.json()) as { endpoints: EndpointRow[] };
        setRows(body.endpoints ?? []);
      } catch (exc) {
        if (abort.signal.aborted) return;
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => abort.abort();
  }, []);

  return (
    <Shell title="Endpoints (live)">
      <div style={{ padding: "1.25rem" }}>
        {error ? (
          <p style={{ color: "#f87171" }}>Failed to load endpoints: {error}</p>
        ) : rows === null ? (
          <p>Loading endpoints…</p>
        ) : rows.length === 0 ? (
          <p style={{ opacity: 0.8 }}>No endpoints returned (all sources empty or access denied).</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", opacity: 0.7 }}>
                <th>Instance</th>
                <th>Backend job</th>
                <th>Model</th>
                <th>Status</th>
                <th>Host:Port</th>
                <th>Ready at</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={`${e.project_id ?? ""}/${e.job_id}`} style={{ borderTop: "1px solid #26262c" }}>
                  <td style={mono}>{e.job_id}</td>
                  <td style={mono}>{e.backend_job_id ?? ""}</td>
                  <td>{e.model}</td>
                  <td>
                    <span style={{ color: STATUS_COLOR[e.status] ?? "#d4d4d8" }}>
                      {e.status}
                    </span>
                  </td>
                  <td style={mono}>
                    {e.host}:{e.port}
                  </td>
                  <td style={mono}>{(e.ready_at ?? "").slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Shell>
  );
}

const mono = { fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" };
