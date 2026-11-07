// The shared reply fold (ADR-103 D-3): the console's chat adapter and the VS
// Code extension's bridge host render a turn's response_* events by it.
import { describe, expect, test } from "bun:test";
import { TurnReplyFold, type ReplyEvent, type ReplyPiece } from "../src/lib/turn-reply";

function foldAll(events: ReplyEvent[]): ReplyPiece[] {
  const fold = new TurnReplyFold();
  return events.flatMap((e) => fold.fold(e));
}

describe("TurnReplyFold", () => {
  test("streamed chunks; the first is marked for time-to-first-token", () => {
    expect(foldAll([{ type: "response_chunk", message: "He" }, { type: "response_chunk", message: "llo" }])).toEqual([
      { kind: "delta", text: "He", first: true },
      { kind: "delta", text: "llo", first: false },
    ]);
  });

  test("a done that repeats the streamed text adds nothing (TUI audit r2 #21)", () => {
    expect(
      foldAll([{ type: "response_chunk", message: "Hello\n" }, { type: "response_done", message: "Hello" }]),
    ).toEqual([{ kind: "delta", text: "Hello\n", first: true }]);
  });

  test("a done that never streamed renders after a round break, never as a first token", () => {
    expect(
      foldAll([
        { type: "response_chunk", message: "Reading." },
        { type: "response_done", message: "Wrap-up only in done" },
      ]),
    ).toEqual([
      { kind: "delta", text: "Reading.", first: true },
      { kind: "roundBreak" },
      { kind: "delta", text: "Wrap-up only in done", first: false },
    ]);
  });

  test("round breaks only after a round with text; turn ids pass through", () => {
    expect(
      foldAll([
        { type: "turn_opened", data: { turn_id: "t-1" } },
        { type: "response_start" },
        { type: "response_start" },
        { type: "response_chunk", message: "a" },
        { type: "response_start" },
        { type: "tool_call" },
        { type: "turn_opened", data: { turn_id: "" } },
      ]),
    ).toEqual([
      { kind: "turnOpened", turnId: "t-1" },
      { kind: "delta", text: "a", first: true },
      { kind: "roundBreak" },
    ]);
  });

  test("sawDelta follows any produced text", () => {
    const fold = new TurnReplyFold();
    expect(fold.sawDelta).toBe(false);
    fold.fold({ type: "response_done", message: "x" });
    expect(fold.sawDelta).toBe(true);
  });
});
