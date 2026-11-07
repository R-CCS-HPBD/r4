/**
 * K1.6 `/save` (knowledge plan 2026-10-24): keep a chat turn in project
 * knowledge, verbatim. Before K1.6 v3 forwarded every `/save` to the
 * companion's transcript writer: `/save 2` wrote a file named "2", bare
 * `/save` was a usage error, and no message action kept an answer.
 *
 * The bridge verb `knowledge.save` reads the turn's text from the session
 * (r4 tests/test_turn_save_k16.py); here: which lines reach it, with which
 * turn and side, and that a transcript path still goes to the companion.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import {
  PUBLISH_CONFIRM_WINDOW_MS,
  SAVE_FINAL_ANSWER_ONLY,
  SAVE_WHILE_RUNNING,
  isTurnSave,
  publishConfirmNotice,
  saveMessageAt,
  setPublishConfirmedForTests,
  turnOfMessage,
} from "../src/commands/save-command";
import { setDemoMode } from "../src/lib/console-mode";
import { setKnowledgeRefreshDelaysForTests } from "../src/hooks/project-refresh";
import { resetWorkspace, setWorkspace, workspace } from "../src/stores";
import type { ChatMessage } from "../src/types";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];

function recordBridge(): void {
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "list_slash_commands") {
        return { commands: [{ name: "save", help: "Keep a turn / write the transcript", category: "export" }] };
      }
      if (method === "knowledge.save") {
        return { turn_id: "t2", tier: "proposed", line: "saved t2 (answer) as proposed" };
      }
      return { handled: true, output: [] };
    },
  } as never);
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 10));

beforeEach(() => {
  setKnowledgeRefreshDelaysForTests([]);
  resetWorkspace();
  setDemoMode(false);
  resetSlashCatalogCache();
  calls.length = 0;
  recordBridge();
  // The publish confirm (L3) has its own block below; the rest start confirmed.
  setPublishConfirmedForTests(true);
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  resetSlashCatalogCache();
  setPublishConfirmedForTests(false);
});

describe("/save grammar", () => {
  it("tells a turn save from a transcript path", () => {
    for (const args of ["", "2", "t2", "T7 mine", "last --vouch", "--title x"]) {
      expect(isTurnSave(args)).toBe(true);
    }
    for (const args of ["notes.md", "out/session.md", "transcript"]) {
      expect(isTurnSave(args)).toBe(false);
    }
  });
});

describe("/save dispatch", () => {
  it("/save 2 --vouch reaches knowledge.save with the line", async () => {
    const result = await dispatchSlashCommand("/save 2 --vouch");
    expect(result).toEqual({ kind: "handled" });
    expect(calls.filter((c) => c.method === "knowledge.save")).toEqual([
      { method: "knowledge.save", params: { args: "2 --vouch" } },
    ]);
    expect(calls.some((c) => c.method === "dispatch_slash_command")).toBe(false);
    const notices = workspace.chatMessages.filter((m) => m.role === "system");
    expect(notices[notices.length - 1]?.content).toBe("saved t2 (answer) as proposed");
  });

  it("bare /save keeps the last answer", async () => {
    await dispatchSlashCommand("/save");
    expect(calls.find((c) => c.method === "knowledge.save")?.params).toEqual({ args: "" });
  });

  it("/save <path.md> still goes to the companion's transcript writer", async () => {
    await dispatchSlashCommand("/save notes.md");
    expect(calls.some((c) => c.method === "knowledge.save")).toBe(false);
    const sent = calls.filter((c) => c.method === "dispatch_slash_command").map((c) => c.params.command);
    expect(sent).toEqual(["/save notes.md"]);
  });
});

describe("message action", () => {
  const rows: ChatMessage[] = [
    { id: "u1", role: "user", content: "How much CO2?", timestamp: "", turnId: "t2" },
    { id: "a1", role: "assistant", content: "0.8 mmol/g", timestamp: "" },
    { id: "s1", role: "system", content: "notice", timestamp: "" },
  ] as ChatMessage[];

  it("a reply belongs to the turn its user message opened", () => {
    expect(turnOfMessage(rows, 1)).toBe("t2");
    expect(turnOfMessage(rows, 0)).toBe("t2");
  });

  it("s on an answer saves the answer; on your message, your message", async () => {
    expect(saveMessageAt(rows, 1)).toBe(true);
    expect(saveMessageAt(rows, 0)).toBe(true);
    await flush();
    expect(calls.filter((c) => c.method === "knowledge.save").map((c) => c.params)).toEqual([
      { turn: "t2", part: "answer" },
      { turn: "t2", part: "prompt" },
    ]);
  });

  it("a notice row is no chat turn", () => {
    expect(saveMessageAt(rows, 2)).toBe(false);
    expect(saveMessageAt([], 0)).toBe(false);
  });
});

// Review of K1 part B (2026-10-24).
describe("review fixes", () => {
  const lastNotice = () => {
    const notices = workspace.chatMessages.filter((m) => m.role === "system");
    return notices[notices.length - 1]?.content;
  };

  it("a save while a turn runs is refused, not queued behind it", async () => {
    setWorkspace("chatStreaming", true);
    await dispatchSlashCommand("/save");
    const rows = [{ id: "u1", role: "user", content: "q", timestamp: "", turnId: "t2" }] as ChatMessage[];
    expect(saveMessageAt(rows, 0)).toBe(true);
    await flush();
    expect(calls.some((c) => c.method === "knowledge.save")).toBe(false);
    expect(lastNotice()).toBe(SAVE_WHILE_RUNNING);
  });

  it("a reply under a user message with no turn id is no turn (never the previous one)", () => {
    const rows = [
      { id: "u1", role: "user", content: "q1", timestamp: "", turnId: "t1" },
      { id: "a1", role: "assistant", content: "a1", timestamp: "" },
      { id: "u2", role: "user", content: "q2 (failed)", timestamp: "" },
      { id: "a2", role: "assistant", content: "error reply", timestamp: "" },
    ] as ChatMessage[];
    expect(turnOfMessage(rows, 3)).toBeUndefined();
    expect(saveMessageAt(rows, 3)).toBe(false);
    expect(turnOfMessage(rows, 1)).toBe("t1");
  });

  it("s on an interim reply says the turn's final answer is what saves", async () => {
    const rows = [
      { id: "u1", role: "user", content: "q", timestamp: "", turnId: "t2" },
      { id: "a1", role: "assistant", content: "Let me search.", timestamp: "" },
      { id: "a2", role: "assistant", content: "0.8 mmol/g", timestamp: "" },
    ] as ChatMessage[];
    expect(saveMessageAt(rows, 1)).toBe(true);
    await flush();
    expect(calls.some((c) => c.method === "knowledge.save")).toBe(false);
    expect(lastNotice()).toBe(SAVE_FINAL_ANSWER_ONLY);
    expect(saveMessageAt(rows, 2)).toBe(true);
    await flush();
    expect(calls.filter((c) => c.method === "knowledge.save").map((c) => c.params)).toEqual([
      { turn: "t2", part: "answer" },
    ]);
  });
});

// L3 (maintainer, 2026-10-25): the first save of a console run publishes to
// the shared graph only after the same request is repeated; later ones don't ask.
describe("first save of a console run asks once", () => {
  const saves = () => calls.filter((c) => c.method === "knowledge.save").map((c) => c.params);
  const lastNotice = () => {
    const notices = workspace.chatMessages.filter((m) => m.role === "system");
    return notices[notices.length - 1]?.content;
  };
  const rows = [
    { id: "u1", role: "user", content: "q", timestamp: "", turnId: "t2" },
    { id: "a1", role: "assistant", content: "0.8 mmol/g", timestamp: "" },
  ] as ChatMessage[];

  beforeEach(() => setPublishConfirmedForTests(false));

  it("the first /save only explains; repeating it saves, and the next save does not ask", async () => {
    await dispatchSlashCommand("/save 2");
    expect(saves()).toEqual([]);
    expect(lastNotice()).toBe(publishConfirmNotice("Repeat /save 2"));
    await dispatchSlashCommand("/save 2");
    await dispatchSlashCommand("/save 3 mine");
    expect(saves()).toEqual([{ args: "2" }, { args: "3 mine" }]);
  });

  it("s asks once, then a second s on the same message saves", async () => {
    expect(saveMessageAt(rows, 1)).toBe(true);
    await flush();
    expect(saves()).toEqual([]);
    expect(lastNotice()).toBe(publishConfirmNotice("Press s again"));
    expect(saveMessageAt(rows, 1)).toBe(true);
    await flush();
    expect(saves()).toEqual([{ turn: "t2", part: "answer" }]);
  });

  it("a different request does not count as the confirm", async () => {
    expect(saveMessageAt(rows, 1)).toBe(true);
    expect(saveMessageAt(rows, 0)).toBe(true);
    await dispatchSlashCommand("/save 2");
    await flush();
    expect(saves()).toEqual([]);
  });

  it("a repeat after the window asks again", async () => {
    const realNow = Date.now;
    let now = 1_000_000;
    Date.now = () => now;
    try {
      await dispatchSlashCommand("/save");
      now += PUBLISH_CONFIRM_WINDOW_MS + 1;
      await dispatchSlashCommand("/save");
      expect(saves()).toEqual([]);
      now += 1_000;
      await dispatchSlashCommand("/save");
      expect(saves()).toEqual([{ args: "" }]);
    } finally {
      Date.now = realNow;
    }
  });
});

describe("the Knowledge pane refreshes after a save", () => {
  function countPolls(): { polls: string[] } {
    const seen = { polls: [] as string[] };
    setWorkspace("project", { id: "P1", name: "P1", description: "", status: "active", goalIds: [] } as never);
    setWorkspace("dataSource", {
      name: "fake",
      pollProjectKnowledge: async (id: string) => {
        seen.polls.push(id);
        return { knowledge: [], tasks: [], ingestion: null, coverage: null, readiness: null, plan: null, loop: null, sessions: null };
      },
    } as never);
    return seen;
  }

  it("/save refreshes at once (and as the ingest lands)", async () => {
    const seen = countPolls();
    await dispatchSlashCommand("/save 2");
    await flush();
    expect(seen.polls).toEqual(["P1"]);
  });
});
