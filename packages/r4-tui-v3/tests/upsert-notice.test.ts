/**
 * upsertSystemNotice — keyed system notices update the SAME transcript row
 * (cumulative tool-activity summary) instead of appending a new row per
 * interim state.
 */

import { describe, expect, test } from "bun:test";
import { actions, workspace } from "../src/stores";

function systemRows() {
  return workspace.chatMessages.filter((m) => m.role === "system");
}

describe("upsertSystemNotice", () => {
  test("first call appends a keyed system notice", () => {
    const before = systemRows().length;
    actions.upsertSystemNotice("__t-key", "✓ $ 1 command run");
    const rows = systemRows();
    expect(rows.length).toBe(before + 1);
    const last = rows[rows.length - 1]!;
    expect(last.content).toBe("✓ $ 1 command run");
    expect(last.noticeKey).toBe("__t-key");
  });

  test("second call with the same key replaces the same row", () => {
    actions.upsertSystemNotice("__t-key2", "1 command run");
    const idBefore = systemRows()[systemRows().length - 1]!.id;
    const countBefore = systemRows().length;
    actions.upsertSystemNotice("__t-key2", "3 commands run");
    const rows = systemRows();
    expect(rows.length).toBe(countBefore);
    const last = rows[rows.length - 1]!;
    expect(last.id).toBe(idBefore);
    expect(last.content).toBe("3 commands run");
  });

  test("different keys append independently; user messages between do not disturb the lookup", () => {
    actions.upsertSystemNotice("__t-a", "summary A");
    actions.upsertSystemNotice("__t-b", "summary B");
    const rowsA = systemRows().length;
    actions.upsertSystemNotice("__t-a", "summary A updated");
    const rows = systemRows();
    expect(rows.length).toBe(rowsA);
    expect(rows.filter((m) => m.noticeKey === "__t-b").at(-1)!.content).toBe("summary B");
    expect(rows.filter((m) => m.noticeKey === "__t-a").at(-1)!.content).toBe("summary A updated");
  });

  test("blank content is ignored", () => {
    const countBefore = systemRows().length;
    actions.upsertSystemNotice("__t-blank", "   ");
    expect(systemRows().length).toBe(countBefore);
  });
});
