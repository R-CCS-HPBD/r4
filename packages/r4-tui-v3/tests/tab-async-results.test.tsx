/**
 * Async results land on the tab that STARTED the request (stabilization
 * 2026-10-21, static-sweep#3 / static-sweep#5): a session new/resume
 * or a /model pick answering after a tab switch wrote the then-active tab —
 * B's transcript wiped and its session rebound, or B badged with a
 * permission mode its bridge never got while A (which did) still read ask.
 *
 * Each tab gets its own fake bridge (the registry's per-tab slot), every
 * request parks on a gate the test releases after switching tabs.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { Show } from "solid-js";
import { testRender } from "@opentui/solid";
import { actions, setWorkspace, workspace } from "../src/stores";
import {
  getRuntime,
  registerBridgeAttacher,
  resetTabsRegistryForTests,
} from "../src/tabs/registry";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { newSession, resumeSession } from "../src/bridge/session-bridge";
import { SlashPickerOverlay } from "../src/components/slash-picker";

type Reply = (tabId: string, method: string, params: Record<string, unknown>) => unknown;

/** Install a gated fake bridge on every open tab (and on tabs created later
 *  through `bridgeFor`). Returns the call log and the gate release. */
function gatedBridges(reply: Reply) {
  const sent: string[] = [];
  const gates: Array<() => void> = [];
  registerBridgeAttacher(() => undefined);
  setCompanionBridgeForTests(null);
  const bridgeFor = (tabId: string) => {
    getRuntime(tabId).bridge = {
      request: async (method: string, params: Record<string, unknown>) => {
        sent.push(`${tabId}:${method}`);
        await new Promise<void>((resolve) => gates.push(resolve));
        return reply(tabId, method, params ?? {});
      },
    } as never;
  };
  for (const tab of workspace.chatTabs) bridgeFor(tab.id);
  return { sent, bridgeFor, release: () => gates.splice(0).forEach((g) => g()) };
}

const store = (tabId: string) => getRuntime(tabId).tabStore!.state;
const meta = (tabId: string) => workspace.chatTabs.find((t) => t.id === tabId)!;
const texts = (tabId: string) => store(tabId).chatMessages.map((m) => m.content);
const settle = () => Bun.sleep(10);

beforeEach(() => {
  actions.resetWorkspace();
  resetTabsRegistryForTests();
  setWorkspace("demoMode", false);
});
afterEach(() => resetTabsRegistryForTests());

describe("session results land on the originating tab", () => {
  it("newSession from A answered after a switch to B: B keeps its conversation", async () => {
    const tabA = workspace.activeChatTabId;
    actions.setSessionId("sess-A-own");
    const tabB = actions.tabCreate({ sessionId: "sess-B-own", title: "B" })!;
    actions.sendSystemNotice("B-OWN-CONVERSATION");
    actions.tabActivate(tabA);
    const fake = gatedBridges(() => ({ session: { id: "sess-NEW" }, messages: [] }));
    const pending = newSession("local");
    await settle();
    actions.tabActivate(tabB);
    fake.release();
    expect(await pending).toBe(true);
    expect(fake.sent[0]).toBe(`${tabA}:initialize`);
    expect(texts(tabB)).toContain("B-OWN-CONVERSATION");
    expect(meta(tabB).sessionId).toBe("sess-B-own");
    expect(meta(tabA).sessionId).toBe("sess-NEW");
    expect(texts(tabA)).toContain("new session started (local venue)");
  });

  it("resumeSession from A answered after a switch to B: A gets the session", async () => {
    const tabA = workspace.activeChatTabId;
    const tabB = actions.tabCreate({ sessionId: "sess-B-own", title: "B" })!;
    actions.sendSystemNotice("B-OWN-CONVERSATION");
    actions.tabActivate(tabA);
    const fake = gatedBridges((_tab, _method, params) => ({
      session: { id: params.session_id },
      messages: [{ role: "user", content: "PROMPT-OF-A" }],
    }));
    const pending = resumeSession("sess-AAAA1111");
    await settle();
    actions.tabActivate(tabB);
    fake.release();
    expect(await pending).toBe(true);
    expect(meta(tabB).sessionId).toBe("sess-B-own");
    expect(texts(tabB)).toContain("B-OWN-CONVERSATION");
    expect(texts(tabB)).not.toContain("PROMPT-OF-A");
    expect(meta(tabA).sessionId).toBe("sess-AAAA1111");
    expect(texts(tabA)).toContain("PROMPT-OF-A");
  });
});

describe("mode results land on the originating tab", () => {
  // Alt+M (permission cycle) retired by ADR-104 D-1; the Shift+Tab mode
  // switch's tab pinning is in tab-async-results-w3.test.ts.

  it("/model pick answered after a switch: the snapshot patches A, not B", async () => {
    const tabA = workspace.activeChatTabId;
    actions.setSessionId("sess-A");
    actions.setCurrentModel("old-A");
    const fake = gatedBridges((tab) => ({ session: { id: tab === tabA ? "sess-A" : "x" }, model: "new-model" }));
    setWorkspace("slashPicker", {
      title: "model",
      commandPrefix: "/model ",
      mode: "set_model",
      items: [{ key: "new-model", label: "new-model", modelName: "new-model" }],
    } as never);
    setWorkspace("slashPickerOpen", true);
    const setup = await testRender(
      () => (
        <Show when={workspace.slashPicker}>
          <SlashPickerOverlay />
        </Show>
      ), { width: 100, height: 20 });
    await setup.renderOnce();
    await settle();
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await settle();
    const tabB = actions.tabCreate({ sessionId: "sess-B", title: "B" })!;
    actions.setCurrentModel("old-B");
    fake.release();
    await settle();
    setup.renderer.destroy();
    expect(fake.sent[0]).toBe(`${tabA}:set_model`);
    expect(store(tabA).currentModel).toBe("new-model");
    expect(store(tabB).currentModel).toBe("old-B");
    expect(meta(tabB).sessionId).toBe("sess-B");
    expect(texts(tabB)).not.toContain("model → new-model");
  });
});
