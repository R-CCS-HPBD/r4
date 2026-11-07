/**
 * Wave C slice B — plan card verbs + plan-mode state semantics.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { planVerbToAction } from "../src/bridge/plan-bridge";
import { setWorkspace, workspace } from "../src/stores";

describe("planVerbToAction", () => {
  test("keys map to server confirm actions", () => {
    expect(planVerbToAction("a")).toBe("execute_auto");
    expect(planVerbToAction("m")).toBe("execute_ask");
    expect(planVerbToAction("n")).toBe("keep_planning");
  });
  test("unrelated keys don't consume", () => {
    expect(planVerbToAction("x")).toBeNull();
    expect(planVerbToAction("escape")).toBeNull(); // Esc handled separately
    expect(planVerbToAction("y")).toBeNull(); // y is the approval key, not a plan key
  });
});

describe("plan awaiting state", () => {
  beforeEach(() => {
    setWorkspace("planAwaitingConfirmation", false);
    setWorkspace("planPendingRequest", "");
  });

  test("pending request arrives non-empty when awaiting", () => {
    // Contract: TUI never renders the card for an empty queue, so no compound
    // decision renders accidentally.
    setWorkspace("planAwaitingConfirmation", true);
    setWorkspace("planPendingRequest", "refactor auth");
    expect(workspace.planAwaitingConfirmation).toBe(true);
    expect(workspace.planPendingRequest).toBe("refactor auth");
  });
});
