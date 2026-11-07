/**
 * Snapshot+patch wire rail client for the assets index (F4 follow-through).
 *
 * Server contract (`umrs/api/assets.py`):
 *   GET  /gw/assets        → {rows, snapshot_hash}
 *   POST /gw/assets/patch  → body {base: {key: shape-hash}} ⇒
 *       {snapshot_hash, upserts, tombstones, same}
 *
 * Semantics: caller holds the map (`kind:asset_id → shape-hash`) — never
 * a server-held cookie. Same visibility rules as the snapshot route.
 */

import { gatewayFetch } from "./gateway";

export interface AssetRow {
  kind: string;
  asset_id: string;
  project_id?: string | null;
  name?: string | null;
  status?: string | null;
  title?: string | null;
  description?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
  [key: string]: unknown;
}

export interface AssetSnapshot {
  rows: AssetRow[];
  snapshot_hash: string;
  count: number;
}

export interface AssetPatchResult {
  snapshot_hash: string;
  upserts: AssetRow[];
  tombstones: string[];
  same: boolean;
  count: number;
}

export async function fetchAssetSnapshot(projectId?: string): Promise<AssetSnapshot> {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  const resp = await gatewayFetch(`/gw/assets/${params.size ? `?${params}` : ""}`);
  if (!resp.ok) throw new Error(`/gw/assets answered ${resp.status}`);
  return (await resp.json()) as AssetSnapshot;
}

export async function postAssetPatch(
  base: Record<string, string>,
  projectId?: string,
): Promise<AssetPatchResult> {
  const body: Record<string, unknown> = { base };
  if (projectId) body.project_id = projectId;
  const resp = await gatewayFetch("/gw/assets/patch/", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`/gw/assets/patch answered ${resp.status}`);
  return (await resp.json()) as AssetPatchResult;
}

/** Canonical per-row key (wire contract prefix) */
export function rowShapeKey(row: AssetRow): string {
  return `${row.kind}:${row.asset_id}`;
}

/**
 * Shape-hash of one row for the patch lane's `base` map — mirrors the
 * server's exactly:
 *   sha1( json.dumps(row, sort_keys=True, default=str, separators=(",", ":")) )[:16]
 * Implemented over WebCrypto (browser) with a small-Std fallback evaluator.
 */
export async function rowShapeHash(row: AssetRow): Promise<string> {
  const canonical = canonicalJson(row);
  const data = new TextEncoder().encode(canonical);
  const digest = await crypto.subtle.digest("SHA-1", data);
  const bytes = Array.from(new Uint8Array(digest));
  return bytes.map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

/** Sorted keys path — mirrors carbons.json.dumps(sort_keys=True/default=str). */
export function canonicalJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>).sort();
    const parts = keys.map(
      (k) => `${JSON.stringify(k)}:${canonicalJson((value as Record<string, unknown>)[k])}`,
    );
    return `{${parts.join(",")}}`;
  }
  return JSON.stringify(String(value));
}

/**
 * Snapshot merger — rows are reordered deterministically by (kind, asset_id)
 * so polls produce stable output regardless of server ordering.
 */
export function mergeIntoSnapshot(
  current: AssetSnapshot,
  patch: AssetPatchResult,
): AssetSnapshot {
  if (patch.same) return { ...current, snapshot_hash: patch.snapshot_hash };
  const map = new Map<string, AssetRow>();
  for (const row of current.rows) map.set(rowShapeKey(row), row);
  for (const row of patch.upserts) map.set(rowShapeKey(row), row);
  for (const key of patch.tombstones) map.delete(key);
  const rows = [...map.values()].sort((a, b) =>
    a.kind === b.kind
      ? String(a.asset_id).localeCompare(String(b.asset_id))
      : a.kind.localeCompare(b.kind),
  );
  return { rows, snapshot_hash: patch.snapshot_hash, count: rows.length };
}
