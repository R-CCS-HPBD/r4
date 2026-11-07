/**
 * /models picker with public sources (stabilization 2026-10-21).
 *
 * connect#2        the configured public row (`instance_id: "public"`,
 *                  model null → mapper "unknown") became a pickable
 *                  "◐ unknown" row per source, highlighted first.
 * slash-a#1        the same row carrying the profile's built-in default
 *                  (rivault-gpt-oss-static, served by nobody) did too.
 * resize-render#8  a registered source's model sat under "unknown" as ◐
 *                  (any non-ready status drew the provisioning glyph).
 * chat#6           a probed public model drew ◐, not ● — and the bare
 *                  endpoint twin hid the routable `model@source` row.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, resetWorkspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { resolveSlashPicker } from "../src/commands/slash-pickers";
import { mapEndpoint } from "../src/adapters/mappers";
import { setDemoMode } from "../src/lib/console-mode";

let savedBackend: string | undefined;
beforeEach(() => {
  savedBackend = process.env.R4_TUI_BACKEND;
  delete process.env.R4_TUI_BACKEND;
  setDemoMode(false);
  resetWorkspace();
});
afterEach(() => {
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
  setCompanionBridgeForTests(null);
  resetWorkspace();
});

/** list_models answers like the bridge: public models are qualified. */
function useBridge(models: Array<{ name: string; status?: string }>, current = ""): void {
  setCompanionBridgeForTests({
    request: async (method: string) => (method === "list_models" ? { models, current, problems: [] } : {}),
  } as never);
}

/** `r4 endpoint list --json` rows for configured public sources. */
function publicRows(rows: Array<{ model: string | null; source?: string }>): void {
  actions.setBackendState({
    endpoints: rows.map((r) =>
      mapEndpoint({ instance_id: "public", tier: "public", model: r.model, status: "configured", _source: r.source } as never),
    ),
  });
}

describe("/models picker — public sources", () => {
  test("no fake 'unknown' or built-in-default row; only the models sources serve", async () => {
    publicRows([{ model: null, source: "alpha" }, { model: null, source: "beta" }, { model: "rivault-gpt-oss-static" }]);
    useBridge([
      { name: "a1@alpha", status: "available" },
      { name: "a2@alpha", status: "available" },
      { name: "b1@beta", status: "available" },
    ]);
    const picker = await resolveSlashPicker("/models");
    expect(picker!.items.map((i) => i.modelName)).toEqual(["a1@alpha", "a2@alpha", "b1@beta"]);
    expect(picker!.items.map((i) => i.group)).toEqual(["alpha", "alpha", "beta"]);
  });

  test("a probed model is ● ready under its source, not ◐ under 'unknown'", async () => {
    publicRows([{ model: "fake-md" }]);
    useBridge([{ name: "fake-md@fakemd", status: "available" }]);
    const picker = await resolveSlashPicker("/models");
    expect(picker!.items).toHaveLength(1);
    expect(picker!.items[0]!.modelName).toBe("fake-md@fakemd");
    expect(picker!.items[0]!.group).toBe("fakemd");
    expect(picker!.items[0]!.label.startsWith("● ")).toBe(true);
  });

  test("the picker opens on the current model", async () => {
    useBridge([{ name: "a1@alpha", status: "available" }, { name: "b1@beta", status: "available" }], "b1@beta");
    const picker = await resolveSlashPicker("/models");
    expect(picker!.items[0]!.modelName).toBe("b1@beta");
    expect(picker!.items[0]!.label).toContain("← current");
  });

  test("a stopped platform endpoint is ○, a provisioning one ◐", async () => {
    actions.setBackendState({
      endpoints: [
        mapEndpoint({ instance_id: "ep-1", model: "qwen-7b", status: "stopped" } as never),
        mapEndpoint({ instance_id: "ep-2", model: "llama-8b", status: "provisioning" } as never),
      ],
    });
    useBridge([]);
    const picker = await resolveSlashPicker("/models");
    const label = (m: string) => picker!.items.find((i) => i.modelName === m)!.label;
    expect(label("qwen-7b").startsWith("○ ")).toBe(true);
    expect(label("llama-8b").startsWith("◐ ")).toBe(true);
  });
});
