/**
 * Assets rail (F4 — cross-subsystem asset index read-model).
 *
 * Snapshot-first pipeline contract (mirrors `packages/r4-web/src/companion/assets.ts`):
 *   1. First call → snapshot rail (`assets.snapshot`) → workspace.assets seeded;
 *      each row's shape-hash is computed LOCALLY via WebCrypto sha1(canonical(row))
 *      exactly like the server's `_row_shape_hash` (sha1 of canoniled JSON, 16-char
 *      prefix). The base map is `{ "<kind>:<asset_id>" → sha16 }`.
 *   2. Every later call → patch rail (`assets.patch`) with the LIVE base; the
 *      server answers only deltas (upserts + tombstones); the local base map is
 *      refreshed from the incoming rows' re-computed shape-hash values.
 *
 * Capability-detected by design: a bridge that lacks the assets.* verbs just
 * resolves to an error and the hook becomes a quiet no-op (never fabricated
 * rows, never invented defaults).
 */

import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import { workspace, actions, setWorkspace } from "../stores";
import type { UiAsset } from "../types";

/** Canonical per-row key — wire contract prefix (mirrors server's shape-key). */
export function rowShapeKey(kind: string, assetId: string): string {
  return `${kind}:${assetId}`;
}

/** Sorted-keys path — mirrors the server's json.dumps(sort_keys=True, default=str). */
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
 * Row shape-hash — exactly mirrors server's `_row_shape_hash`:
 *   sha1(canonical_json)[:16]  (WebCrypto digest, bun-compatible)
 */
export async function rowShapeHash(raw: Record<string, unknown>): Promise<string> {
  const canonical = canonicalJson(raw);
  const data = new TextEncoder().encode(canonical);
  const digest = await crypto.subtle.digest("SHA-1", data);
  const bytes = Array.from(new Uint8Array(digest));
  return bytes.map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

/** Project a server row into UiAsset truthfully (no reinterpretation). */
function strField(raw: Record<string, unknown>, key: string): string | undefined {
  const v = raw[key];
  return typeof v === "string" ? v : undefined;
}

function mapAssetRow(raw: Record<string, unknown>, shapeHash: string): UiAsset | null {
  const kind = strField(raw, "kind") ?? "";
  const id = strField(raw, "asset_id");
  if (!kind || !id) return null;
  return {
    kind,
    id,
    shapeKey: rowShapeKey(kind, id),
    projectId: strField(raw, "project_id"),
    title: strField(raw, "title"),
    status: strField(raw, "status"),
    createdAt: strField(raw, "created_at"),
    updatedAt: strField(raw, "updated_at"),
    shapeHash,
  };
}

/** Seed the base map from one batch of rows (local sha1 computation). */
async function mapRowsWithHashes(
  rows: Array<Record<string, unknown>>,
): Promise<{ mapped: UiAsset[]; base: Record<string, string> }> {
  const mapped: UiAsset[] = [];
  const base: Record<string, string> = {};
  for (const raw of rows) {
    const hash = await rowShapeHash(raw);
    const row = mapAssetRow(raw, hash);
    if (!row) continue;
    mapped.push(row);
    base[row.shapeKey] = hash;
  }
  return { mapped, base };
}

async function fetchSnapshot(): Promise<void> {
  const bridge = getCompanionBridge();
  const result = await rpc(bridge, "assets.snapshot", {});
  const rows = Array.isArray(result.rows) ? result.rows : [];
  const { mapped, base } = await mapRowsWithHashes(rows);
  actions.applyAssetsSnapshot(mapped, result.snapshot_hash ?? null);
  setWorkspace("assetsBase", base);
}

async function fetchPatch(): Promise<void> {
  const bridge = getCompanionBridge();
  const base = workspace.assetsBase;
  const result = await rpc(bridge, "assets.patch", { base });
  const rows = Array.isArray(result.rows) ? result.rows : [];
  const tombstoned = Array.isArray(result.tombstoned) ? result.tombstoned : [];
  const { mapped } = await mapRowsWithHashes(rows);
  actions.applyAssetsPatch(mapped, tombstoned, result.snapshot_hash ?? null);
}

/** F4 rail driver: first call snapshots, later calls patch (never a second
 * full snapshot while the local base is still live). Safe to call repeatedly. */
export async function pollAssetsOnce(): Promise<void> {
  if (workspace.assetsSnapshotHash === null && Object.keys(workspace.assetsBase).length === 0) {
    await fetchSnapshot();
    return;
  }
  await fetchPatch();
}

/** Reset the assets rail (project switch / diagnosis reset): the next poll
 * call snapshots again. Never silent — the workspace content clears too. */
export function resetAssetsRail(): void {
  actions.applyAssetsSnapshot([], null);
}
