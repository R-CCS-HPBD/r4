/**
 * TUI audit r2 #11/#62 — executing an accepted plan (a / m on the plan
 * card) runs a whole agent turn through `confirm_plan`; its streamed reply
 * must reach the transcript like a chat turn's, with the turn in flight
 * meanwhile (streaming state, the card hidden so a second a/m cannot send
 * a second confirm_plan).
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { respondPlan } from "../src/bridge/plan-bridge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";

type Listener = (payload: unknown) => void;

function fakeBridge() {
  const listeners = new Set<Listener>();
  const calls: Array<{ method: string; params: unknown }> = [];
  let resolvePlan: (value: unknown) => void = () => {};
  return {
    alive: true,
    calls,
    on(_channel: string, fn: Listener) {
      listeners.add(fn);
    },
    off(_channel: string, fn: Listener) {
      listeners.delete(fn);
    },
    emit(payload: unknown) {
      for (const fn of listeners) fn(payload);
    },
    async ensureStarted() {},
    abortInFlightSend() {},
    cancelTurn() {},
    respondApproval() {},
    request(method: string, params: unknown) {
      calls.push({ method, params });
      if (method === "confirm_plan") return new Promise((resolve) => (resolvePlan = resolve));
      return Promise.resolve({});
    },
    resolvePlan: (value: unknown) => resolvePlan(value),
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
  actions.resetWorkspace();
  resetTabsRegistryForTests();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
  setCompanionBridgeForTests(null);
  actions.resetWorkspace();
  resetTabsRegistryForTests();
});

describe("r2 #11/#62 — plan execution reply reaches the transcript", () => {
  test("a (execute_auto) streams the execution reply into chat as an in-flight turn", async () => {
    const bridge = fakeBridge();
    setCompanionBridgeForTests(bridge as never);
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.setInteractionModeState({ interactionMode: "plan", planAwaitingConfirmation: true });

    const done = respondPlan("execute_auto");
    await sleep(10);
    expect(bridge.calls.map((c) => c.method)).toContain("confirm_plan");
    expect(bridge.calls.find((c) => c.method === "confirm_plan")?.params).toEqual({ action: "execute_auto" });
    // The turn is in flight and the card is gone (no second confirm_plan).
    expect(workspace.chatStreaming).toBe(true);
    expect(workspace.planAwaitingConfirmation).toBe(false);

    bridge.emit({ type: "response_start" });
    bridge.emit({ type: "response_chunk", message: "Step 1 done. " });
    bridge.emit({ type: "response_chunk", message: "All steps executed." });
    await sleep(10);
    bridge.resolvePlan({
      response: "Step 1 done. All steps executed.",
      state: { session: { id: "sess-1", interaction_mode: "plan", plan_awaiting_confirmation: false } },
    });
    expect(await done).toBe(true);

    expect(workspace.chatStreaming).toBe(false);
    const replies = workspace.chatMessages.filter((m) => m.role === "assistant");
    const last = replies[replies.length - 1];
    expect(last?.content).toBe("Step 1 done. All steps executed.");
    expect(last?.streaming).toBe(false);
  });

  test("a reply that never streamed falls back to the RPC's response text", async () => {
    const bridge = fakeBridge();
    setCompanionBridgeForTests(bridge as never);
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.setInteractionModeState({ planAwaitingConfirmation: true });

    const done = respondPlan("execute_ask");
    await sleep(10);
    bridge.resolvePlan({ response: "Plan finished: 3 files written.", state: {} });
    await done;
    const last = workspace.chatMessages[workspace.chatMessages.length - 1];
    expect(last?.role).toBe("assistant");
    expect(last?.content).toBe("Plan finished: 3 files written.");
  });
});
