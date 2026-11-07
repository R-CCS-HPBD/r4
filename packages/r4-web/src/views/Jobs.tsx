/**
 * Jobs live view (G-P9-4 FR-2, WP-08 round 2).
 *
 * Renders `/gw/jobs` durable jobs exactly as served. Honesty states:
 * loading placeholders are visible spinner text, empty answer prints
 * "no jobs", error prints the typed detail verbatim. Unknown or absent
 * fields render blank (never made up).
 */

import { useEffect, useState } from "react";
import { gatewayFetch } from "../companion/gateway";
import { Shell } from "../components/Shell";

interface JobRow {
  job_id: string;
  job_type: string;
  status: string;
  project_id?: string | null;
  user_id?: string | null;
  venue?: string | null;
  created_at?: string | null;
}

interface JobsResponse {
  jobs: JobRow[];
  total?: number;
}

const STATUS_COLOR: Record<string, string> = {
  completed: "#34d399",
  succeeded: "#34d399",
  running: "#38bdf8",
  queued: "#fbbf24",
  failed: "#f87171",
  cancelled: "#a3a3a3",
};

export function Jobs() {
  const [rows, setRows] = useState<JobRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      try {
        const resp = await gatewayFetch("/gw/jobs?limit=100", {
          signal: abort.signal,
        });
        if (!resp.ok) {
          setError(`/gw/jobs answered ${resp.status}`);
          return;
        }
        const body = (await resp.json()) as JobsResponse;
        setRows(body.jobs ?? []);
      } catch (exc) {
        if (abort.signal.aborted) return;
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => abort.abort();
  }, []);

  return (
    <Shell title="Jobs (durable)">
      <div style={{ padding: "1.25rem" }}>
        {error ? (
          <p style={{ color: "#f87171" }}>Failed to load jobs: {error}</p>
        ) : rows === null ? (
          <p>Loading jobs…</p>
        ) : rows.length === 0 ? (
          <p style={{ opacity: 0.8 }}>No durable jobs returned for this caller.</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", opacity: 0.7 }}>
                <th>Job ID</th>
                <th>Type</th>
                <th>Status</th>
                <th>Project</th>
                <th>Venue</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((j) => (
                <tr key={j.job_id} style={{ borderTop: "1px solid #26262c" }}>
                  <td style={mono}>{j.job_id}</td>
                  <td>{j.job_type}</td>
                  <td>
                    <span style={{ color: STATUS_COLOR[j.status] ?? "#d4d4d8" }}>
                      {j.status}
                    </span>
                  </td>
                  <td>{j.project_id ?? ""}</td>
                  <td>{j.venue ?? ""}</td>
                  <td style={mono}>{(j.created_at ?? "").slice(0, 16)}</td>
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
