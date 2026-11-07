/**
 * Remote (frontend-venue) chat branch: send_message → {remote, queued} → the
 * watcher polls session.venue_sync, streams partial text as deltas and
 * finishes on the adopted assistant row (wave 2026-09-21, G-RS-22).
 */
import { test, expect, beforeEach, afterEach } from "bun:test";
import { CompanionChatBackend, setRemotePollMsForTests } from "../src/adapters/companion-chat";
import type { ChatStreamChunk } from "../src/adapters/types";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";
import { setWorkspace, workspace } from "../src/stores";

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
  setRemotePollMsForTests(5);
  setWorkspace("toolApproval", null);
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
  setRemotePollMsForTests(1_500);
});

function makeBridge(syncs: Array<Record<string, unknown>>, approvals: Array<Record<string, unknown>> = []) {
  const calls: string[] = [];
  return {
    calls,
    ensureStarted: async () => {},
    on: () => {},
    off: () => {},
    request: async (method: string) => {
      calls.push(method);
      if (method === "send_message") {
        return {
          response: "",
          remote: true,
          queued: true,
          state: { messages: [], total_messages: 2, session: { model: "gpt-x" } },
        };
      }
      if (method === "session.venue_sync") {
        return syncs.length > 1 ? syncs.shift() : syncs[0];
      }
      if (method === "session.venue_approvals") return { approvals, count: approvals.length };
      return {};
    },
    abortInFlightSend: () => {},
    cancelTurn: () => {},
  };
}

async function collect(backend: CompanionChatBackend, signal?: AbortSignal): Promise<ChatStreamChunk[]> {
  const out: ChatStreamChunk[] = [];
  for await (const chunk of backend.chatStream!("hi", [], "gpt-x", signal)) out.push(chunk);
  return out;
}

test("streams partial deltas then the final assistant row", async () => {
  const bridge = makeBridge([
    { runtime: { phase: "running", partial: "hel", turn_seq: 2 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
    { runtime: { phase: "running", partial: "hello", turn_seq: 2 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
    {
      adopted: true,
      runtime: { phase: "idle", partial: "", turn_seq: 2 },
      venue_state: "running",
      lease_remaining_sec: 1200,
      state: {
        total_messages: 4,
        session: { model: "gpt-x", venue: "frontend" },
        messages: [
          { role: "user", content: "hi" },
          { role: "assistant", content: "hello world" },
        ],
      },
    },
  ]);
  const chunks = await collect(new CompanionChatBackend(bridge as never));
  const text = chunks.map((c) => c.delta ?? "").join("");
  expect(text).toBe("hello world");
  expect(chunks.some((c) => c.roundBreak)).toBe(false);
  expect(chunks.at(-1)?.done).toBe(true);
  expect(workspace.venueRuntime?.state).toBe("running");
  expect(workspace.venueRuntime?.leaseRemainingSec).toBe(1200);
  expect(bridge.calls.filter((c) => c === "session.venue_sync").length).toBe(3);
});

test("reply adoption before idle heartbeat still completes", async () => {
  const state = {
    total_messages: 4,
    messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: "finished reply" },
    ],
  };
  const bridge = makeBridge([
    { adopted: true, runtime: { phase: "running", partial: "finished reply" }, venue_state: "running", state },
    { adopted: false, runtime: { phase: "idle", partial: "" }, venue_state: "running", state },
  ]);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 500);
  try {
    const chunks = await collect(new CompanionChatBackend(bridge as never), controller.signal);
    expect(controller.signal.aborted).toBe(false);
    expect(chunks.map((chunk) => chunk.delta ?? "").join("")).toBe("finished reply");
    expect(bridge.calls.filter((method) => method === "session.venue_sync")).toHaveLength(2);
  } finally {
    clearTimeout(timeout);
  }
});

test("watcher waits for queued followups and returns the server transcript", async () => {
  const bridge = makeBridge([
    {
      adopted: true,
      runtime: { phase: "idle" },
      state: { total_messages: 4, session: { chat_queue: [{ content: "followup" }] }, messages: [{ role: "assistant", content: "first reply" }] },
    },
    {
      adopted: true,
      runtime: { phase: "idle" },
      state: { total_messages: 6, session: { chat_queue: [] }, messages: [{ role: "assistant", content: "first reply" }, { role: "assistant", content: "second reply" }] },
    },
  ]);
  const chunks = await collect(new CompanionChatBackend(bridge as never));
  expect(bridge.calls.filter((method) => method === "session.venue_sync")).toHaveLength(2);
  expect(chunks.find((chunk) => chunk.transcript)?.transcript?.map((row) => row.content))
    .toEqual(["first reply", "second reply"]);
});

test("worker error phase ends the turn with an honest failure line", async () => {
  const bridge = makeBridge([
    { runtime: { phase: "running", partial: "", turn_seq: 2 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
    { runtime: { phase: "error", partial: "", detail: "RuntimeError: upstream exploded", turn_seq: 2 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
  ]);
  const chunks = await collect(new CompanionChatBackend(bridge as never));
  const text = chunks.map((c) => c.delta ?? "").join("");
  expect(text).toContain("[remote turn failed] RuntimeError: upstream exploded");
  expect(chunks.at(-1)?.done).toBe(true);
});

test("pending remote approvals surface through the tool-approval card", async () => {
  const bridge = makeBridge(
    [
      { runtime: { phase: "awaiting_approval", partial: "", turn_seq: 2 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
      {
        adopted: true,
        runtime: { phase: "idle", partial: "", turn_seq: 2 },
        venue_state: "running",
        state: { total_messages: 4, messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "ok" }] },
      },
    ],
    [{ approval_id: "appr-9", status: "pending", action: { tool: "shell_exec", description: "ls -la" } }],
  );
  const chunks = await collect(new CompanionChatBackend(bridge as never));
  expect(chunks.at(-1)?.done).toBe(true);
  expect(workspace.toolApproval?.id).toBe("remote:appr-9");
  expect(workspace.toolApproval?.tool).toBe("shell_exec");
  setWorkspace("toolApproval", null);
});

test("abort stops watching without claiming the turn ended", async () => {
  const bridge = makeBridge([
    { runtime: { phase: "running", partial: "part", turn_seq: 2 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
  ]);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 30);
  const chunks = await collect(new CompanionChatBackend(bridge as never), controller.signal);
  expect(chunks.at(-1)?.done).toBe(true);
  expect(chunks.map((c) => c.delta ?? "").join("")).toBe("part");
});

test("tool_activity rides the watcher: venue runtime + turn-end notice details", async () => {
  const bridge = makeBridge([
    {
      runtime: {
        phase: "running",
        partial: "reading…",
        turn_seq: 3,
        tool_activity: ["▸ hpc_submit sbatch run.slurm", "▸ rag_search defect ✓ 1.2s"],
      },
      venue_state: "running",
      state: { messages: [], total_messages: 3 },
    },
    {
      adopted: true,
      runtime: { phase: "idle", partial: "", turn_seq: 3 },
      venue_state: "running",
      state: {
        total_messages: 4,
        session: { model: "gpt-x" },
        messages: [
          { role: "user", content: "hi" },
          { role: "assistant", content: "answer" },
        ],
      },
    },
  ]);
  await collect(new CompanionChatBackend(bridge as never));
  // Runtime chip carries the bounded lines verbatim.
  expect(workspace.venueRuntime?.toolActivity).toEqual([
    "▸ hpc_submit sbatch run.slurm",
    "▸ rag_search defect ✓ 1.2s",
  ]);
  // Turn-end notice is one row with the activity details.
  const notice = workspace.chatMessages.find(
    (m) => m.role === "system" && m.noticeKey === "remote-tools:3",
  );
  expect(notice?.content).toContain("2 tool actions");
  expect(workspace.noticeDetails["remote-tools:3"]?.[1]).toContain("rag_search");
});

test("older deployments (no tool_activity field) stay quiet — no notice, no rows", async () => {
  const bridge = makeBridge([
    { runtime: { phase: "running", partial: "x", turn_seq: 4 }, venue_state: "running", state: { messages: [], total_messages: 3 } },
    {
      adopted: true,
      runtime: { phase: "idle", partial: "", turn_seq: 4 },
      venue_state: "running",
      state: {
        total_messages: 4,
        messages: [
          { role: "user", content: "hi" },
          { role: "assistant", content: "done" },
        ],
      },
    },
  ]);
  await collect(new CompanionChatBackend(bridge as never));
  expect(workspace.venueRuntime?.toolActivity).toEqual([]);
  expect(
    workspace.chatMessages.some(
      (m) => m.role === "system" && m.noticeKey === "remote-tools:4",
    ),
  ).toBe(false);
});
