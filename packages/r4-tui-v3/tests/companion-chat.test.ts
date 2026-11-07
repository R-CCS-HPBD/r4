/**
 * Process-view contract for the Companion bridge chat path.
 *
 * Parity target (opencode/OpenHands UI): while the agent works through
 * tools, every model round's text stays in the transcript and rounds are
 * separated by explicit `roundBreak` chunks — the store splits each round
 * into its own assistant segment so tool work pins under the text that
 * triggered it (strict invocation-time order). Intermediate LLM text must
 * never vanish from the visible chat.
 */
import { test, expect, beforeEach, afterEach } from "bun:test";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import type { ChatStreamChunk } from "../src/adapters/types";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";

// (V2 audit): the console-mode module singleton (`demoMode`/`demoVisuals`) is
// a global mutable flag shared across test files; other suites flip it on
// and nothing restores it, which replayed itself as `[Offline] …` chunks
// inside these pins whenever the full suite ran them in default file order.
// Every test in this file now resets mode explicitly like the round-segments
// suite already does. The CI job pins R4_TUI_BACKEND=offline, a second
// ambient that beats these fixtures the same way from another direction —
// this pin table's mocks must win unconditionally (env saved+restored).
let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
});

function makeBridge(
  emission: Array<[string, string?]>,
  result?: { response?: string; state?: unknown },
) {
  const fns: Array<(e: unknown) => void> = [];
  return {
    ensureStarted: async () => {},
    on: (_name: string, fn: unknown) => {
      fns.push(fn as (e: unknown) => void);
    },
    off: () => {},
    request: async () => {
      for (const [type, message] of emission) {
        for (const fn of fns) fn({ type, message });
      }
      return (
        result ?? { response: "", state: { messages: [], session: { model: "kimi-k3" } } }
      );
    },
    abortInFlightSend: () => {},
    cancelTurn: () => {},
  };
}

async function collectChunks(backend: CompanionChatBackend): Promise<ChatStreamChunk[]> {
  const chunks: ChatStreamChunk[] = [];
  for await (const chunk of backend.chatStream!("go", [], "kimi-k3")) {
    chunks.push(chunk);
  }
  return chunks;
}

function joinedText(chunks: ChatStreamChunk[]): string {
  return chunks.map((c) => c.delta ?? "").join("");
}

test("rounds emit a roundBreak between text rounds; no text is lost", async () => {
  const backend = new CompanionChatBackend(
    makeBridge([
      ["response_start"],
      ["response_chunk", "thinking: listing the folder "] as [string, string],
      ["response_start"],
      ["response_chunk", "answering: files look fine"] as [string, string],
    ]) as never,
  );
  const chunks = await collectChunks(backend);
  const joined = joinedText(chunks);

  expect(joined).toContain("thinking: listing the folder");
  expect(joined).toContain("answering: files look fine");
  expect(joined.indexOf("thinking: listing")).toBeLessThan(joined.indexOf("answering: files"));
  // Exactly one round boundary, positioned between the two rounds' deltas —
  // no synthetic "\n\n" separator is injected into the text itself.
  const breakIndexes = chunks
    .map((c, i) => (c.roundBreak ? i : -1))
    .filter((i) => i >= 0);
  expect(breakIndexes).toHaveLength(1);
  expect(joined).not.toContain("\n\n");
  const firstDeltaIdx = chunks.findIndex((c) => (c.delta ?? "").includes("thinking: listing"));
  const secondDeltaIdx = chunks.findIndex((c) => (c.delta ?? "").includes("answering: files"));
  expect(firstDeltaIdx).toBeLessThan(breakIndexes[0]!);
  expect(breakIndexes[0]!).toBeLessThan(secondDeltaIdx);
});

test("single-round stream emits no round break", async () => {
  const backend = new CompanionChatBackend(
    makeBridge([
      ["response_start"],
      ["response_chunk", "just one take"] as [string, string],
    ]) as never,
  );
  const chunks = await collectChunks(backend);
  expect(joinedText(chunks)).toBe("just one take");
  expect(chunks.some((c) => c.roundBreak)).toBe(false);
});

test("adjacent textless rounds push no stacking breaks", async () => {
  const backend = new CompanionChatBackend(
    makeBridge([
      ["response_start"],
      ["response_chunk", "only here"] as [string, string],
      ["response_start"],
      ["response_start"],
      ["response_chunk", "done now"] as [string, string],
    ]) as never,
  );
  const chunks = await collectChunks(backend);
  const joined = joinedText(chunks);
  expect(joined).toContain("only here");
  expect(joined).toContain("done now");
  // exactly one round break between the two text-bearing rounds
  expect(chunks.filter((c) => c.roundBreak)).toHaveLength(1);
});

test("streamless terminal slice (wrap-up digest) renders via response_done", async () => {
  // 2026-10-06 report: tool blocked, then a hard-roof wrap-up answered ONLY
  // with response_done — the chat showed the preamble, then nothing
  // ("agent stopped silently").
  const backend = new CompanionChatBackend(
    makeBridge(
      [
        ["response_start"],
        ["response_chunk", "I'll run ls"] as [string, string],
        ["tool_blocked", "Tool shell_exec blocked by policy"] as [string, string],
        ["response_done", "Checkpoint after 2 tool rounds — evidence so far: denied"] as [
          string,
          string,
        ],
      ],
      {
        response: "Checkpoint after 2 tool rounds — evidence so far: denied",
        state: {
          messages: [
            { role: "assistant", content: "Checkpoint after 2 tool rounds — evidence so far: denied" },
          ],
          session: { model: "kimi-k3" },
        },
      },
    ) as never,
  );
  const chunks = await collectChunks(backend);
  const joined = joinedText(chunks);
  expect(joined).toContain("I'll run ls");
  expect(joined).toContain("Checkpoint after 2 tool rounds");
  // The digest opens its own segment after the preamble (not fused into it).
  const breakIdx = chunks.findIndex((c) => c.roundBreak);
  expect(breakIdx).toBeGreaterThan(-1);
});

test("response_done duplicating a fully-streamed message is not re-rendered", async () => {
  const backend = new CompanionChatBackend(
    makeBridge([
      ["response_start"],
      ["response_chunk", "hello world"] as [string, string],
      ["response_done", "hello world"] as [string, string],
    ]) as never,
  );
  const chunks = await collectChunks(backend);
  expect(joinedText(chunks)).toBe("hello world");
});

test("fallback prefers the turn response over tool-carrier rows (no `\"\"` bubble)", async () => {
  // Turn produced zero chunks; state history ends on a tool-call CARRIER row
  // (content null). The old reader rendered JSON.stringify("") — a literal
  // empty-quote bubble.
  const backend = new CompanionChatBackend(
    makeBridge([], {
      response: "The policy blocked file_write; propose an alternative instead.",
      state: {
        messages: [
          { role: "user", content: "patch it" },
          { role: "assistant", content: null },
          { role: "assistant", content: "The policy blocked file_write; propose an alternative instead." },
        ],
        session: { model: "kimi-k3" },
      },
    }) as never,
  );
  const chunks = await collectChunks(backend);
  const joined = joinedText(chunks);
  expect(joined).toContain("The policy blocked file_write");
  expect(joined).not.toContain('""');
});

// Stabilization sweep 2026-10-21, chat#2: Esc right after Enter fired while
// set_model was awaited; the turn was dispatched anyway, the bridge ignored
// the earlier cancel and streamed the whole reply into hidden history.
test("an interrupt during set_model never dispatches send_message", async () => {
  const abort = new AbortController();
  const methods: string[] = [];
  const bridge = {
    ...makeBridge([["response_chunk", "hidden reply"] as [string, string]]),
    request: async (method: string) => {
      methods.push(method);
      if (method === "set_model") abort.abort(); // the user pressed Esc meanwhile
      return { response: "hidden reply", state: { messages: [], session: { model: "kimi-k3" } } };
    },
  };
  const backend = new CompanionChatBackend(bridge as never);
  const chunks: ChatStreamChunk[] = [];
  for await (const chunk of backend.chatStream!("go", [], "kimi-k3", abort.signal)) chunks.push(chunk);
  expect(methods).toEqual(["set_model"]);
  expect(joinedText(chunks)).toBe("");
});

// Stabilization sweep 2026-10-21, chat#7/#12: a turn that failed after text
// streamed glued the error onto the partial reply (one row, "offline
// fallback"), and a read timeout was called "server-side".
test("a failure after streamed text opens its own row with honest copy", async () => {
  const fns: Array<(e: unknown) => void> = [];
  const bridge = {
    ...makeBridge([]),
    on: (_name: string, fn: unknown) => {
      fns.push(fn as (e: unknown) => void);
    },
    request: async (method: string) => {
      if (method === "set_model") return {};
      for (const fn of fns) fn({ type: "response_chunk", message: "stall-alpha " });
      throw new Error("Request timed out: timed out.");
    },
  };
  const chunks = await collectChunks(new CompanionChatBackend(bridge as never));
  const breakAt = chunks.findIndex((c) => c.roundBreak);
  const failAt = chunks.findIndex((c) => c.delta?.startsWith("[Chat unavailable]"));
  expect(chunks.findIndex((c) => c.delta === "stall-alpha ")).toBeLessThan(breakAt);
  expect(breakAt).toBeLessThan(failAt);
  expect(chunks[failAt]!.delta).toContain("read timeout");
  expect(chunks[failAt]!.delta).not.toContain("server-side");
});
