/**
 * Assets view on the snapshot+patch wire rail (F4 follow-through consumer).
 * Reads `/gw/assets` once (snapshot), then polls the caller-held
 * `/gw/assets/patch` lane every `POLL_MS` ms — never a second request for
 * full rows until the hashes spin.
 *
 * Split per the complexity gate: fetch/poll hook + pure render segments.
 */

import { useEffect, useRef, useState } from "react";
import { Shell } from "../components/Shell";
import {
  fetchAssetSnapshot,
  postAssetPatch,
  mergeIntoSnapshot,
  rowShapeHash,
  type AssetRow,
  type AssetSnapshot,
} from "../companion/assets";

const POLL_MS = 12_000;

const KIND_COLOR: Record<string, string> = {
  artifact: "#38bdf8",
  endpoints: "#34d399",
  knowledge: "#fbbf24",
  job: "#f87171",
  model: "#a78bfa",
};

async function callerBaseOf(rows: AssetRow[]): Promise<Record<string, string>> {
  const entries = await Promise.all(
    rows.map(async (r) => {
      const hash = await rowShapeHash(r);
      return [`${r.kind}:${r.asset_id}`, hash] as [string, string];
    }),
  );
  return Object.fromEntries(entries);
}

function useAssetsStore(): {
  snap: AssetSnapshot | null;
  error: string | null;
  appliedDelta: number;
} {
  const [snap, setSnap] = useState<AssetSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [appliedDelta, setAppliedDelta] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const initial = await fetchAssetSnapshot();
        if (!mounted) return;
        setSnap(initial);
        const base = await callerBaseOf(initial.rows);
        const pollOnce = async () => {
          try {
            const patch = await postAssetPatch(base);
            if (!mounted) return;
            setSnap((cur) => (cur ? mergeIntoSnapshot(cur, patch) : cur));
            setAppliedDelta(
              (d) => d + patch.upserts.length + patch.tombstones.length,
            );
          } catch (err) {
            void err;
          }
        };
        await pollOnce();
        if (mounted) timer.current = setInterval(pollOnce, POLL_MS);
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      mounted = false;
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  return { snap, error, appliedDelta };
}

function AssetTableRow({ row }: { row: AssetRow }) {
  return (
    <tr style={{ borderTop: "1px solid #26262c" }}>
      <td>
        <span style={{ color: KIND_COLOR[row.kind] ?? "#d4d4d8" }}>{row.kind}</span>
      </td>
      <td
        style={{
          fontFamily: "ui-monospace, monospace",
          fontSize: "0.8rem",
        }}
      >
        {row.asset_id.slice(0, 18)}…
      </td>
      <td>{String(row.status ?? "")}</td>
      <td>{String((row as { scope?: string }).scope ?? "")}</td>
      <td>{row.title ?? ""}</td>
    </tr>
  );
}

export function Assets() {
  const { snap, error, appliedDelta } = useAssetsStore();

  return (
    <Shell title="Assets (snapshot + patch)">
      <div style={{ padding: "1.25rem" }}>
        {error ? (
          <p style={{ color: "#f87171" }}>Failed: {error}</p>
        ) : snap === null ? (
          <p>Loading snapshot…</p>
        ) : snap.rows.length === 0 ? (
          <p style={{ opacity: 0.8 }}>
            No assets in view right now (snapshot empty, never fabricated).
          </p>
        ) : (
          <>
            <p style={{ opacity: 0.75, marginBottom: "0.75rem" }}>
              snapshot <code>{snap.snapshot_hash.slice(0, 12)}</code> · rows{" "}
              {snap.rows.length} · patches applied <strong>{appliedDelta}</strong>
            </p>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", opacity: 0.7 }}>
                  <th>Kind</th>
                  <th>Asset ID</th>
                  <th>Status</th>
                  <th>Scope</th>
                  <th>Title</th>
                </tr>
              </thead>
              <tbody>
                {snap.rows.map((row) => (
                  <AssetTableRow key={`${row.kind}:${row.asset_id}`} row={row} />
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </Shell>
  );
}
