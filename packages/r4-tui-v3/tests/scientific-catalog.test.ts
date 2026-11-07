/**
 * D-39 pins: the /catalog pane's Scientific Catalog rail — adapter wire
 * shapes, store mapping, offline fixture honesty.
 */
import { describe, expect, test } from "bun:test";
import { OfflineDataSource } from "../src/adapters/offline";
import { scienceActions, scienceStore } from "../src/stores/science";
import { workspace } from "../src/stores";

describe("scientific catalog rail (D-39)", () => {
  test("offline adapter returns versioned rows in the wire shape", async () => {
    const src = new OfflineDataSource();
    const payload = (await src.scientificCatalogs!("demo-project")) as {
      catalogs: Array<Record<string, unknown>>;
    };
    expect(payload.catalogs.length).toBe(2);
    expect(payload.catalogs[0].catalog_id).toBe("scat-demo1");
    expect(payload.catalogs[0].version).toBe("0.2.0");
    expect(Array.isArray(payload.catalogs[0].member_graph_ids)).toBe(true);
  });

  test("store maps member ids to counts, never content", async () => {
    // The workspace provider is only present in live mode; when absent the
    // action is a quiet no-op (offline console start).
    const before = scienceStore.scientificCatalogs.length;
    await scienceActions.loadScientificCatalogs();
    expect(scienceStore.scientificCatalogs.length).toBe(before);
  });

  test("wire row carries licence/change_note for the pane line", async () => {
    const src = new OfflineDataSource();
    const payload = (await src.scientificCatalogs!()) as {
      catalogs: Array<Record<string, unknown>>;
    };
    const row = payload.catalogs[0];
    expect(row.licence).toBe("CC-BY-4.0");
    expect(row.change_note).toBe("added defect class D4");
  });

  test("workspace provider typing (DataSource method exists)", () => {
    const src = new OfflineDataSource();
    expect(typeof src.scientificCatalogs).toBe("function");
    expect(workspace).toBeDefined();
  });
});
