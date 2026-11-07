/**
 * B5 — Structured error cards.
 * One card type (what / action / verb); latest card is targeted by Enter on
 * an empty draft (retry when retryable, else /logs) and click activation.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";

function reset() {
  setWorkspace("chatMessages", []);
  setWorkspace("lastErrorCardId", null);
  setWorkspace("paneView", null);
}

describe("sendErrorCard", () => {
  beforeEach(reset);

  test("appends a system message carrying the error card", () => {
    actions.sendErrorCard({ what: "workflow run failed", action: "needs_you", detail: "rpc timeout" });
    const msg = workspace.chatMessages.at(-1)!;
    expect(msg.role).toBe("system");
    expect(msg.errorCard?.what).toBe("workflow run failed");
    expect(msg.errorCard?.action).toBe("needs_you");
    expect(msg.errorCard?.detail).toBe("rpc timeout");
    expect(msg.errorCard?.retryable).toBe(false); // no retry fn supplied
    expect(workspace.lastErrorCardId).toBe(msg.id);
  });

  test("retry callback marks the card retryable", () => {
    actions.sendErrorCard({ what: "bridge exited", action: "dropped" }, () => {});
    expect(workspace.chatMessages.at(-1)!.errorCard?.retryable).toBe(true);
  });

  test("resolveLastErrorKey retries a retryable card once and consumes it", () => {
    let calls = 0;
    actions.sendErrorCard({ what: "rpc timeout", action: "dropped" }, () => {
      calls += 1;
    });
    expect(actions.resolveLastErrorKey()).toBe(true);
    expect(calls).toBe(1);
    expect(workspace.lastErrorCardId).toBeNull(); // one-shot
    expect(actions.resolveLastErrorKey()).toBe(false);
    expect(calls).toBe(1);
  });

  test("non-retryable card falls back to /logs view", () => {
    actions.sendErrorCard({ what: "dispatch failed", action: "needs_you" });
    expect(actions.resolveLastErrorKey()).toBe(true);
    expect(workspace.paneView).toBe("logs");
  });

  test("activateErrorCard clears state and opens logs for a fresh click", () => {
    actions.sendErrorCard({ what: "workflow cancel failed", action: "needs_you" });
    const id = workspace.lastErrorCardId!;
    // setWorkspace('paneView') cleared in beforeEach; simulate an earlier card id
    setWorkspace("lastErrorCardId", id);
    actions.activateErrorCard(id);
    expect(workspace.paneView).toBe("logs");
  });
});
