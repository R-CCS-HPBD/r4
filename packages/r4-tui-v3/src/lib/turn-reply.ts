/**
 * One turn's `response_*` bridge events → reply pieces (ADR-103 D-3,
 * 2026-10-31). Shared by the console's chat adapter
 * (`adapters/companion-chat.ts`) and the VS Code extension's bridge host, so
 * both render a reply from the same rules. Pure: no store, Solid or Bun.
 *
 * Rounds are separated by `roundBreak`; `response_done` renders only its
 * un-streamed remainder.
 */

/** A `{"event": …}` bridge line's payload, as far as the reply needs it. */
export interface ReplyEvent {
  type?: string;
  message?: string;
  data?: { turn_id?: unknown } | null;
}

export type ReplyPiece =
  | { kind: "delta"; text: string; first: boolean }
  | { kind: "roundBreak" }
  | { kind: "turnOpened"; turnId: string };

export class TurnReplyFold {
  private sawDeltaFlag = false;
  private roundHasText = false;
  private streamedText = "";

  /** True once any reply text (streamed or a done remainder) was produced. */
  get sawDelta(): boolean {
    return this.sawDeltaFlag;
  }

  /** The pieces one bridge event adds to the reply (none for other events). */
  fold(event: ReplyEvent): ReplyPiece[] {
    if (event.type === "response_start") return this.onRoundStart();
    if (event.type === "response_chunk") return this.onChunk(event.message ?? "");
    if (event.type === "response_done") return this.onDone((event.message ?? "").trim());
    if (event.type === "turn_opened") return this.onTurnOpened(event.data);
    return [];
  }

  // The bridge committed the user message as a session turn: tag its row
  // now, so a turn cancelled or failed later can still be rewound or
  // retried by its id (r2 #17 review).
  private onTurnOpened(data: ReplyEvent["data"]): ReplyPiece[] {
    const turnId = data?.turn_id;
    return typeof turnId === "string" && turnId ? [{ kind: "turnOpened", turnId }] : [];
  }

  // Round boundary: when the last round produced text, close that assistant
  // segment so the tools that ran after it pin UNDER that text and a fresh
  // segment starts — text and tool work interleave in invocation-time order
  // (opencode/OpenHands process-view parity). Consecutive textless rounds
  // push no break — no empty segments.
  private onRoundStart(): ReplyPiece[] {
    if (!this.roundHasText) return [];
    this.roundHasText = false;
    return [{ kind: "roundBreak" }];
  }

  private onChunk(delta: string): ReplyPiece[] {
    if (!delta) return [];
    const first = !this.sawDeltaFlag;
    this.sawDeltaFlag = true;
    this.roundHasText = true;
    this.streamedText += delta;
    return [{ kind: "delta", text: delta, first }];
  }

  // Terminal slices that never streamed chunks (hard-roof wrap-up,
  // answer-mode digest after an empty stream, …) arrive ONLY as
  // response_done — render the un-streamed remainder or the turn ends
  // looking silent right after a tool was blocked (2026-10-06 report).
  // A message already fully streamed (suffix of streamedText) is a
  // duplicate — skip it. `done` arrives trimmed, so compare against the
  // trimmed stream: a reply streamed with a trailing newline rendered twice
  // once the bridge surfaced response_done (TUI audit r2 #21).
  private onDone(done: string): ReplyPiece[] {
    if (!done || this.streamedText.trimEnd().endsWith(done)) return [];
    const pieces: ReplyPiece[] = [];
    if (this.roundHasText) {
      pieces.push({ kind: "roundBreak" });
      this.roundHasText = false;
    }
    this.sawDeltaFlag = true;
    this.streamedText += done;
    // `first` marks a streamed first token (time-to-first-token); a done
    // remainder never counts as one.
    pieces.push({ kind: "delta", text: done, first: false });
    return pieces;
  }
}
