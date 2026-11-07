/**
 * RS-1 round 4, R2: an answer to a remote question that closed while the
 * user switched tabs. `setPendingChatDraft` is global and the ACTIVE
 * composer takes it, so the answer typed on tab A landed in tab B's
 * composer (Enter then sent A's answer as a prompt to B's session) while A
 * said "your answer is back in the composer". The answer now stays a
 * notice on A.
 */
import { afterEach, beforeEach, expect, test } from "bun:test";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { answerRemoteQuestion, resetRemoteQuestionsForTests } from "../src/bridge/venue-bridge";
import { setDemoMode } from "../src/lib/console-mode";
import { actions, workspace } from "../src/stores";
import { getRuntime, resetTabsRegistryForTests } from "../src/tabs/registry";

beforeEach(() => {
  setDemoMode(false);
  actions.resetWorkspace();
  resetTabsRegistryForTests();
  resetRemoteQuestionsForTests();
});
afterEach(() => {
  setCompanionBridgeForTests(null);
  resetTabsRegistryForTests();
  resetRemoteQuestionsForTests();
});

function closedQuestionBridge() {
  const gates: Array<() => void> = [];
  const bridge = {
    ensureStarted: async () => {},
    on: () => {},
    off: () => {},
    request: async () => {
      await new Promise<void>((resolve) => gates.push(resolve));
      throw new Error("venue.question_closed: question q-1 is expired");
    },
  };
  return { bridge, release: () => gates.splice(0).forEach((g) => g()) };
}

const texts = (tabId: string) =>
  getRuntime(tabId).tabStore!.state.chatMessages.map((m) => String(m.content ?? ""));

test("R4-R2: a closed question's answer never lands in another tab's composer", async () => {
  const tabA = workspace.activeChatTabId;
  const tabB = actions.tabCreate({ sessionId: "sess-B", title: "B" })!;
  actions.tabActivate(tabA);
  const fake = closedQuestionBridge();
  setCompanionBridgeForTests(fake.bridge as never);
  const seq = workspace.pendingChatDraftSeq;
  const pending = answerRemoteQuestion("q-1", "use dataset B");
  await Bun.sleep(5);
  actions.tabActivate(tabB); // B's composer is empty
  fake.release();
  expect(await pending).toBe(false);
  await Bun.sleep(5);
  expect(workspace.pendingChatDraftSeq).toBe(seq);
  expect(texts(tabA).some((t) => t.includes("already closed") && t.includes("use dataset B"))).toBe(true);
  expect(texts(tabB).some((t) => t.includes("use dataset B"))).toBe(false);
});

test("R4-R2: on the same tab with an empty composer the answer still returns to it", async () => {
  const fake = closedQuestionBridge();
  setCompanionBridgeForTests(fake.bridge as never);
  const seq = workspace.pendingChatDraftSeq;
  const pending = answerRemoteQuestion("q-1", "use dataset B");
  await Bun.sleep(5);
  fake.release();
  expect(await pending).toBe(false);
  await Bun.sleep(5);
  expect(workspace.pendingChatDraft).toBe("use dataset B");
  expect(workspace.pendingChatDraftSeq).toBe(seq + 1);
});
