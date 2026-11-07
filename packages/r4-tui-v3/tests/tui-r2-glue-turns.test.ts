/**
 * TUI audit r2 (2026-10-17), glue lane — the TS side of the python lane's
 * protocol changes.
 *
 * #17       send_message's `turn_id` tags the user row (snapshot rows carry
 *           metadata.turn_id); edit-and-resend cuts the companion history
 *           with `session.truncate {turn_id}` before slicing and sending,
 *           and refuses (nothing cut, nothing sent) when it cannot.
 * followup  dispatch_slash_command `follow_up_messages` are sent as chat
 *           turns and `dropped_turn_ids` remove those turns' rows.
 * #20       /compact and the bridge's context_compact event share one line.
 * #28       a slash RPC failure keeps the server's recovery hint.
 * #18       export metadata: server model/project over the session row's cost.
 * #21       response_done (now surfaced) never repeats a streamed reply.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import type { ChatMessage } from "../src/types";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { BridgeRequestError, setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { exportSession, mapSnapshotMessages } from "../src/bridge/session-bridge";
import { onBridgeEvent, resetCompanionRuntimeBridgeForTests } from "../src/bridge/companion-runtime-bridge";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { setDemoMode } from "../src/lib/console-mode";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

type Call = [string, Record<string, unknown>];

/** Answers every bridge RPC from a table; records the calls in order. */
function fakeBridge(answers: Record<string, (params: Record<string, unknown>) => unknown>) {
  const calls: Call[] = [];
  return {
    calls,
    alive: true,
    turnInFlight: false,
    ensureStarted: async () => undefined,
    abortInFlightSend: () => undefined,
    cancelTurn: () => undefined,
    on: () => undefined,
    off: () => undefined,
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push([method, params]);
      const answer = answers[method];
      if (!answer) return {};
      return answer(params);
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
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  setCompanionBridgeForTests(null);
  resetWorkspace();
  resetTabsRegistryForTests();
});

function useCompanion(bridge: ReturnType<typeof fakeBridge>): void {
  setCompanionBridgeForTests(bridge as never);
  actions.setChatBackend(new CompanionChatBackend(bridge as never));
}

const sentTexts = (bridge: ReturnType<typeof fakeBridge>) =>
  bridge.calls.filter(([m]) => m === "send_message").map(([, p]) => String(p.text));

describe("r2 #17 — the user row carries its session turn id", () => {
  test("send_message's turn_id tags the row the message opened", async () => {
    const bridge = fakeBridge({ send_message: () => ({ response: "hi", turn_id: "t7", state: {} }) });
    useCompanion(bridge);
    actions.sendChatMessage("hello");
    await sleep(20);
    const user = workspace.chatMessages.find((m) => m.role === "user" && m.content === "hello");
    expect(user?.turnId).toBe("t7");
  });

  test("snapshot user rows keep metadata.turn_id", () => {
    const rows = mapSnapshotMessages([
      { role: "user", content: "q", metadata: { turn_id: "t3" } },
      { role: "assistant", content: "a", metadata: { turn_id: "t3" } },
    ]);
    expect(rows[0]?.turnId).toBe("t3");
    expect(rows[1]?.turnId).toBeUndefined();
  });
});

describe("r2 #17 — edit-and-resend cuts the companion history first", () => {
  const seed = (turnId?: string) => {
    setWorkspace("chatMessages", [
      row("u1", "user", "first", turnId),
      row("a1", "assistant", "reply one"),
      row("u2", "user", "second", turnId ? "t2" : undefined),
      row("a2", "assistant", "reply two"),
    ]);
    expect(actions.beginEditResend(0)).toBe(true);
  };

  test("session.truncate by the row's turn id, then slice and send", async () => {
    const bridge = fakeBridge({
      "session.truncate": () => ({ ok: true, removed: 4, messages: 0, state: {} }),
      send_message: () => ({ response: "new reply", turn_id: "t1", state: {} }),
    });
    useCompanion(bridge);
    seed("t1");
    await actions.submitChatText("first, edited");
    await sleep(20);
    const methods = bridge.calls.map(([m]) => m).filter((m) => m !== "set_model");
    expect(methods.slice(0, 2)).toEqual(["session.truncate", "send_message"]);
    expect(bridge.calls.find(([m]) => m === "session.truncate")?.[1]).toEqual({ turn_id: "t1" });
    expect(workspace.chatMessages.some((m) => m.content === "reply two")).toBe(false);
    expect(workspace.chatMessages[0]?.content).toBe("first, edited");
    expect(workspace.editResendFromIndex).toBeNull();
  });

  test("a row without a turn id refuses: nothing cut, nothing sent", async () => {
    const bridge = fakeBridge({});
    useCompanion(bridge);
    seed(undefined);
    await actions.submitChatText("edited");
    expect(bridge.calls.map(([m]) => m)).not.toContain("session.truncate");
    expect(sentTexts(bridge)).toEqual([]);
    expect(workspace.chatMessages.some((m) => m.content === "reply two")).toBe(true);
    expect(workspace.chatMessages.some((m) => m.content.includes("cannot rewind"))).toBe(true);
    // 2026-10-19: a refusal cancels the edit — the marker used to stay set
    // and dead-lock the tab into refusals on every later Enter.
    expect(workspace.editResendFromIndex).toBeNull();
    await actions.submitChatText("plain follow-up");
    await sleep(20);
    expect(sentTexts(bridge)).toEqual(["plain follow-up"]);
  });

  test("a refused cut (rewind.desync) keeps the transcript and sends nothing", async () => {
    const bridge = fakeBridge({
      "session.truncate": () => {
        throw new Error("rewind.desync: turn t1 is not in the session history (compacted or retried) — nothing was cut");
      },
    });
    useCompanion(bridge);
    seed("t1");
    await actions.submitChatText("edited");
    expect(sentTexts(bridge)).toEqual([]);
    expect(workspace.chatMessages.some((m) => m.content === "reply two")).toBe(true);
    const card = workspace.chatMessages.find((m) => m.errorCard);
    expect(card?.errorCard?.detail).toContain("rewind.desync");
  });

  test("a backend without a companion session still slices locally", async () => {
    const sent: string[] = [];
    actions.setChatBackend({ chat: async (m: string) => (sent.push(m), "ok") });
    seed(undefined);
    await actions.submitChatText("edited");
    await sleep(10);
    expect(sent).toEqual(["edited"]);
    expect(workspace.chatMessages[0]?.content).toBe("edited");
  });
});

describe("r2 followup — slash commands hand turns back", () => {
  test("follow_up_messages are sent as chat turns, in order", async () => {
    const bridge = fakeBridge({
      dispatch_slash_command: () => ({
        handled: true,
        output: [{ kind: "system", text: "starting agentic review on a.py" }],
        state: {},
        follow_up_messages: ["Perform a rigorous code review. Target: a.py.", "and a second one"],
        dropped_turn_ids: [],
      }),
      send_message: (p) => ({ response: `re: ${String(p.text)}`, state: {} }),
      // The second turn queues behind the first (the durable queue rail).
      "session.queue_remote": () => ({ applied: true, queue: [] }),
    });
    useCompanion(bridge);
    const result = await dispatchSlashCommand("/code review a.py");
    expect(result.kind).toBe("handled");
    await sleep(60);
    expect(sentTexts(bridge)).toEqual(["Perform a rigorous code review. Target: a.py.", "and a second one"]);
    const users = workspace.chatMessages.filter((m) => m.role === "user").map((m) => m.content);
    expect(users).toEqual(["Perform a rigorous code review. Target: a.py.", "and a second one"]);
  });

  test("/retry drops the cut turn's rows and resends its prompt once", async () => {
    const bridge = fakeBridge({
      list_slash_commands: () => ({ commands: [{ name: "retry", help: "Retry", takesArgs: false }] }),
      dispatch_slash_command: () => ({
        handled: true,
        output: [{ kind: "system", text: "retrying: what is x" }],
        state: {},
        follow_up_messages: ["[Project context] Active project: P1.\n\nwhat is x"],
        dropped_turn_ids: ["t4"],
      }),
      send_message: () => ({ response: "x is y", turn_id: "t4", state: {} }),
    });
    useCompanion(bridge);
    setWorkspace("chatMessages", [
      row("u0", "user", "earlier", "t3"),
      row("a0", "assistant", "earlier answer"),
      row("u1", "user", "what is x", "t4"),
      row("a1", "assistant", "old answer"),
      row("u2", "user", "/retry"),
    ]);
    await dispatchSlashCommand("/retry");
    await sleep(40);
    const contents = workspace.chatMessages.map((m) => m.content);
    expect(contents).not.toContain("old answer");
    expect(contents.slice(0, 3)).toEqual(["earlier", "earlier answer", "/retry"]);
    expect(sentTexts(bridge)).toEqual(["what is x"]);
    const resent = workspace.chatMessages.filter((m) => m.role === "user" && m.content === "what is x");
    expect(resent).toHaveLength(1);
    expect(resent[0]?.turnId).toBe("t4");
  });

  test("a failed slash RPC keeps the server's recovery hint", async () => {
    const bridge = fakeBridge({
      dispatch_slash_command: () => {
        throw new BridgeRequestError("401 Unauthorized", "run /login <JWT>", "AuthError");
      },
    });
    useCompanion(bridge);
    const result = await dispatchSlashCommand("/code status");
    expect(result.kind).toBe("error");
    expect(result.kind === "error" && result.message).toContain("→ run /login <JWT>");
  });
});

describe("r2 #20 — /compact shows one compacted line", () => {
  test("the bridge event and the /compact reply upsert the same notice", async () => {
    const bridge = fakeBridge({
      "session.compact": () => {
        onBridgeEvent({ type: "context_compact", message: "Conversation compacted.", data: { kept_messages: 4 } });
        return { changed: true, from: 20, to: 6, keep_recent: 4 };
      },
    });
    useCompanion(bridge);
    expect((await dispatchSlashCommand("/compact")).kind).toBe("handled");
    const lines = workspace.chatMessages.filter((m) => /compacted/.test(m.content));
    expect(lines).toHaveLength(1);
    expect(lines[0]?.content).toContain("20 → 6");
  });
});

describe("r2 #18 — export metadata merges field by field", () => {
  test("server model/project with the session row's cost", async () => {
    const cwd = process.cwd();
    const dir = mkdtempSync(join(tmpdir(), "tui-r2-glue-export-"));
    process.chdir(dir);
    try {
      setCompanionBridgeForTests({
        request: async () => ({ markdown: "# body\n", model: "server-model", project: "OLD_PROJ" }),
      } as never);
      setWorkspace("sessionId", "live-session");
      setWorkspace("sessionsList", [
        { id: "older-session", name: "old", model: "row-model", created_at: "", updated_at: "", cost_usd: 0.25 },
      ] as never);
      const file = await exportSession("older-session");
      const text = readFileSync(join(dir, file!), "utf8");
      expect(text).toContain("model: server-model");
      expect(text).toContain("project: OLD_PROJ");
      expect(text).toContain("cost_usd: 0.2500");
    } finally {
      process.chdir(cwd);
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("r2 #21 — response_done renders only an un-streamed remainder", () => {
  /** Bridge whose send_message streams events, then answers. */
  function streamingBridge(events: Array<Record<string, unknown>>) {
    const listeners: Array<(e: Record<string, unknown>) => void> = [];
    return {
      alive: true,
      turnInFlight: false,
      ensureStarted: async () => undefined,
      abortInFlightSend: () => undefined,
      cancelTurn: () => undefined,
      on: (_c: string, fn: (e: Record<string, unknown>) => void) => void listeners.push(fn),
      off: () => undefined,
      request: async (method: string) => {
        if (method !== "send_message") return {};
        for (const event of events) for (const fn of listeners) fn(event);
        return { response: "", state: {} };
      },
    };
  }

  test("a reply streamed with a trailing newline is not repeated", async () => {
    const bridge = streamingBridge([
      { type: "response_start", message: "" },
      { type: "response_chunk", message: "The answer is 42.\n" },
      { type: "response_done", message: "The answer is 42.\n" },
    ]);
    let text = "";
    for await (const chunk of new CompanionChatBackend(bridge as never).chatStream("q")) text += chunk.delta ?? "";
    expect(text).toBe("The answer is 42.\n");
  });

  test("a closing slice that never streamed is rendered", async () => {
    const bridge = streamingBridge([
      { type: "response_start", message: "" },
      { type: "response_chunk", message: "Working on it." },
      { type: "response_start", message: "" },
      { type: "response_done", message: "Checkpoint after 3 tool rounds." },
    ]);
    let text = "";
    for await (const chunk of new CompanionChatBackend(bridge as never).chatStream("q")) text += chunk.delta ?? "";
    expect(text).toContain("Checkpoint after 3 tool rounds.");
  });
});
