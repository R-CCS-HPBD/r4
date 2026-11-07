/**
 * P2/P3 (plan 2026-09-09) — session venue + unified listing:
 * record mapping of the additive wire fields (venue/state/sync), filter-chip
 * state machine (s/v/A cycles, wire params), venue picker gating
 * (remote_available=false → Server disabled WITH reason).
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { mapSessionRecord, resumeNoticeText } from "../src/bridge/session-bridge";
import {
  SESSION_STATE_ORDER,
  SESSION_VENUE_ORDER,
  DEFAULT_SESSION_FILTERS,
  REMOTE_ONLY_BLOCKED_VERBS,
  buildListSessionsParams,
  cycleSessionStateFilter,
  cycleSessionVenueFilter,
  venuePickerOptions,
  venueGlyph,
  venueWord,
  isRemoteOnly,
  sessionVerbBlockedForRemoteOnly,
} from "../src/lib/session-filters";
import { remoteOnlyReasonLine } from "../src/routes/sessions";

describe("venue picker — cluster option shape (P6 grammar hook)", () => {
  test("cluster row present, always disabled-with-reason, non-selectable", () => {
    const options = venuePickerOptions(true, "");
    const cluster = options.find((o) => o.key === "c");
    expect(cluster).toBeDefined();
    expect(cluster!.disabled).toBe(true);
    expect(cluster!.disabledReason).toContain("loop-jobs-only");
    expect(cluster!.choice.startsWith("cluster:")).toBe(true);
  });

  test("cluster row consequence text explains runtime gap honestly", () => {
    const cluster = venuePickerOptions(true, "").find((o) => o.key === "c")!;
    expect(cluster.consequence).toContain("loop-runtime-only");
  });

  test("local and server rows unchanged (back-compat of picker shape)", () => {
    const options = venuePickerOptions(true, "");
    expect(options.find((o) => o.choice === "local")?.disabled).toBe(false);
    expect(options.find((o) => o.choice === "frontend")?.disabled).toBe(false);
    expect(options.find((o) => o.key === "l")).toBeDefined();
    expect(options.find((o) => o.key === "f")).toBeDefined();
  });
});

describe("mapSessionRecord — additive venue/state/sync fields", () => {
  test("maps the new fields when present and valid", () => {
    const record = mapSessionRecord({
      id: "abc123",
      name: "s",
      model: "m",
      created_at: "",
      updated_at: "",
      venue: "frontend",
      state: "ongoing",
      sync: "mirrored",
    });
    expect(record?.venue).toBe("frontend");
    expect(record?.state).toBe("ongoing");
    expect(record?.sync).toBe("mirrored");
  });

  test("absent fields default to empty (pre-venue servers)", () => {
    const record = mapSessionRecord({ id: "abc123", name: "s", model: "m", created_at: "", updated_at: "" });
    expect(record?.venue).toBe("");
    expect(record?.state).toBe("");
    expect(record?.sync).toBe("");
  });

  test("unknown sync words never invent a badge (fixed §8a-5 vocabulary)", () => {
    for (const word of ["MIRRORED", "synced", "half-remote", 42, null]) {
      const record = mapSessionRecord({ id: "x", sync: word });
      expect(record?.sync).toBe("");
    }
    for (const word of ["local-only", "remote-only", "mirrored", "degraded"] as const) {
      const record = mapSessionRecord({ id: "x", sync: word });
      expect(record?.sync).toBe(word);
    }
  });

  test("non-string venue/state collapse to empty (drift tolerated)", () => {
    const record = mapSessionRecord({ id: "x", venue: 3, state: false });
    expect(record?.venue).toBe("");
    expect(record?.state).toBe("");
    const cluster = mapSessionRecord({ id: "x", venue: "cluster:rccs" });
    expect(cluster?.venue).toBe("cluster:rccs");
  });
});

describe("filter-chip state machine (pure)", () => {
  test("s cycles all → ongoing → stopped → completed → error → archived → all", () => {
    const seen: string[] = [];
    let cur = SESSION_STATE_ORDER[0]!;
    for (let i = 0; i <= SESSION_STATE_ORDER.length; i++) {
      seen.push(cur);
      cur = cycleSessionStateFilter(cur);
    }
    expect(seen).toEqual(["all", "ongoing", "stopped", "completed", "error", "archived", "all"]);
  });

  test("v cycles all → local → frontend → all", () => {
    const seen: string[] = [];
    let cur = SESSION_VENUE_ORDER[0]!;
    for (let i = 0; i <= SESSION_VENUE_ORDER.length; i++) {
      seen.push(cur);
      cur = cycleSessionVenueFilter(cur);
    }
    expect(seen).toEqual(["all", "local", "frontend", "all"]);
  });

  test("first-visit defaults: all/all, archives hidden (§8a-4)", () => {
    expect(DEFAULT_SESSION_FILTERS).toEqual({ state: "all", venue: "all", includeArchived: false });
    expect(buildListSessionsParams(DEFAULT_SESSION_FILTERS)).toEqual({ limit: 50 });
  });

  test("wire params omit empty filters; archived chip implies include_archived", () => {
    expect(buildListSessionsParams({ state: "ongoing", venue: "frontend", includeArchived: false })).toEqual({
      limit: 50,
      state: "ongoing",
      venue: "frontend",
    });
    expect(buildListSessionsParams({ state: "archived", venue: "all", includeArchived: false })).toEqual({
      limit: 50,
      state: "archived",
      include_archived: true,
    });
    expect(buildListSessionsParams({ state: "stopped", venue: "all", includeArchived: true })).toEqual({
      limit: 50,
      state: "stopped",
      include_archived: true,
    });
    // state != archived + archives off → no include_archived key on the wire
    const params = buildListSessionsParams({ state: "error", venue: "local", includeArchived: false });
    expect("include_archived" in params).toBe(false);
  });
});

describe("filter-chip store actions", () => {
  beforeEach(() => {
    setWorkspace("sessionFilters", { state: "all", venue: "all", includeArchived: false });
  });

  test("store defaults match first-visit rule", () => {
    // reset to the true default, then read it back through the store
    expect(workspace.sessionFilters).toEqual({ state: "all", venue: "all", includeArchived: false });
  });

  test("s/v/A actions mutate the store chips in cycle order", () => {
    expect(actions.cycleSessionStateFilter()).toBe("ongoing");
    expect(workspace.sessionFilters.state).toBe("ongoing");
    expect(actions.cycleSessionStateFilter()).toBe("stopped");
    actions.cycleSessionStateFilter();
    actions.cycleSessionStateFilter();
    expect(actions.cycleSessionStateFilter()).toBe("archived");
    expect(actions.cycleSessionStateFilter()).toBe("all");

    expect(actions.cycleSessionVenueFilter()).toBe("local");
    expect(actions.cycleSessionVenueFilter()).toBe("frontend");
    expect(actions.cycleSessionVenueFilter()).toBe("all");

    expect(actions.toggleSessionArchived()).toBe(true);
    expect(workspace.sessionFilters.includeArchived).toBe(true);
    expect(actions.toggleSessionArchived()).toBe(false);
  });
});

describe("venue picker gating", () => {
  test("journal unreachable → Server disabled WITH its reason; Local stays", () => {
    const options = venuePickerOptions(false, "journal HTTP 503");
    const local = options.find((o) => o.choice === "local")!;
    const server = options.find((o) => o.choice === "frontend")!;
    expect(local.disabled).toBe(false);
    expect(local.disabledReason).toBe("");
    expect(server.disabled).toBe(true);
    expect(server.disabledReason).toContain("journal HTTP 503");
  });

  test("journal reachable → both options enabled, no reasons", () => {
    const options = venuePickerOptions(true, "");
    // Local and Server rows enabled with a reachable journal; the Cluster
    // row is always disabled-with-reason (P6 grammar hook, runtime gap).
    const selectable = options.filter((o) => o.key !== "c");
    expect(selectable.every((o) => !o.disabled)).toBe(true);
    expect(options.find((o) => o.key === "c")?.disabled).toBe(true);
    expect(selectable.every((o) => o.disabledReason === "")).toBe(true);
  });

  test("empty reason still yields an honest explanation (never a bare dead button)", () => {
    const server = venuePickerOptions(false, "   ").find((o) => o.choice === "frontend")!;
    expect(server.disabled).toBe(true);
    expect(server.disabledReason.length).toBeGreaterThan(0);
    expect(server.disabledReason).toContain("unavailable");
  });

  test("options carry consequences in plain language", () => {
    const options = venuePickerOptions(true, "");
    expect(options.find((o) => o.choice === "local")!.consequence).toContain("dies when r4 exits");
    expect(options.find((o) => o.choice === "frontend")!.consequence).toContain("survives disconnect");
  });
});

describe("venue/state row presentation helpers", () => {
  test("venue glyph/word — unknown rows never fabricated", () => {
    expect(venueGlyph("local")).toBe("L");
    expect(venueGlyph("frontend")).toBe("F");
    expect(venueGlyph("cluster:rccs")).toBe("C");
    expect(venueGlyph("")).toBe("?");
    expect(venueWord("local")).toBe("local");
    expect(venueWord("frontend")).toBe("frontend");
    expect(venueWord("cluster:rccs")).toBe("cluster:rccs");
    expect(venueWord("")).toBe("unknown");
  });

  test("isRemoteOnly matches the badge vocabulary exactly", () => {
    expect(isRemoteOnly({ sync: "remote-only" })).toBe(true);
    expect(isRemoteOnly({ sync: "mirrored" })).toBe(false);
    expect(isRemoteOnly({ sync: "local-only" })).toBe(false);
    expect(isRemoteOnly({ sync: "degraded" })).toBe(false);
    expect(isRemoteOnly({})).toBe(false);
  });
});

describe("remote-only verb gating (pull-fallback round)", () => {
  const remoteRow = { sync: "remote-only" };

  test("resume is allowed on remote-only rows (pull rides the bridge)", () => {
    expect(sessionVerbBlockedForRemoteOnly(remoteRow, "resume")).toBe(false);
  });

  test("rename/pin/archive/export stay blocked on remote-only rows", () => {
    for (const verb of ["rename", "pin", "archive", "export"]) {
      expect(sessionVerbBlockedForRemoteOnly(remoteRow, verb)).toBe(true);
    }
    expect(REMOTE_ONLY_BLOCKED_VERBS).not.toContain("resume");
  });

  test("every verb stays allowed on local-present rows", () => {
    for (const sync of ["", "local-only", "mirrored", "degraded"]) {
      for (const verb of ["resume", "rename", "pin", "archive", "export"]) {
        expect(sessionVerbBlockedForRemoteOnly({ sync }, verb)).toBe(false);
      }
    }
  });

  test("disabled-reason line never implies resume is blocked", () => {
    const line = remoteOnlyReasonLine();
    expect(line).toContain("resume pulls from journal");
    expect(line).toContain("rename");
    expect(line).toContain("export");
    expect(line).not.toMatch(/resume\/rename/);
    expect(line.toLowerCase()).not.toContain("resume unavailable");
  });
});

describe("resumeNoticeText — server notice mapping", () => {
  const id = "abcdefghijklmnopqrstuvwxyz";

  test("snapshot notice wins (pull-fallback: pulled from server journal)", () => {
    expect(
      resumeNoticeText(id, { notice: "pulled from server journal before resume", messages: [] }),
    ).toBe("pulled from server journal before resume");
  });

  test("absent/blank/non-string notice falls back to the resumed line", () => {
    expect(resumeNoticeText(id, { messages: [] })).toBe(`resumed session ${id.slice(0, 8)}`);
    expect(resumeNoticeText(id, { notice: "   " })).toBe(`resumed session ${id.slice(0, 8)}`);
    expect(resumeNoticeText(id, { notice: 42 })).toBe(`resumed session ${id.slice(0, 8)}`);
    expect(resumeNoticeText(id, null)).toBe(`resumed session ${id.slice(0, 8)}`);
    expect(resumeNoticeText(id, undefined)).toBe(`resumed session ${id.slice(0, 8)}`);
  });
});
