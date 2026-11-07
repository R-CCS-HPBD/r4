import { describe, expect, test } from "bun:test";
import { cardPaneTarget } from "../src/components/mgmt-card";
import type { MgmtCard } from "../src/types";

function card(type: string): MgmtCard {
  return { v: 1, type, payload: {} };
}

describe("cardPaneTarget — cards are the buttons that summon panes (V5)", () => {
  test("workflow cards → workflows pane", () => {
    expect(cardPaneTarget(card("wf-list"))).toBe("work");
    expect(cardPaneTarget(card("wf-run"))).toBe("work");
    expect(cardPaneTarget(card("wf-status"))).toBe("work");
  });
  test("loop cards → research pane", () => {
    expect(cardPaneTarget(card("loop-list"))).toBe("research");
    expect(cardPaneTarget(card("loop-status"))).toBe("research");
    expect(cardPaneTarget(card("session-view"))).toBe("research");
  });
  test("knowledge cards → knowledge pane", () => {
    expect(cardPaneTarget(card("knowledge-status"))).toBe("knowledge");
    expect(cardPaneTarget(card("knowledge-graph"))).toBe("knowledge");
    expect(cardPaneTarget(card("knowledge-detail"))).toBe("knowledge");
  });
  test("inert cards stay inert (no phantom panes)", () => {
    expect(cardPaneTarget(card("request-op"))).toBeNull();
    expect(cardPaneTarget(card("mystery"))).toBeNull();
  });
});
