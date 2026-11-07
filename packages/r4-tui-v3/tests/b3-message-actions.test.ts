/**
 * B3 — Message actions: transcript cursor + copy/retry/edit verbs.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { osc52Sequence } from "../src/lib/clipboard";

function seedMessages() {
  setWorkspace("chatMessages", [
    { id: "a", role: "user", content: "first question", timestamp: "2026-09-02T10:00:00Z" },
    { id: "b", role: "assistant", content: "first answer", timestamp: "2026-09-02T10:00:05Z" },
    { id: "c", role: "user", content: "second question", timestamp: "2026-09-02T10:01:00Z" },
    { id: "d", role: "assistant", content: "second answer", timestamp: "2026-09-02T10:01:05Z" },
  ]);
}

describe("transcript cursor", () => {
  beforeEach(() => {
    seedMessages();
    setWorkspace("messageCursor", null);
    setWorkspace("editResendFromIndex", null);
  });

  test("enter focuses the latest message; empty transcript fails cleanly", () => {
    expect(actions.enterMessageCursor()).toBe(true);
    expect(workspace.messageCursor).toBe(3);
    setWorkspace("chatMessages", []);
    setWorkspace("messageCursor", null);
    expect(actions.enterMessageCursor()).toBe(false);
    expect(workspace.messageCursor).toBeNull();
  });

  test("movement is clamped to transcript bounds", () => {
    actions.enterMessageCursor();
    actions.moveMessageCursor(-2);
    expect(workspace.messageCursor).toBe(1);
    actions.moveMessageCursor(-99);
    expect(workspace.messageCursor).toBe(0);
    actions.moveMessageCursor(99);
    expect(workspace.messageCursor).toBe(3);
  });

  test("exit clears the cursor", () => {
    actions.enterMessageCursor();
    actions.exitMessageCursor();
    expect(workspace.messageCursor).toBeNull();
  });
});

describe("retry + edit verbs", () => {
  beforeEach(() => {
    seedMessages();
    setWorkspace("chatStreaming", false);
    setWorkspace("editResendFromIndex", null);
  });

  test("retryMessage rejects assistant rows", () => {
    expect(actions.retryMessage(1)).toBe(false);
  });

  test("beginEditResend seeds draft text + marks truncation; truncation keeps prefix only", () => {
    expect(actions.beginEditResend(2)).toBe(true);
    expect(workspace.editResendFromIndex).toBe(2);
    expect(workspace.pendingChatDraft).toBe("second question");
    expect(workspace.messageCursor).toBeNull(); // exits cursor mode
    actions.truncateTranscriptForResend();
    expect(workspace.editResendFromIndex).toBeNull();
    expect(workspace.chatMessages.map((m) => m.id)).toEqual(["a", "b"]);
  });

  test("truncateTranscriptForResend is a no-op without a marker", () => {
    actions.truncateTranscriptForResend();
    expect(workspace.chatMessages.length).toBe(4);
  });
});

describe("osc52Sequence", () => {
  test("encodes clipboard text base64 between the OSC52 markers", () => {
    const seq = osc52Sequence("hello");
    expect(seq.startsWith("\x1b]52;c;")).toBe(true);
    expect(seq.endsWith("\x07")).toBe(true);
    expect(Buffer.from(seq.slice(7, -1), "base64").toString()).toBe("hello");
  });
});
