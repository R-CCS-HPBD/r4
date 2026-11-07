/**
 * Async results land on the tab that STARTED the request — the remaining
 * sites (stabilization 2026-10-21, w3-tui item 5). Shift+Tab mode rotation,
 * the plan card's keep-planning, a catalog skill toggle, the session-plan
 * fetch and every venue verb read the active tab only AFTER their await, so
 * an answer arriving after a tab switch was written into the other tab.
 *
 * Same harness as tab-async-results.test.tsx: each tab gets its own gated
 * fake bridge; the test switches tabs before releasing the answer.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { getRuntime, registerBridgeAttacher, resetTabsRegistryForTests } from "../src/tabs/registry";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { rotateInteractionMode } from "../src/lib/interaction-mode";
import { respondPlan } from "../src/bridge/plan-bridge";
import { toggleSkill } from "../src/bridge/catalog-bridge";
import { fetchSessionPlan } from "../src/lib/session-plan";
import * as venue from "../src/bridge/venue-bridge";

type Reply = (method: string, params: Record<string, unknown>) => unknown;

function gatedBridges(reply: Reply) {
  const gates: Array<() => void> = [];
  registerBridgeAttacher(() => undefined);
  setCompanionBridgeForTests(null);
  for (const tab of workspace.chatTabs) {
    getRuntime(tab.id).bridge = {
      request: async (method: string, params: Record<string, unknown>) => {
        await new Promise<void>((resolve) => gates.push(resolve));
        const out = reply(method, params ?? {});
        if (out instanceof Error) throw out;
        return out;
      },
    } as never;
  }
  return { release: () => gates.splice(0).forEach((g) => g()) };
}

const store = (tabId: string) => getRuntime(tabId).tabStore!.state;
const texts = (tabId: string) => store(tabId).chatMessages.map((m) => m.content);
const settle = () => Bun.sleep(10);

/** Tab A starts `run` with `reply` answering; the user is on B when it lands. */
async function raceFromA(reply: Reply, run: () => Promise<unknown>): Promise<{ tabA: string; tabB: string }> {
  const tabA = workspace.activeChatTabId;
  actions.setSessionId("sess-A");
  const tabB = actions.tabCreate({ sessionId: "sess-B", title: "B" })!;
  actions.tabActivate(tabA);
  const fake = gatedBridges(reply);
  const pending = run();
  await settle();
  actions.tabActivate(tabB);
  fake.release();
  await pending;
  await settle();
  return { tabA, tabB };
}

const STATE = { session: { id: "sess-A", interaction_mode: "plan" }, messages: [] };

beforeEach(() => {
  actions.resetWorkspace();
  resetTabsRegistryForTests();
  setWorkspace("demoMode", false);
});
afterEach(() => {
  venue.stopVenueStatusPolling();
  resetTabsRegistryForTests();
});

describe("mode / plan / catalog answers land on the originating tab", () => {
  it("Shift+Tab rotation answered after a switch: A gets the mode notice, B nothing", async () => {
    const { tabA, tabB } = await raceFromA(() => ({ state: STATE }), () => rotateInteractionMode());
    expect(texts(tabA).some((t) => t.startsWith("mode → plan"))).toBe(true);
    expect(texts(tabB).some((t) => t.startsWith("mode →"))).toBe(false);
    expect(store(tabB).interactionMode).not.toBe("plan");
  });

  it("keep-planning answered after a switch: the snapshot patches A, not B", async () => {
    const { tabA, tabB } = await raceFromA(() => ({ state: STATE }), () => respondPlan("keep_planning"));
    expect(store(tabA).interactionMode).toBe("plan");
    expect(store(tabB).interactionMode).not.toBe("plan");
  });

  it("a skill toggle answered after a switch: A gets the notice", async () => {
    const row = { kind: "skill", id: "sk1" } as never;
    const { tabA, tabB } = await raceFromA(() => STATE, () => toggleSkill(row));
    expect(texts(tabA)).toContain("skill on: sk1");
    expect(texts(tabB)).not.toContain("skill on: sk1");
  });

  it("the session-plan fetch answered after a switch: A's plan, B's stays empty", async () => {
    const steps = [{ id: "s1", title: "PLAN-OF-A", status: "pending" }];
    const { tabA, tabB } = await raceFromA(() => ({ steps }), () => fetchSessionPlan());
    expect(store(tabA).sessionPlan.map((s) => s.title)).toEqual(["PLAN-OF-A"]);
    expect(store(tabB).sessionPlan).toEqual([]);
  });
});

describe("venue answers land on the originating tab", () => {
  const RUNNING = { state: "running", source_names: ["s"], default_source: "s" };

  it("venue start: A gets the runtime and the notice", async () => {
    const { tabA, tabB } = await raceFromA(
      () => ({ venue: RUNNING }),
      () => venue.startRemoteSession({ source: "s", webAccess: { mode: "none", domains: [] } }),
    );
    expect(store(tabA).venueRuntime?.state).toBe("running");
    expect(store(tabB).venueRuntime).toBeNull();
    expect(texts(tabB).some((t) => t.includes("remote session"))).toBe(false);
  });

  it("venue status: A's runtime, never B's", async () => {
    const { tabA, tabB } = await raceFromA(() => ({ venue: RUNNING }), () => venue.fetchVenueStatusChecked());
    expect(store(tabA).venueRuntime?.state).toBe("running");
    expect(store(tabB).venueRuntime).toBeNull();
  });

  it("venue stop: the notice lands on A", async () => {
    const { tabA, tabB } = await raceFromA(() => ({ venue: { state: "stopped" } }), () => venue.stopRemoteSession());
    expect(texts(tabA)).toContain("remote session stopped");
    expect(texts(tabB)).not.toContain("remote session stopped");
  });

  it("source push / unpush: the receipts land on A", async () => {
    const pushed = await raceFromA(() => ({ allow_host: "h", key_fingerprint: "fp" }), () => venue.pushSource("src"));
    expect(texts(pushed.tabA).some((t) => t.startsWith("pushed 'src'"))).toBe(true);
    expect(texts(pushed.tabB).some((t) => t.startsWith("pushed"))).toBe(false);
    actions.resetWorkspace();
    resetTabsRegistryForTests();
    const unpushed = await raceFromA(() => ({ leases_revoked: 1 }), () => venue.unpushSource("src"));
    expect(texts(unpushed.tabA).some((t) => t.startsWith("unpushed 'src'"))).toBe(true);
    expect(texts(unpushed.tabB).some((t) => t.startsWith("unpushed"))).toBe(false);
  });

  it("a failed remote approval decision: the error card lands on A", async () => {
    const { tabA, tabB } = await raceFromA(
      () => new Error("decide refused"),
      () => venue.decideRemoteApproval("ap1", "once"),
    );
    expect(texts(tabA)).toContain("remote approval failed");
    expect(texts(tabB)).not.toContain("remote approval failed");
  });

  it("surfaced remote approvals open the card on A, not B", async () => {
    const approvals = [{ approval_id: "ap1", status: "pending", action: { tool: "bash" } }];
    const { tabA, tabB } = await raceFromA(() => ({ approvals }), () => venue.surfaceRemoteApprovalsChecked());
    expect(store(tabA).toolApproval?.id).toBe("remote:ap1");
    expect(store(tabB).toolApproval).toBeNull();
  });
});
