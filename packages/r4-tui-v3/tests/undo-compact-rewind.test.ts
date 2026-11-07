/**
 * /undo, /compact and the rewind list act on chat prompts only
 * (stabilization 2026-10-21).
 *
 * slash-c#2  /undo matched its own '/undo' echo (the newest user row), cut
 *            just that, reported "draft restored (5 chars)" and left the
 *            last turn in the transcript and the model context.
 * slash-c#1  '/compact help' (Number("help") is NaN → default keep)
 *            compacted irreversibly instead of printing usage.
 * slash-a#3 / composer#10  the rewind menu listed slash echoes (the current
 *            /rewind highlighted first) and relayed shell results.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import type { ChatMessage } from "../src/types";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { resetCompanionRuntimeBridgeForTests } from "../src/bridge/companion-runtime-bridge";
import { resetSlashCatalogCache } from "../src/commands/slash";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { setDemoMode } from "../src/lib/console-mode";
import { rewindCandidates } from "../src/lib/tui-parity";

type Call = [string, Record<string, unknown>];

function fakeBridge(answers: Record<string, (params: Record<string, unknown>) => unknown>) {
  const calls: Call[] = [];
  return {
    calls,
    alive: true,
    turnInFlight: false,
    ensureStarted: async () => undefined,
    on: () => undefined,
    off: () => undefined,
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push([method, params]);
      const answer = answers[method];
      return answer ? answer(params) : {};
    },
  };
}

const row = (id: string, role: ChatMessage["role"], content: string, turnId?: string): ChatMessage => ({
  id,
  role,
  content,
  timestamp: new Date().toISOString(),
  ...(turnId ? { turnId } : {}),
});

const RELAY = "I ran `!echo hi` in the TUI's local shell — it did not run through your tools, so it is not in your tool history.";

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
  resetCompanionRuntimeBridgeForTests();
  resetSlashCatalogCache();
});
afterEach(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  setCompanionBridgeForTests(null);
  resetWorkspace();
  resetTabsRegistryForTests();
});

const notices = () => workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);

describe("slash-c#2 — /undo undoes the last chat turn, not its own echo", () => {
  test("the last prompt is cut (transcript + companion) and lands in the composer", async () => {
    const bridge = fakeBridge({ "session.truncate": () => ({ ok: true }) });
    setCompanionBridgeForTests(bridge as never);
    setWorkspace("chatMessages", [
      row("u1", "user", "turn number 4", "t4"),
      row("a1", "assistant", "reply 4"),
      row("u2", "user", "turn number 5", "t5"),
      row("a2", "assistant", "reply 5"),
    ]);
    const seq = workspace.pendingChatDraftSeq;
    await actions.dispatchSlashFromChat("/undo");
    expect(workspace.chatMessages.map((m) => m.content).slice(0, 2)).toEqual(["turn number 4", "reply 4"]);
    expect(workspace.chatMessages.some((m) => m.content === "turn number 5" || m.content === "/undo")).toBe(false);
    expect(workspace.pendingChatDraft).toBe("turn number 5");
    expect(workspace.pendingChatDraftSeq).toBeGreaterThan(seq);
    expect(bridge.calls).toContainEqual(["session.truncate", { turn_id: "t5" }]);
    expect(notices().at(-1)).toContain("(13 chars)");
  });

  test("with only slash echoes and relays, it says nothing to undo", async () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    setWorkspace("chatMessages", [row("u1", "user", "/models"), row("u2", "user", RELAY)]);
    await actions.dispatchSlashFromChat("/undo");
    expect(notices().at(-1)).toContain("nothing to undo");
    expect(workspace.chatMessages.some((m) => m.content === RELAY)).toBe(true);
  });

  test("a refused companion cut leaves the transcript alone", async () => {
    const bridge = fakeBridge({
      "session.truncate": () => {
        throw new Error("venue.remote: rewind cannot cut a remote session's context");
      },
    });
    setCompanionBridgeForTests(bridge as never);
    setWorkspace("chatMessages", [row("u1", "user", "hello", "t1"), row("a1", "assistant", "hi")]);
    const result = await actions.dispatchSlashFromChat("/undo");
    expect(result.kind).toBe("error");
    expect(workspace.chatMessages.some((m) => m.content === "hello")).toBe(true);
  });
});

describe("slash-c#1 — /compact never compacts on a non-number", () => {
  for (const line of ["/compact help", "/compact --help", "/compact zzbogus", "/compact 0"]) {
    test(`${line} prints usage and calls no RPC`, async () => {
      const bridge = fakeBridge({ "session.compact": () => ({ changed: true, from: 10, to: 7, keep_recent: 6 }) });
      setCompanionBridgeForTests(bridge as never);
      await actions.dispatchSlashFromChat(line);
      expect(bridge.calls.some(([m]) => m === "session.compact")).toBe(false);
      expect(notices().at(-1)).toContain("usage: /compact [keep]");
    });
  }

  test("/compact 4 still passes its keep count", async () => {
    const bridge = fakeBridge({ "session.compact": () => ({ changed: false, message_count: 3 }) });
    setCompanionBridgeForTests(bridge as never);
    await actions.dispatchSlashFromChat("/compact 4");
    expect(bridge.calls).toContainEqual(["session.compact", { keep_recent: 4 }]);
  });
});

describe("slash-a#3 / composer#10 — rewind lists chat prompts only", () => {
  test("slash echoes (the current /rewind included) and shell relays are not candidates", () => {
    const msgs = [
      row("u1", "user", "/models fake-a@fakesrc"),
      row("u2", "user", "hello one"),
      row("a1", "assistant", "hi"),
      row("u3", "user", RELAY),
      row("u4", "user", "hello two"),
      row("u5", "user", "/rewind"),
    ];
    expect(rewindCandidates(msgs).map((c) => c.preview)).toEqual(["hello two", "hello one"]);
  });
});
