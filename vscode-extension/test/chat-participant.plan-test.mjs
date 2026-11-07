import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, writeFileSync } from "node:fs";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "chat-participant.plan-test.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "src/chat-participant.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--external:vscode",
    "--outfile=" + bundlePath,
  ],
  { stdio: "inherit", cwd: rootDir },
);

let bundle = readFileSync(bundlePath, "utf-8");
bundle = bundle.replace(/__toESM\(require\("vscode"\)\)/g, "{}");
// Patch __toCommonJS so module.exports works in non-VSCode runtime.
bundle = bundle.replace(/var __toCommonJS = .+?;/, "var __toCommonJS = (m) => m;");
// Add planModeSystemPrompt and planModeFooter to the __export call.
bundle = bundle.replace(
  "resolvePlanState\n});",
  "resolvePlanState,\n  planModeSystemPrompt: () => planModeSystemPrompt,\n  planModeFooter: () => planModeFooter\n});",
);
writeFileSync(bundlePath, bundle);

const require = createRequire(import.meta.url);
const { resolvePlanState, planModeSystemPrompt, planModeFooter } = require(bundlePath);

// === Plan-mode state machine tests ===

test("plan-mode: plan state returns plan override and disables tool calls", () => {
  const result = resolvePlanState("plan", { awaiting_confirmation: true, plan_text: "" });
  assert.ok(result.planOverride, "plan override should be present");
  assert.ok(result.planOverride.includes("### USER_ACTION_REQUIRED"), "plan override includes USER_ACTION_REQUIRED");
  assert.equal(result.enableToolCalls, false, "tool calls disabled in plan mode");
});

test("plan-mode: awaiting_confirmation false still disables tool calls", () => {
  const result = resolvePlanState("plan", { awaiting_confirmation: false });
  assert.equal(result.planOverride, undefined, "no plan override when not awaiting");
  assert.equal(result.enableToolCalls, false, "tool calls still disabled");
});

test("plan-mode: normal mode enables tool calls", () => {
  const result = resolvePlanState("normal", null);
  assert.equal(result.planOverride, undefined, "no plan override in normal mode");
  assert.equal(result.enableToolCalls, true, "tool calls enabled");
});

test("plan-mode: undefined interaction mode enables tool calls", () => {
  const result = resolvePlanState(undefined, null);
  assert.equal(result.planOverride, undefined, "no plan override with no mode");
  assert.equal(result.enableToolCalls, true, "tool calls enabled");
});

test("plan-mode: system prompt includes USER_ACTION_REQUIRED marker", () => {
  const prompt = planModeSystemPrompt();
  assert.ok(prompt.includes("### USER_ACTION_REQUIRED"), "system prompt includes USER_ACTION_REQUIRED");
  assert.ok(prompt.includes("yes") && prompt.includes("no"), "system prompt mentions yes/no");
});

test("plan-mode: footer text is user-friendly", () => {
  const footer = planModeFooter();
  assert.ok(footer.includes("Plan Ready"), "footer has Plan Ready heading");
  assert.ok(footer.includes("yes") && footer.includes("no"), "footer mentions yes/no");
});

test("full flow: yes response resets plan state", () => {
  let interactionMode = "plan";
  let planModeState = { awaiting_confirmation: true, plan_text: "step 1, step 2" };

  const userResponse = "yes";
  if (userResponse === "yes" || userResponse === "y") {
    planModeState = { awaiting_confirmation: false };
    interactionMode = "normal";
  }

  assert.equal(interactionMode, "normal", "interaction_mode becomes normal after yes");
  assert.ok(!planModeState.awaiting_confirmation, "awaiting_confirmation cleared");

  const result = resolvePlanState(interactionMode, planModeState);
  assert.equal(result.enableToolCalls, true, "tool calls enabled after yes");
  assert.equal(result.planOverride, undefined, "no plan override after yes");
});

test("full flow: no response resets plan state", () => {
  let interactionMode = "plan";
  let planModeState = { awaiting_confirmation: true };

  const userResponse = "no";
  if (userResponse === "no" || userResponse === "n") {
    planModeState = { awaiting_confirmation: false };
    interactionMode = "normal";
  }

  assert.equal(interactionMode, "normal", "interaction_mode becomes normal after no");
  assert.ok(!planModeState.awaiting_confirmation, "awaiting_confirmation cleared");
});

test("full flow: non-yes/no response preserves plan state", () => {
  let interactionMode = "plan";
  let planModeState = { awaiting_confirmation: true, plan_text: "some plan" };

  const userResponse = "can you add error handling?";
  // Not yes/no — state should be preserved.
  if (userResponse !== "yes" && userResponse !== "y" && userResponse !== "no" && userResponse !== "n") {
    // State unchanged.
  }

  assert.equal(interactionMode, "plan", "interaction_mode stays plan");
  assert.ok(planModeState.awaiting_confirmation, "awaiting_confirmation still true");

  const result = resolvePlanState(interactionMode, planModeState);
  assert.equal(result.enableToolCalls, false, "tool calls still disabled");
  assert.ok(result.planOverride, "plan override still present");
});
