/**
 * Wave 2026-09-21 (ADR-046 A2): remote-session runtime helpers — pure rules
 * the TUI leans on (venue error parsing, lease chip, remote-turn watcher).
 */

import { describe, expect, test, beforeEach } from "bun:test";
import {
  eligibleRemoteSources,
  formatLeaseRemaining,
  formatVenueChip,
  leaseRemainingFromIso,
  mapRemoteSourceRow,
  mapSnapshotVenueRuntime,
  mapVenueRuntime,
  parseVenueError,
  partialDelta,
  remoteBadge,
  remoteTurnDone,
  startTransition,
} from "../src/lib/venue-runtime";
import { actions, setWorkspace, workspace } from "../src/stores";
import { mapBridgeStateToWorkspace } from "../src/bridge/map-state";

describe("parseVenueError", () => {
  test("source_not_pushed carries the missing names", () => {
    const parsed = parseVenueError(
      "venue.source_not_pushed: sources not pushed: openai-main, claude (run `r4 source push <name>`)",
    );
    expect(parsed?.code).toBe("source_not_pushed");
    expect(parsed?.missing).toEqual(["openai-main", "claude"]);
  });

  test("other codes parse without names; non-venue text is null", () => {
    expect(parseVenueError("venue.unavailable: not configured: UMRS_VENUE_ENABLED")?.code).toBe(
      "unavailable",
    );
    expect(parseVenueError("venue.unavailable: x")?.missing).toEqual([]);
    expect(parseVenueError("API error: 500")).toBeNull();
  });
});

describe("lease formatting", () => {
  test("lease remaining from iso + formatting", () => {
    const now = Date.parse("2026-09-21T10:00:00Z");
    expect(leaseRemainingFromIso("2026-09-21T10:47:00Z", now)).toBe(47 * 60);
    expect(leaseRemainingFromIso("2026-09-21T09:00:00Z", now)).toBe(0);
    expect(leaseRemainingFromIso("garbage", now)).toBeNull();
    expect(formatLeaseRemaining(47 * 60)).toBe("47m");
    expect(formatLeaseRemaining(2 * 3600 + 5 * 60)).toBe("2h5m");
    expect(formatLeaseRemaining(null)).toBe("");
  });
});

describe("formatVenueChip", () => {
  const base = mapVenueRuntime({ state: "running", runtime: { phase: "idle" } }, { lease_remaining_sec: 47 * 60 });
  test("running shows phase + lease, ok", () => {
    expect(formatVenueChip(base)).toEqual({ label: "remote:running/idle · lease 47m", state: "ok" });
  });
  test("short lease warns; error phase errors; stopped dims; null hides", () => {
    expect(formatVenueChip({ ...base, leaseRemainingSec: 120 })?.state).toBe("warn");
    expect(formatVenueChip({ ...base, phase: "error" })?.state).toBe("error");
    expect(formatVenueChip({ ...base, state: "stopped" })?.state).toBe("dim");
    expect(formatVenueChip({ ...base, state: "provisioning" })?.state).toBe("info");
    expect(formatVenueChip(null)).toBeNull();
    expect(formatVenueChip({ ...base, state: "" })).toBeNull();
  });
});

describe("remote source rows", () => {
  test("map + eligibility + badge", () => {
    const rows = [
      mapRemoteSourceRow({ name: "openai-main", kind: "public", url: "https://a", has_api_key: true, remote: "pushed (47m)" }),
      mapRemoteSourceRow({ name: "stale-one", kind: "public", url: "https://b", has_api_key: true, remote: "stale" }),
      mapRemoteSourceRow({ name: "nokey", kind: "public", url: "https://c", has_api_key: false, remote: "-" }),
      mapRemoteSourceRow({ name: "rccs", kind: "platform", url: "https://gw", has_api_key: true, remote: "-" }),
      mapRemoteSourceRow({ name: "unknown", kind: "public", url: "https://d", has_api_key: true, remote: "?" }),
    ];
    expect(eligibleRemoteSources(rows).map((r) => r.name)).toEqual(["openai-main", "stale-one", "unknown"]);
    expect(remoteBadge(rows[0]!)).toEqual({ glyph: "pushed (47m)", state: "ok", needsPush: false });
    expect(remoteBadge(rows[1]!).needsPush).toBe(true);
    expect(remoteBadge(rows[4]!)).toEqual({ glyph: "server unreachable", state: "error", needsPush: false });
    expect(remoteBadge(mapRemoteSourceRow({ name: "x", kind: "public", has_api_key: true, remote: "-" }))).toEqual({
      glyph: "not pushed",
      state: "dim",
      needsPush: true,
    });
    expect(rows[0]!.pushed).toBe(true);
  });
});

describe("remote-turn watcher rules", () => {
  test("partialDelta continuation vs reset", () => {
    expect(partialDelta("hel", "hello")).toEqual({ delta: "lo", reset: false });
    expect(partialDelta("hello", "new round")).toEqual({ delta: "new round", reset: true });
    expect(partialDelta("", "")).toEqual({ delta: "", reset: false });
  });

  test("remoteTurnDone", () => {
    expect(remoteTurnDone({ baseTotal: 2, total: 3, phase: "running", adopted: true })).toBe(false);
    expect(remoteTurnDone({ baseTotal: 2, total: 4, phase: "awaiting_approval", adopted: true })).toBe(false);
    expect(remoteTurnDone({ baseTotal: 2, total: 3, phase: "idle", adopted: true })).toBe(false);
    expect(remoteTurnDone({ baseTotal: 2, total: 4, phase: "idle", adopted: true })).toBe(true);
    expect(remoteTurnDone({ baseTotal: 2, total: 4, phase: "idle", adopted: false })).toBe(false);
    expect(remoteTurnDone({ baseTotal: 2, total: 3, phase: "error", adopted: false })).toBe(true);
  });
});

describe("snapshot mapping + store sweep", () => {
  beforeEach(() => {
    setWorkspace("venueRuntime", null);
    setWorkspace("sessionVenue", "local");
    setWorkspace("venuePickerOpen", false);
    setWorkspace("remoteSourcePickerOpen", false);
  });

  test("bridge snapshot carries venue + runtime into the patch", () => {
    const patch = mapBridgeStateToWorkspace(
      {
        session: {
          id: "s1",
          model: "gpt-x",
          venue: "frontend",
          venue_runtime: {
            state: "running",
            lease_expires_at: new Date(Date.now() + 600_000).toISOString(),
            source_names: ["openai-main"],
            web_policy: "public",
          },
        },
      },
      undefined,
    );
    expect(patch.sessionVenue).toBe("frontend");
    expect(patch.venueRuntime?.state).toBe("running");
    expect(patch.venueRuntime?.leaseRemainingSec).toBeGreaterThan(500);
    expect(mapSnapshotVenueRuntime(null)).toBeNull();
  });

  test("closeAllOverlays clears both pickers (G-RS-25)", () => {
    actions.openVenuePicker();
    actions.openRemoteSourcePicker("manage");
    expect(workspace.venuePickerOpen).toBe(true);
    expect(workspace.remoteSourcePickerMode).toBe("manage");
    actions.closeAllOverlays();
    expect(workspace.venuePickerOpen).toBe(false);
    expect(workspace.remoteSourcePickerOpen).toBe(false);
  });

  test("setVenueRuntime / setSessionVenue", () => {
    actions.setSessionVenue("");
    expect(workspace.sessionVenue).toBe("local");
    actions.setVenueRuntime(mapVenueRuntime({ state: "running" }));
    expect(workspace.venueRuntime?.state).toBe("running");
    actions.setVenueRuntime(null);
    expect(workspace.venueRuntime).toBeNull();
  });
});

describe("startTransition", () => {
  const base = mapVenueRuntime({ state: "provisioning" });
  test("provisioning and unavailable rows stay pending", () => {
    expect(startTransition(base).kind).toBe("pending");
    expect(startTransition(null).kind).toBe("pending");
  });
  test("running / failed / stopped settle with the reason surfaced", () => {
    expect(startTransition({ ...base, state: "running" }).kind).toBe("running");
    const failed = startTransition({ ...base, state: "failed", reason: "egress: boom" });
    expect(failed.kind).toBe("failed");
    expect(failed.text).toContain("egress: boom");
    expect(startTransition({ ...base, state: "stopped" }).kind).toBe("stopped");
  });
});
