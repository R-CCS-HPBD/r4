/**
 * tool_blocked must be visible IN CHAT, not only in the activity console
 * (2026-10-06 report: "tool blocked, then the agent stops silently" — from
 * the chat view nothing at all followed a policy denial).
 */

import { describe, expect, test, beforeEach } from "bun:test";
import {
  onBridgeEvent,
  resetCompanionRuntimeBridgeForTests,
} from "../src/bridge/companion-runtime-bridge";
import { resetWorkspace, workspace } from "../src/stores";

beforeEach(() => {
  resetWorkspace();
  resetCompanionRuntimeBridgeForTests();
});

describe("tool_blocked chat visibility", () => {
  test("policy denial posts a chat system marker naming the tool", () => {
    onBridgeEvent({
      type: "tool_blocked",
      message: "Tool shell_exec blocked by policy",
    });
    const notices = workspace.chatMessages.filter((m) => m.role === "system");
    expect(notices).toHaveLength(1);
    expect(notices[0]?.content).toContain("shell_exec blocked by policy");
    expect(notices[0]?.content).toContain("not to retry");
  });

  test("missing message still names the tool from the payload", () => {
    onBridgeEvent({ type: "tool_blocked", data: { name: "file_write" } });
    const notices = workspace.chatMessages.filter((m) => m.role === "system");
    expect(notices).toHaveLength(1);
    expect(notices[0]?.content).toContain("file_write");
  });

  test("plain error events do NOT spam the chat", () => {
    onBridgeEvent({ type: "error", message: "Error executing read_file: boom" });
    expect(workspace.chatMessages.filter((m) => m.role === "system")).toHaveLength(0);
  });
});
