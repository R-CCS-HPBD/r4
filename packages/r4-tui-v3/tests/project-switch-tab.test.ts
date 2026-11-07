/**
 * `/project switch` lands on the tab that typed it (stabilization 2026-10-21).
 * The intercept flushed `chatMessages` and patched project state on the
 * ACTIVE tab after two bridge awaits: switching tabs mid-request wiped the
 * OTHER tab's transcript. Same gated per-tab bridge harness as
 * tab-async-results-w3.test.ts.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { getRuntime, registerBridgeAttacher, resetTabsRegistryForTests } from "../src/tabs/registry";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { handleWaveIntercepts } from "../src/commands/wave-intercepts";

function gatedBridges(reply: (method: string) => unknown) {
  const gates: Array<() => void> = [];
  registerBridgeAttacher(() => undefined);
  setCompanionBridgeForTests(null);
  for (const tab of workspace.chatTabs) {
    getRuntime(tab.id).bridge = {
      request: async (method: string) => {
        await new Promise<void>((resolve) => gates.push(resolve));
        return reply(method);
      },
    } as never;
  }
  return { release: () => gates.splice(0).forEach((g) => g()) };
}

const texts = (tabId: string) => getRuntime(tabId).tabStore!.state.chatMessages.map((m) => m.content);

beforeEach(() => {
  actions.resetWorkspace();
  resetTabsRegistryForTests();
  setWorkspace("demoMode", false);
});
afterEach(() => resetTabsRegistryForTests());

describe("/project switch after a tab switch", () => {
  it("leaves the other tab's transcript alone and reports on the switching tab", async () => {
    const tabA = workspace.activeChatTabId;
    actions.setSessionId("sess-A");
    const tabB = actions.tabCreate({ sessionId: "sess-B", title: "B" })!;
    actions.sendSystemNotice("B keeps this line");
    actions.tabActivate(tabA);
    const fake = gatedBridges((method) =>
      method === "get_state" ? { session: { id: "sess-A" }, project: { id: "p2" }, messages: [] } : {},
    );

    let done = false;
    const pending = handleWaveIntercepts({ name: "project", args: "switch p2" }).finally(() => {
      done = true;
    });
    await Bun.sleep(10);
    actions.tabActivate(tabB);
    for (let i = 0; i < 50 && !done; i += 1) {
      fake.release();
      await Bun.sleep(10);
    }
    await pending;

    expect(texts(tabB)).toContain("B keeps this line");
    expect(texts(tabB).some((t) => t.startsWith("project →"))).toBe(false);
    expect(texts(tabA).some((t) => t.startsWith("project → p2"))).toBe(true);
  });
});
