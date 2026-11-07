import { describe, expect, it } from "bun:test";
import { resolveVerificationForUi } from "../src/lib/resolve-verification";
import { actions } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";

describe("resolveVerificationForUi", () => {
  it("uses offline demo answers when backend is offline", async () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    try {
      agentActions.updateMissionControl({
        researchGoal: "Identify biomarkers",
        hypotheses: [{ id: "H7", statement: "Test", confidence: 0.7 }],
      });
      const result = await resolveVerificationForUi("why?");
      expect(result.answer.length).toBeGreaterThan(0);
      expect(result.confidence).toBeGreaterThan(0);
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });

  it("falls through to project notice when live gated and no project", async () => {
    process.env["R4_TUI_BACKEND"] = "cli";
    delete process.env["UMRS_BASE_URL"];
    delete process.env["UMRS_URL"];
    actions.resetWorkspace();
    try {
      const result = await resolveVerificationForUi("what supports this?");
      expect(result.confidence).toBe(0);
      expect(result.answer).toMatch(/No active project|not connected|r4 project verify/i);
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });
});
