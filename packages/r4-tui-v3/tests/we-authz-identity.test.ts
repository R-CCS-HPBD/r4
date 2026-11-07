/**
 * Wave E — identity context + authz audit + denial render state.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { classifyProbeResult, authzReportLines, READ_ONLY_PROBE_RPCS } from "../src/lib/authz-audit";
import { mapBridgeStateToWorkspace, applyBridgeStatePatch } from "../src/bridge/map-state";

describe("identity from snapshot config", () => {
  beforeEach(() => {
    setWorkspace("identityProjectId", "");
    setWorkspace("identityProfile", "default");
    setWorkspace("identitySource", "");
  });

  test("config fields map into identity state", () => {
    const patch = mapBridgeStateToWorkspace({
      session: {},
      config: { profile: "mynoto", project_id: "qd-crispr", source_name: "ai4s" },
    }, undefined);
    applyBridgeStatePatch(patch);
    expect(workspace.identityProfile).toBe("mynoto");
    expect(workspace.identityProjectId).toBe("qd-crispr");
    expect(workspace.identitySource).toBe("ai4s");
  });

  test("config absent leaves identity untouched", () => {
    applyBridgeStatePatch(mapBridgeStateToWorkspace({ session: {} }, undefined));
    expect(workspace.identityProfile).toBe("default");
  });
});

describe("classifyProbeResult", () => {
  test("unscoped asset arrays are flagged", () => {
    const flags = classifyProbeResult("list_models", { models: [{ name: "x" }] });
    expect(flags).toHaveLength(1);
    expect(flags[0]!.kind).toBe("unscoped_response");
    expect(flags[0]!.rpc).toBe("list_models");
  });

  test("scope-tagged rows pass", () => {
    const flags = classifyProbeResult("list_sessions", { sessions: [{ id: "a", project_id: "p1" }] });
    expect(flags).toEqual([]);
  });

  test("empty arrays pass", () => {
    expect(classifyProbeResult("list_sessions", { sessions: [] })).toEqual([]);
  });
});

describe("authzReportLines", () => {
  test("clean report marks ✓ with the canary note", () => {
    const lines = authzReportLines({ flags: [], probed: READ_ONLY_PROBE_RPCS.length, canaryNote: "no canary" });
    expect(lines.length).toBe(3);
    expect(lines[1]).toContain("✓");
    expect(lines[2]).toContain("no canary");
  });

  test("flags list includes per-rpc lines", () => {
    const lines = authzReportLines({
      flags: [{ rpc: "list_models", kind: "rpc_failed", detail: "404" }],
      probed: 1,
      canaryNote: "no canary",
    });
    expect(lines.some((l) => l.includes("list_models") && l.includes("⚠"))).toBe(true);
  });
});

describe("denial render classification (contract)", () => {
  test("denial patterns match", () => {
    const rx = /denied|forbidden|not authorized|permission/i;
    expect(rx.test("project switch denied")).toBe(true);
    expect(rx.test("permission required")).toBe(true);
    expect(rx.test("workflow run failed")).toBe(false);
  });
});
