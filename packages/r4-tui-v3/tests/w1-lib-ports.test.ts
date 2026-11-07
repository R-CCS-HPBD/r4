import { describe, expect, test } from "bun:test";
import { createPasteTracker } from "../src/lib/paste-chips";
import { hintForError } from "../src/lib/error-hints";
import {
  BUSY_WHILE_SLASH_MESSAGE,
  QUEUED_BADGE,
  formatBusyLabel,
  isChatMessage,
  queuedMarkerFor,
} from "../src/lib/message-queue";
import {
  formatObservabilitySummary,
  isObservabilityIdle,
} from "../src/lib/observability-strip";

describe("paste-chips", () => {
  test("small paste passes through", () => {
    const t = createPasteTracker();
    expect(t.ingest("hello")).toEqual({ textToInsert: "hello", chipId: null });
    expect(t.count).toBe(0);
  });

  test("3+ lines becomes chip and re-inflates", () => {
    const t = createPasteTracker();
    const big = "a\nb\nc";
    const { textToInsert, chipId } = t.ingest(big);
    expect(textToInsert).toBe("[Pasted text #1 ~3 lines]");
    expect(chipId).toBe(1);
    expect(t.expand(`look: ${textToInsert}`)).toBe(`look: ${big}`);
  });

  test(">150 chars single line becomes chip", () => {
    const t = createPasteTracker();
    const big = "x".repeat(200);
    expect(t.ingest(big).textToInsert).toBe("[Pasted text #1 ~1 lines]");
  });

  test("unmappable chips survive expand", () => {
    const t = createPasteTracker();
    expect(t.expand("[Pasted text #9 ~9 lines]")).toBe("[Pasted text #9 ~9 lines]");
  });

  // composer#1: chips expand by identity, never by position.
  test("a forgotten (Esc'd) paste never expands into the next chip", () => {
    const t = createPasteTracker();
    const a = t.ingest("AAA-1\nAAA-2\nAAA-3").textToInsert;
    t.forget(a); // the draft holding A was cleared
    const b = t.ingest("BBB-1\nBBB-2\nBBB-3\nBBB-4").textToInsert;
    expect(t.expand(b)).toBe("BBB-1\nBBB-2\nBBB-3\nBBB-4");
  });

  test("a damaged chip does not shift the later chips onto earlier pastes", () => {
    const t = createPasteTracker();
    const a = t.ingest("AAA-1\nAAA-2\nAAA-3").textToInsert;
    const b = t.ingest("BBB-1\nBBB-2\nBBB-3\nBBB-4").textToInsert;
    const damaged = `${a.slice(0, -1)}${b}`;
    expect(t.expand(damaged)).toBe(`${a.slice(0, -1)}BBB-1\nBBB-2\nBBB-3\nBBB-4`);
  });

  test("forget drops only the chips named in the cleared text", () => {
    const t = createPasteTracker();
    const a = t.ingest("a\nb\nc").textToInsert;
    const b = t.ingest("d\ne\nf").textToInsert;
    t.forget(`sent ${b}`);
    expect(t.tokens()).toEqual([a]);
    expect(t.expand(a)).toBe("a\nb\nc");
  });
});

describe("error-hints", () => {
  test("timeout", () => {
    expect(hintForError("Request timed out")).toMatch(/^retry/);
  });
  test("401", () => {
    expect(hintForError("401 unauthorized")).toMatch(/r4 auth login/);
  });
  test("no match → null", () => {
    expect(hintForError("some novel failure")).toBeNull();
  });
});

describe("message-queue", () => {
  test("busy label with queue count", () => {
    expect(formatBusyLabel("thinking", 0)).toBe("thinking");
    expect(formatBusyLabel("thinking", 1)).toBe("thinking · 1 message queued");
    expect(formatBusyLabel("running", 3)).toBe("running · 3 messages queued");
  });
  test("slash commands are not chat messages", () => {
    expect(isChatMessage("/help")).toBe(false);
    expect(isChatMessage("hello")).toBe(true);
  });
  test("queued badge only for queued user items", () => {
    expect(queuedMarkerFor({ kind: "user", queued: true })).toBe(QUEUED_BADGE);
    expect(queuedMarkerFor({ kind: "user", queued: false })).toBeNull();
    expect(queuedMarkerFor({ kind: "assistant", queued: true })).toBeNull();
  });
  test("busy-while-slash message constant", () => {
    expect(BUSY_WHILE_SLASH_MESSAGE).toContain("queue automatically");
  });
});

describe("observability-strip", () => {
  test("idle when no active agents", () => {
    expect(isObservabilityIdle({ agentTree: { active_count: 0 } })).toBe(true);
    expect(isObservabilityIdle({ agentTree: { active_count: 2 } })).toBe(false);
  });
  test("summary format", () => {
    const s = formatObservabilitySummary({
      agentTree: { active_count: 2, total_count: 5 },
      resources: { cpu: { percent: 31 }, memory: { percent: 64 }, hostname: "data0" },
    });
    expect(s).toBe("agents:2/5 · CPU 31.0% · RAM 64% · data0");
  });
});

