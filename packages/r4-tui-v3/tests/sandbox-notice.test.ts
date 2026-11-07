/**
 * ADR-101 D-1, maintainer requirement: "default -> off as claude code but r4
 * tui needs to notify users and how to make this on during a session at
 * first". A local session that opens (init_state, a new session, a resume)
 * with a bridge notice shows one keyed, expandable row; a frontend session
 * or a bridge without `session.sandbox` shows none.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { activeTab, resetWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { handleInitState } from "../src/bridge/companion-runtime-bridge";
import { newSession, resumeSession } from "../src/bridge/session-bridge";
import { SANDBOX_NOTICE_KEY } from "../src/lib/sandbox";
import { OFF_NOTICE, wireSandbox } from "./helpers/sandbox-wire";

function keyedRows() {
  return workspace.chatMessages.filter((m) => m.role === "system" && m.noticeKey === SANDBOX_NOTICE_KEY);
}

function snapshot(session: Record<string, unknown>): Record<string, unknown> {
  return { workspace_root: "/work/proj", session: { venue: "local", ...session }, messages: [] };
}

function fakeBridge(answers: Record<string, unknown>): void {
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    shutdown: async () => {},
    cancelTurn: () => {},
    abortInFlightSend: () => {},
    respondAskUser: () => {},
    respondApproval: () => {},
    request: async (method: string) => answers[method] ?? {},
  } as never);
}

beforeEach(() => resetWorkspace());
afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
});

describe("init_state", () => {
  it("shows the off notice once, keyed and expandable, with how to turn it on", () => {
    handleInitState(snapshot({ sandbox: wireSandbox() }));
    const rows = keyedRows();
    expect(rows.length).toBe(1);
    expect(rows[0]!.content).toBe(`⚠ ${OFF_NOTICE}`);
    expect(rows[0]!.content).toContain("/sandbox on");
    const details = activeTab().noticeDetails[SANDBOX_NOTICE_KEY] ?? [];
    expect(details.join("\n")).toContain("/sandbox status");
    expect(details.join("\n")).toContain("backend: bwrap");
    expect(activeTab().sandbox?.mode).toBe("off");
    // a bridge restart re-sends init_state: the same row, not a second one
    handleInitState(snapshot({ sandbox: wireSandbox() }));
    expect(keyedRows().length).toBe(1);
  });

  it("the unavailable text comes from the bridge as is", () => {
    const notice = "Sandbox unavailable: bwrap not found. Shell, Python and MCP servers run with your full permissions.";
    handleInitState(snapshot({ sandbox: wireSandbox({ available: false, reason: "bwrap not found", notice }) }));
    expect(keyedRows()[0]!.content).toBe(`⚠ ${notice}`);
  });

  it("none for an on sandbox without a notice, a frontend session or an old bridge", () => {
    handleInitState(snapshot({ sandbox: wireSandbox({ mode: "on", notice: "" }) }));
    handleInitState(snapshot({ venue: "frontend", sandbox: wireSandbox({ mode: "remote", notice: "" }) }));
    handleInitState(snapshot({ venue: "frontend", sandbox: wireSandbox() }));
    handleInitState(snapshot({}));
    expect(keyedRows()).toEqual([]);
  });
});

describe("new and resumed sessions", () => {
  it("a new local session shows the notice again", async () => {
    fakeBridge({ initialize: snapshot({ sandbox: wireSandbox() }) });
    expect(await newSession("local")).toBe(true);
    expect(keyedRows().map((m) => m.content)).toEqual([`⚠ ${OFF_NOTICE}`]);
  });

  it("a resumed session shows its own notice", async () => {
    fakeBridge({ load_session: snapshot({ sandbox: wireSandbox() }) });
    expect(await resumeSession("01HRESUMED0000000000000000")).toBe(true);
    expect(keyedRows().length).toBe(1);
  });

  it("a resumed on session shows none", async () => {
    fakeBridge({ load_session: snapshot({ sandbox: wireSandbox({ mode: "on", notice: "" }) }) });
    await resumeSession("01HRESUMED0000000000000000");
    expect(keyedRows()).toEqual([]);
    expect(activeTab().sandbox?.mode).toBe("on");
  });
});
