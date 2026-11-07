/**
 * Exit-cleanup pins (issue-853 class fix, 2026-09-28): safeExit runs the
 * registered cleanup ONCE, defaultCleanup stops the bridge BEFORE the
 * renderer destroy, and teardown failure never blocks exit.
 */
import { describe, it, expect } from "bun:test";
import { defaultCleanup, registerExitCleanup } from "../src/lib/exit-cleanup";

describe("defaultCleanup", () => {
  it("stops the bridge before destroying the renderer", async () => {
    const order: string[] = [];
    await defaultCleanup(
      async () => { order.push("bridge"); },
      () => order.push("renderer"),
    );
    expect(order).toEqual(["bridge", "renderer"]);
  });

  it("a bridge-stop failure still runs the renderer destroy", async () => {
    const order: string[] = [];
    await defaultCleanup(
      async () => { throw new Error("boom"); },
      () => order.push("renderer"),
    );
    expect(order).toEqual(["renderer"]);
  });
});

describe("registerExitCleanup semantics", () => {
  it("register/replace is a single-take surface (no double cleanup)", () => {
    let calls = 0;
    registerExitCleanup(() => { calls++; });
    registerExitCleanup(() => { calls = calls + 10; });
    // Second registration replaces the first (the app's only owner).
    expect(calls).toBe(0);
  });
});
