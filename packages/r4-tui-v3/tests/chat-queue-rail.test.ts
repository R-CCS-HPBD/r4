/**
 * F9 chat-queue rail: remote lane first, session-local verbs when it fails
 * (plan H-4.4 split queueRemote; fakes typed by the bridge contract).
 */
import { afterEach, describe, expect, test } from "bun:test";
import { BridgeRequestError, setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { RpcResult } from "../src/bridge/rpc";
import { mayHaveLanded, queueRemote } from "../src/lib/chat-queue-rail";

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(replies: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  setCompanionBridgeForTests({
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push({ method, params });
      const reply = replies[method];
      if (reply instanceof Error) throw reply;
      return reply;
    },
  } as never);
  return calls;
}

const down = new Error("journal unreachable");

afterEach(() => setCompanionBridgeForTests(null));

describe("queueRemote", () => {
  test("the remote lane answers with the server queue", async () => {
    const remote: RpcResult<"session.queue_remote"> = { applied: true, queue: [{ content: "a" }, { content: "b" }], front: 2 };
    const calls = fakeBridge({ "session.queue_remote": remote });
    const out = await queueRemote("append", { content: "b", clientId: "tui" });
    expect(out).toEqual({ applied: true, usedRemote: true, count: 2 });
    expect(calls).toEqual([
      { method: "session.queue_remote", params: { action: "append", content: "b", client_id: "tui" } },
    ]);
  });

  test("append falls back to the local verb and counts its queue", async () => {
    const local: RpcResult<"session.queue_append"> = { ok: true, queue: ["x", "y", "z"] };
    const calls = fakeBridge({ "session.queue_remote": down, "session.queue_append": local });
    expect(await queueRemote("append", { content: "z" })).toEqual({ applied: true, usedRemote: false, count: 3 });
    expect(calls[1]).toEqual({ method: "session.queue_append", params: { content: "z" } });
  });

  test("pop counts what remains, clear counts what it dropped", async () => {
    const pop: RpcResult<"session.queue_pop"> = { content: "x", remaining: 4 };
    const clear: RpcResult<"session.queue_clear"> = { ok: true, cleared: 5 };
    fakeBridge({ "session.queue_remote": down, "session.queue_pop": pop, "session.queue_clear": clear });
    expect(await queueRemote("pop")).toEqual({ applied: true, usedRemote: false, count: 4 });
    expect(await queueRemote("clear")).toEqual({ applied: true, usedRemote: false, count: 5 });
  });

  test("remoteOnly never falls back; a failed local verb is not applied", async () => {
    const calls = fakeBridge({ "session.queue_remote": down, "session.queue_pop": down });
    expect(await queueRemote("pop", { remoteOnly: true })).toMatchObject({ applied: false, usedRemote: false, count: 0 });
    expect(calls.map((c) => c.method)).toEqual(["session.queue_remote"]);
    const failed = await queueRemote("pop");
    expect(failed).toMatchObject({ applied: false, usedRemote: false, count: 0 });
    expect(failed.error).toBeDefined(); // why, for the caller's message
  });

  test("a failure with no answer is unconfirmed, a definite one is not (cycle 3 verifier round 5)", async () => {
    const timeout = new Error("bridge request timed out: session.queue_remote");
    fakeBridge({ "session.queue_remote": timeout });
    expect(await queueRemote("append", { content: "a", remoteOnly: true })).toMatchObject({ applied: false, unconfirmed: true });
    fakeBridge({ "session.queue_remote": { applied: false, queue: [] } });
    const refused = await queueRemote("append", { content: "a", remoteOnly: true });
    expect(refused.applied).toBe(false);
    expect(refused.unconfirmed).toBeUndefined();
    fakeBridge({ "session.queue_remote": down, "session.queue_append": timeout });
    expect(await queueRemote("append", { content: "a" })).toMatchObject({ applied: false, unconfirmed: true });
  });

  test("mayHaveLanded: only a bridge answer (or no bridge at all) is definite", () => {
    expect(mayHaveLanded(new Error("bridge request timed out: session.queue_remote"))).toBe(true);
    expect(mayHaveLanded(new Error("bridge exited: Traceback"))).toBe(true);
    expect(mayHaveLanded(new BridgeRequestError("Request timed out: ReadTimeout.", undefined, "R4TimeoutError"))).toBe(true);
    expect(mayHaveLanded(new BridgeRequestError("Request failed: RemoteProtocolError.", undefined, "R4Error"))).toBe(true);
    expect(mayHaveLanded(new Error("bridge unavailable"))).toBe(false);
    expect(mayHaveLanded(new BridgeRequestError("Connection failed: refused.", undefined, "R4ConnectionError"))).toBe(false);
    expect(mayHaveLanded(new BridgeRequestError("changed elsewhere", undefined, "SessionConflictError"))).toBe(false);
    expect(mayHaveLanded(new Error("rpc timeout"))).toBe(true); // no bridge answer: never definite
  });
});
