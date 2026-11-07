/**
 * Zone-separation pin (2026-09-30 redesign).
 *
 * Three visual zones must never collide in ANY selectable theme:
 *   user input zone  → msgUserBg  (composer, user bubbles, in-float inputs)
 *   agent output     → msgAssistantBg (assistant prose, stays near the backdrop)
 *   floating panes   → bgActive   (overlays / palettes, rendered line-free)
 *
 * Prev-wave incident: five themes had msgUserBg === bgActive exactly (input
 * fields on floats disappeared); nord + solarized-dark had msgUserBg ===
 * bgPanel (user bubbles invisible on the chat panel). Thresholds below keep
 * the zones perceptually apart (Euclidean RGB distance).
 */

import { describe, it, expect } from "bun:test";
import { THEMES } from "../src/themes";

function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function dist(a: string, b: string): number {
  const [ar, ag, ab] = rgb(a);
  const [br, bg, bb] = rgb(b);
  return Math.hypot(ar - br, ag - bg, ab - bb);
}

describe("theme zone separation (user input / agent output / floating)", () => {
  it("every theme keeps the three zones apart", () => {
    for (const theme of THEMES) {
      const c = theme.colors;
      // user zone vs floating surface (inputs must show on overlays)
      expect(dist(c.msgUserBg, c.bgActive), `${theme.name}: msgUserBg~bgActive`).toBeGreaterThanOrEqual(28);
      // user zone vs agent output (bubble contrast in the transcript)
      expect(dist(c.msgUserBg, c.msgAssistantBg), `${theme.name}: msgUserBg~msgAssistantBg`).toBeGreaterThanOrEqual(35);
      // user zone vs its own panel backdrop (composer / bubbles must show)
      expect(dist(c.msgUserBg, c.bgPanel), `${theme.name}: msgUserBg~bgPanel`).toBeGreaterThanOrEqual(28);
    }
  });
});
