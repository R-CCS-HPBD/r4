/**
 * B6 — Context + compaction indicator.
 * Cost chip already carries the ctx gauge (verified here as a contract);
 * compaction events from the companion surface as keyed, update-in-place
 * notices, with summarize-text under ↵ when the wire carries it.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { formatCostChip } from "../src/lib/cost-chip";

describe("ctx gauge (cost chip contract)", () => {
  test("renders the context-fill percentage", () => {
    const chip = formatCostChip({
      session_id: "s",
      model: "m",
      turn_tokens: 1000,
      total_tokens: 1000,
      cost_usd_turn: 0,
      cost_usd_total: 0,
      price_known: false,
      context_used_tokens: 6200,
      context_limit_tokens: 10000,
    });
    expect(chip).toContain("62% ctx");
  });
});

describe("compaction notice (keyed update-in-place)", () => {
  beforeEach(() => {
    setWorkspace("chatMessages", []);
    setWorkspace("noticeDetails", {});
    setWorkspace("lastSummaryKey", null);
  });

  test("a compaction upsert carries ⇅ mark and keeps details attachable", () => {
    actions.upsertSystemNotice("context-compaction", "⇅ context compacted — Conversation compacted");
    actions.setNoticeDetails("context-compaction", ["first summary line", "second line"]);
    const row = workspace.chatMessages.at(-1)!;
    expect(row.role).toBe("system");
    expect(row.content).toContain("⇅ context compacted");
    expect(workspace.noticeDetails["context-compaction"]?.length).toBe(2);
    expect(workspace.lastSummaryKey).toBe("context-compaction");
  });

  test("later compaction refresh updates the same row in place", () => {
    actions.upsertSystemNotice("context-compaction", "⇅ context compacted — first");
    const before = workspace.chatMessages.length;
    actions.upsertSystemNotice("context-compaction", "⇅ context compacted — second");
    expect(workspace.chatMessages.length).toBe(before);
    expect(workspace.chatMessages.at(-1)!.content).toContain("second");
  });
});
