/**
 * /sweep + /evidence command pins (sweep batch 3, 2026-09-27).
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { resetWorkspace, workspace } from "../src/stores";
import type { RpcResult } from "../src/bridge/rpc";

// Sweep replies typed by the bridge contract (rpc-types.gen.ts): a drifted fake fails tsc.
const PACK: RpcResult<"sweep.templates"> = {
  templates: [{ name: "matgen", description: "grid", required_params: ["lattice"], params: ["lattice"] }],
  count: 1,
};
const EMPTY_PACK: RpcResult<"sweep.templates"> = { templates: [], count: 0 };
const PREVIEW: RpcResult<"sweep.preview"> = {
  name: "matgen",
  description: "grid",
  required_params: [],
  job_name: "r4sweep-matgen-001",
  script: "#!/bin/bash\necho hi",
};

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
let reply: unknown = {};

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return reply;
    },
  }),
}));

const { handleSweepCommand } = await import("../src/commands/sweep-command");
const { handleEvidenceCommand } = await import("../src/commands/evidence-command");

function lastNotice(): string {
  const systems = workspace.chatMessages.filter((m) => m.role === "system");
  return systems[systems.length - 1]?.content ?? "";
}

describe("/sweep command", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    reply = PACK;
  });
  afterEach(() => resetWorkspace());

  it("bare /sweep lists the pack", async () => {
    await handleSweepCommand("");
    expect(calls).toEqual([{ method: "sweep.templates", params: {} }]);
    expect(lastNotice()).toContain("matgen");
    expect(lastNotice()).toContain("lattice");
  });

  it("preview passes the name and renders the script", async () => {
    reply = PREVIEW;
    await handleSweepCommand("preview matgen");
    expect(calls).toEqual([{ method: "sweep.preview", params: { name: "matgen" } }]);
    expect(lastNotice()).toContain("#!/bin/bash");
    expect(lastNotice()).toContain("r4sweep-matgen-001");
  });

  it("empty pack is honest, never sampled", async () => {
    reply = EMPTY_PACK;
    await handleSweepCommand("list");
    expect(lastNotice()).toContain("no templates in the pack");
  });
});

describe("/evidence command", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    reply = { evidence: [{ id: "abcdef1234", status: "retracted", claim: "alloy X is stable", source_type: "loop" }], count: 1 };
  });
  afterEach(() => resetWorkspace());

  it("renders caller-scoped rows", async () => {
    await handleEvidenceCommand("");
    expect(calls).toEqual([{ method: "evidence.list", params: { limit: 30 } }]);
    expect(lastNotice()).toContain("retracted");
    expect(lastNotice()).toContain("alloy X is stable");
    expect(lastNotice()).toContain("abcdef12");
  });

  it("status filter rides the wire", async () => {
    await handleEvidenceCommand("retracted");
    expect(calls).toEqual([{ method: "evidence.list", params: { status: "retracted", limit: 30 } }]);
  });

  it("empty plane answers honestly", async () => {
    reply = { evidence: [], count: 0 };
    await handleEvidenceCommand("retracted");
    expect(lastNotice()).toContain('no rows with status "retracted"');
  });
});
