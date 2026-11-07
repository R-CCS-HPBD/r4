import { test, expect } from "bun:test";
import { actions, workspace } from "../src/stores";

function seedApproval(id: string | number = "gate-1") {
  actions.openToolApproval({
    id,
    tool: "workflow.approve_gate",
    description: "manual gate — release the run?",
    sessionApprovalAllowed: true,
  });
}

test("approval decision emits an inline request-op card when ambient enabled", async () => {
  const wire = (await import("../src/bridge/approval-bridge"));
  seedApproval();
  actions.setAmbientCardsEnabled(true);

  // First decision should append a request-op mgmt-card with the answer.
  wire.respondToolApproval("once");
  const cards = workspace.chatMessages
    .filter((m) => m.mgmtCard?.type === "request-op");
  expect(cards.length).toBeGreaterThanOrEqual(1);
  const last = cards.at(-1)!.mgmtCard!.payload as { decision?: string; tool?: string };
  expect(last.decision).toBe("once");
  expect(last.tool).toBe("workflow.approve_gate");
});

test("ambient setting off skips the inline card", async () => {
  const wire = (await import("../src/bridge/approval-bridge"));
  actions.setAmbientCardsEnabled(false);
  const baseline = workspace.chatMessages.filter((m) => m.mgmtCard?.type === "request-op").length;

  seedApproval("gate-2");
  wire.respondToolApproval("reject");
  const after = workspace.chatMessages.filter((m) => m.mgmtCard?.type === "request-op").length;
  expect(after).toBe(baseline);
});
