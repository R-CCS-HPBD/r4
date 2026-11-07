/**
 * Wave D — /sessions pane: cursor clamp, record mapping, snapshot replay,
 * export front-matter.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { mapSnapshotMessages, mapSessionRecord, sessionExportFrontMatter } from "../src/bridge/session-bridge";

describe("sessions cursor + list", () => {
  beforeEach(() => {
    setWorkspace("sessionsList", []);
    setWorkspace("sessionsCursor", 0);
  });

  test("setSessionsList clamps a stale cursor", () => {
    setWorkspace("sessionsCursor", 5);
    actions.setSessionsList([
      { id: "a", name: "one", model: "m", created_at: "", updated_at: "" },
      { id: "b", name: "two", model: "m", created_at: "", updated_at: "" },
    ]);
    expect(workspace.sessionsCursor).toBe(1);
  });

  test("movement is clamped both ways", () => {
    actions.setSessionsList([
      { id: "a", name: "one", model: "m", created_at: "", updated_at: "" },
      { id: "b", name: "two", model: "m", created_at: "", updated_at: "" },
    ]);
    actions.moveSessionCursor(-99);
    expect(workspace.sessionsCursor).toBe(0);
    actions.moveSessionCursor(99);
    expect(workspace.sessionsCursor).toBe(1);
    actions.moveSessionCursor(-1);
    expect(workspace.sessionsCursor).toBe(0);
  });
});

describe("server record → SessionRecord mapping", () => {
  test("maps defensively with unknown drift", () => {
    const record = mapSessionRecord({
      id: "abc123",
      name: "Resumed",
      model: "gpt-oss",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-01T12:00:00Z",
      pinned: true,
      archived_at: "",
      extra_unknown: 3,
    });
    expect(record?.id).toBe("abc123");
    expect(record?.pinned).toBe(true);
  });

  test("records without an id are dropped", () => {
    expect(mapSessionRecord({ name: "noid" })).toBeNull();
    expect(mapSessionRecord(null)).toBeNull();
  });
});

describe("resume replay maps snapshot messages", () => {
  test("rewrites rows to chat messages; empty-content rows dropped", () => {
    const mapped = mapSnapshotMessages([
      { role: "user", content: "hello", timestamp: "2026-09-01T10:00:00Z" },
      { role: "assistant", content: "hi back", timestamp: "2026-09-01T10:00:05Z" },
      { role: "tool", content: "internal log", timestamp: "" },
      { role: "system", content: "   ", timestamp: "2026-09-01T10:00:06Z" },
      { role: "user", content: "", timestamp: "" },
      null,
      "junk",
    ]);
    expect(mapped).toHaveLength(3);
    expect(mapped[0]!.role).toBe("user");
    expect(mapped[1]!.role).toBe("assistant");
    expect(mapped[2]!.role).toBe("system"); // unknown server roles → system
  });

  test("non-arrays map to empty", () => {
    expect(mapSnapshotMessages(undefined)).toEqual([]);
    expect(mapSnapshotMessages("junk")).toEqual([]);
  });
});

describe("export front-matter", () => {
  test("carries session metadata", () => {
    const fm = sessionExportFrontMatter({
      sessionId: "abc123def456",
      project: "qd-crispr",
      model: "gpt-oss",
      costUsd: 0.12345,
      exportedAt: "2026-09-02T00:00:00Z",
    });
    expect(fm).toContain("session_id: abc123def456");
    expect(fm).toContain("project: qd-crispr");
    expect(fm).toContain("model: gpt-oss");
    expect(fm).toContain("cost_usd: 0.1235");
    expect(fm.startsWith("---\n")).toBe(true);
  });
});
