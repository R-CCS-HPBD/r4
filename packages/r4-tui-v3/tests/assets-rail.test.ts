/**
 * F4 assets rail — stores-level pins (TUI/capability-detected absorption).
 *
 * The rails is honest-contract graded: shape-hash computed locally via the
 * WebCrypto/canonicalJSON pair mirroring the server's _row_shape_hash byte-
 * identical, base map holds `<kind>:<asset_id>` keys verbatim, and reductions
 * never fabricate rows. First call snapshots; later calls patch.
 */

import { describe, expect, it, beforeEach } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { canonicalJson, rowShapeKey } from "../src/lib/assets-rail";
import type { UiAsset } from "../src/types";

beforeEach(() => {
  resetWorkspace();
});

describe("assets rail — store reducers (F4)", () => {
  const row1: UiAsset = {
    kind: "model",
    id: "m-1",
    shapeKey: "model:m-1",
    projectId: "demo",
    title: "GLM-5.2",
    status: "ready",
    shapeHash: "aaa",
  };
  const row2: UiAsset = {
    kind: "endpoint",
    id: "e-2",
    shapeKey: "endpoint:e-2",
    status: "ready",
    shapeHash: "bbb",
  };

  it("applyAssetsSnapshot seeds rows + base + snapshotHash in one move", () => {
    actions.applyAssetsSnapshot([row1, row2], "snap-0001");
    expect(workspace.assets.map((a) => a.shapeKey)).toEqual(["model:m-1", "endpoint:e-2"]);
    expect(workspace.assetsBase).toEqual({ "model:m-1": "aaa", "endpoint:e-2": "bbb" });
    expect(workspace.assetsSnapshotHash).toBe("snap-0001");
  });

  it("applyAssetsSnapshot missing shapeHash keeps row visible but does not stitch base entry", () => {
    actions.applyAssetsSnapshot([{ ...row1, shapeHash: undefined }], "snap-2");
    expect(workspace.assets.map((a) => a.shapeKey)).toEqual(["model:m-1"]);
    expect(workspace.assetsBase).toEqual({});
  });

  it("applyAssetsPatch upserts incoming rows by their own shapeKey", () => {
    actions.applyAssetsSnapshot([row1], "snap-1");
    actions.applyAssetsPatch(
      [{ ...row2, shapeHash: "bbb-forged" }],
      [],
      "snap-2",
    );
    expect(workspace.assets.map((a) => a.shapeKey).sort()).toEqual(["endpoint:e-2", "model:m-1"]);
    expect(workspace.assetsBase["endpoint:e-2"]).toBe("bbb-forged");
    expect(workspace.assetsBase["model:m-1"]).toBe("aaa");
    expect(workspace.assetsSnapshotHash).toBe("snap-2");
  });

  it("applyAssetsPatch removes tombstoned shape-keys", () => {
    actions.applyAssetsSnapshot([row1, row2], "snap-1");
    actions.applyAssetsPatch([], ["model:m-1"], "snap-3");
    expect(workspace.assets.map((a) => a.shapeKey)).toEqual(["endpoint:e-2"]);
    expect(workspace.assetsBase).toEqual({ "endpoint:e-2": "bbb" });
  });

  it("applyAssetsPatch rewrites shapeKey entries (upsert semantics — never duplicated)", () => {
    actions.applyAssetsSnapshot([row1], "snap-1");
    actions.applyAssetsPatch(
      [{ ...row1, status: "down", shapeHash: "zzz" }],
      [],
      "snap-9",
    );
    expect(workspace.assets.length).toBe(1);
    expect(workspace.assets[0].status).toBe("down");
    expect(workspace.assetsBase["model:m-1"]).toBe("zzz");
  });
});

describe("assets rail — canonical helpers (server mirror contract)", () => {
  it("rowShapeKey follows the server's colon-of-kind/asset_id form", () => {
    expect(rowShapeKey("model", "m-42")).toBe("model:m-42");
  });

  it("canonicalJson preserves sorted keys + primitive transcription byte-identical", () => {
    expect(canonicalJson({ b: 1, a: "x" })).toBe('{"a":"x","b":1}');
    expect(canonicalJson(null)).toBe("null");
    expect(canonicalJson(true)).toBe("true");
    expect(canonicalJson([1, "a"])).toBe('[1,"a"]');
    expect(canonicalJson({ nested: { z: 0, a: true } })).toBe('{"nested":{"a":true,"z":0}}');
  });

  it("resetWorkspace leaves the assets rail clean between runs", () => {
    actions.applyAssetsSnapshot([{
      kind: "model",
      id: "m-9",
      shapeKey: "model:m-9",
      shapeHash: "xxx",
    }], "snap");
    resetWorkspace();
    expect(workspace.assets).toEqual([]);
    expect(workspace.assetsBase).toEqual({});
    expect(workspace.assetsSnapshotHash).toBe(null);
  });
});
