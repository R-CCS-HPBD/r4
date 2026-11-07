/**
 * The session mode (ADR-104 D-1): rotation order, the bridge verb, the
 * server's notice lines, and a failure that never reads as success.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { resetWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import {
  modeOutputLines,
  nextInteractionMode,
  rotateInteractionMode,
  switchInteractionMode,
} from "../src/lib/interaction-mode";

describe("nextInteractionMode", () => {
  it("cycles chat → plan → autopilot → chat", () => {
    expect(nextInteractionMode("chat")).toBe("plan");
    expect(nextInteractionMode("plan")).toBe("autopilot");
    expect(nextInteractionMode("autopilot")).toBe("chat");
  });

  it("unknown / empty modes reset to chat", () => {
    expect(nextInteractionMode("")).toBe("chat");
    expect(nextInteractionMode("weird")).toBe("chat");
    expect(nextInteractionMode("CHAT")).toBe("plan");
  });
});

describe("set_interaction_mode", () => {
  const savedBackend = process.env.R4_TUI_BACKEND;
  let calls: Array<[string, unknown]> = [];
  let answer: (method: string) => unknown = () => ({});

  beforeEach(() => {
    process.env.R4_TUI_BACKEND = "companion";
    setDemoMode(false);
    resetWorkspace();
    calls = [];
    setCompanionBridgeForTests({
      request: async (method: string, params: unknown) => {
        calls.push([method, params]);
        const out = answer(method);
        if (out instanceof Error) throw out;
        return out;
      },
    } as never);
  });

  afterEach(() => {
    setCompanionBridgeForTests(null);
    if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = savedBackend;
    resetWorkspace();
  });

  const notices = () => workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);

  it("Shift+Tab asks for the next mode and shows the server's lines + snapshot", async () => {
    answer = () => ({
      mode: "plan",
      output: [{ kind: "info", text: "mode → plan (read-only tools only)" }],
      state: { session: { interaction_mode: "plan" } },
    });
    const outcome = await rotateInteractionMode();
    expect(outcome).toEqual({ ok: true, mode: "plan" });
    expect(calls).toEqual([["set_interaction_mode", { mode: "plan" }]]);
    expect(workspace.interactionMode).toBe("plan");
    expect(notices()).toContain("mode → plan (read-only tools only)");
  });

  it("no output lines: the fallback names the mode", async () => {
    answer = () => ({ mode: "autopilot", output: [], state: { session: { interaction_mode: "autopilot" } } });
    await switchInteractionMode("autopilot");
    expect(notices()).toContain("mode → autopilot");
  });

  it("a rotation failure shows the error, never a success notice", async () => {
    answer = () => new Error("unknown mode");
    const outcome = await rotateInteractionMode();
    expect(outcome.ok).toBe(false);
    expect(workspace.interactionMode).toBe("chat");
    expect(notices().some((n) => n.includes("mode unchanged") && n.includes("unknown mode"))).toBe(true);
    expect(notices().some((n) => n.startsWith("mode →"))).toBe(false);
  });

  it("/mode autopilot goes through the same verb", async () => {
    answer = () => ({ mode: "autopilot", output: ["autopilot on"], state: { session: { interaction_mode: "autopilot" } } });
    const { dispatchSlashCommand } = await import("../src/commands/slash");
    await dispatchSlashCommand("/mode autopilot");
    expect(calls.map((c) => c[0])).toContain("set_interaction_mode");
    expect(workspace.interactionMode).toBe("autopilot");
  });

  it("output lines accept strings and {text}", () => {
    expect(modeOutputLines(["a", { text: "b" }, "", null])).toEqual(["a", "b"]);
    expect(modeOutputLines(undefined)).toEqual([]);
  });
});
