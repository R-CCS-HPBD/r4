/**
 * Phase 1 trust gates — offline honesty + CLI verification gate.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { generateLocalReply } from "../src/lib/chat-reply";
import { agentActions } from "../src/stores/agent-runtime";
import { isDemoMode, setDemoMode } from "../src/lib/console-mode";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  setDemoMode(false);
  delete process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_ALLOW_DEMO_VERIFY"];
});

afterEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_ALLOW_DEMO_VERIFY"];
  setDemoMode(false);
});

describe("Phase 1 trust: chat-reply", () => {
  it("prefixes offline and does not claim indexing", () => {
    const reply = generateLocalReply("upload my papers and make a plan", 0);
    expect(reply.startsWith("[Offline]")).toBe(true);
    expect(reply.toLowerCase()).not.toMatch(/\bqueued\b/);
    expect(reply.toLowerCase()).not.toMatch(/turned .+ into a research plan/);
    expect(reply.toLowerCase()).toMatch(/cannot upload|no indexing/);
  });
});

describe("Phase 1 trust: verification gate", () => {
  it("refuses demo answers when backend is not offline", () => {
    process.env["R4_TUI_BACKEND"] = "cli";
    const result = agentActions.queryVerification("What supports hypothesis H7?");
    expect(result.confidence).toBe(0);
    expect(result.evidenceIds).toEqual([]);
    expect(result.answer).toMatch(/not connected|r4 project verify/i);
  });

  it("allows demo answers when R4_TUI_BACKEND=offline", () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    agentActions.updateMissionControl({
      researchGoal: "Identify biomarkers",
      hypotheses: [{ id: "H7", statement: "Test", confidence: 0.7 }],
    });
    const result = agentActions.queryVerification("why?");
    expect(result.answer.length).toBeGreaterThan(0);
    expect(result.confidence).toBeGreaterThan(0);
  });
});

describe("Phase 1 trust: console-mode flag", () => {
  it("tracks demo mode", () => {
    expect(isDemoMode()).toBe(false);
    setDemoMode(true);
    expect(isDemoMode()).toBe(true);
  });
});
