/**
 * Plan H-7 stage 5: `getCompanionBridge(tabId?)` resolves the named tab's
 * bridge (same instance as `bridgeForTab`), and no-argument keeps meaning
 * "the tab the user is looking at" (active reading).
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, workspace } from "../src/stores";
import { getCompanionBridge } from "../src/bridge/jsonl-client";
import {
  bridgeForTab,
  registerBridgeAttacher,
  resetTabsRegistryForTests,
} from "../src/tabs/registry";

describe("getCompanionBridge(tabId?)", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    resetTabsRegistryForTests();
  });

  it("answers the named tab's bridge, distinct per tab", () => {
    registerBridgeAttacher(() => undefined);
    const first = workspace.chatTabs[0]!.id;
    const second = actions.tabCreate({})!;
    const bridgeA = getCompanionBridge(first);
    const bridgeB = getCompanionBridge(second);
    expect(bridgeA).toBe(bridgeForTab(first));
    expect(bridgeB).toBe(bridgeForTab(second));
    expect(bridgeA).not.toBe(bridgeB);
  });

  it("no argument still means the active tab", () => {
    registerBridgeAttacher(() => undefined);
    const first = workspace.chatTabs[0]!.id;
    expect(getCompanionBridge()).toBe(bridgeForTab(first));
    const second = actions.tabCreate({})!;
    expect(getCompanionBridge()).toBe(bridgeForTab(second));
    actions.tabActivate(first);
    expect(getCompanionBridge()).toBe(bridgeForTab(first));
  });
});
