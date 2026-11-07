/** C3: cost chip renders live from the session/cost control envelope. */
import { test, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions } from "../src/stores";
import { formatCostChip } from "../src/lib/cost-chip";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  resetWorkspace();
  actions.closeAllOverlays();
});
afterEach(async () => {
  await new Promise((r) => setTimeout(r, 50));
  resetWorkspace();
  actions.closeAllOverlays();
});

test("cost chip formats the shared contract text", () => {
  expect(formatCostChip({
    session_id: "s1",
    model: "qwen",
    turn_tokens: 12345,
    total_tokens: 90000,
    cost_usd_turn: 0.0931,
    cost_usd_total: 0.42,
    price_known: true,
    context_used_tokens: 12345,
    context_limit_tokens: 131072,
  })).toBe("⚡ 12.3k (9% ctx) · $0.0931");

  expect(formatCostChip({
    session_id: "s1",
    model: "local",
    turn_tokens: 800,
    total_tokens: 800,
    cost_usd_turn: 0,
    cost_usd_total: 0,
    price_known: false,
    context_used_tokens: 800,
    context_limit_tokens: 128000,
  })).toBe("⚡ 800 (1% ctx) · free");
});

test("cost chip appears in the chat frame when a cost state lands", async () => {
  const setup = await testRender(() => <App />, { width: 160, height: 60 });
  actions.setCostState({
    session_id: "s1",
    model: "qwen-ready",
    turn_tokens: 12345,
    total_tokens: 90000,
    cost_usd_turn: 0.0931,
    cost_usd_total: 0.42,
    price_known: true,
    context_used_tokens: 12345,
    context_limit_tokens: 131072,
  });
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  expect(frame).toContain("⚡ 12.3k (9% ctx) · $0.0931");
  setup.renderer.destroy();
});
