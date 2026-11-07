/**
 * Bridge process-generation race (login → reconnect fallout).
 *
 * reconnect() spawns a fresh bridge while the OLD process's stdout reader
 * is still winding down. Without generation guard, that stale reader calls
 * the exit handler AFTER the new process started — wiping the new process
 * reference and failing its initialize with a bogus "bridge exited".
 * Only the current generation may report exit.
 */
import { describe, expect, test } from "bun:test";
import { CompanionBridgeClient } from "../src/bridge/jsonl-client";

type AnyClient = {
  processGen: number;
  destroyed: boolean;
  reportProcessExit: (gen: number, error: Error) => void;
};

function asAny(client: CompanionBridgeClient): AnyClient {
  return client as unknown as AnyClient;
}

describe("reportProcessExit generation guard", () => {
  test("stale generation exit is ignored — new process untouched", () => {
    const client = new CompanionBridgeClient();
    const c = asAny(client);
    c.processGen = 2; // new bridge process already started
    let exitEmitted: Error | null = null;
    client.on("exit", (err: Error) => (exitEmitted = err));

    c.reportProcessExit(1, new Error("bridge exited"));

    expect(exitEmitted).toBeNull();
    expect(c.destroyed).toBe(false);
  });

  test("current generation exit reports normally", () => {
    const client = new CompanionBridgeClient();
    const c = asAny(client);
    c.processGen = 2;
    let exitEmitted: Error | null = null;
    client.on("exit", (err: Error) => (exitEmitted = err));

    c.reportProcessExit(2, new Error("bridge exited: real crash"));

    expect((exitEmitted as Error | null)?.message).toBe("bridge exited: real crash");
    expect(c.destroyed).toBe(true);
  });

  test("older stale exits stay inert even when repeated", () => {
    const client = new CompanionBridgeClient();
    const c = asAny(client);
    c.processGen = 5;
    let exits = 0;
    client.on("exit", () => exits++);
    c.reportProcessExit(3, new Error("a"));
    c.reportProcessExit(4, new Error("b"));
    expect(exits).toBe(0);
    expect(c.destroyed).toBe(false);
  });
});
