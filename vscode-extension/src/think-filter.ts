/**
 * Streaming filter that removes model reasoning markup from visible chat text.
 *
 * Served reasoning models (e.g. Kimi-K2) emit `<think>…</think>` reasoning
 * inline in the OpenAI `content` field when no server-side `--reasoning-parser`
 * is configured (see umrs/deploy/ai4s/serve.slurm, which only sets one for GLM).
 * The chat template usually injects the opening `<think>` into the prompt, so
 * only a stray closing `</think>` leaks into the answer. This filter strips
 * complete `<think>…</think>` blocks, hides a leading orphan `</think>` block
 * (reasoning with no in-band opening tag), and removes stray close markers,
 * while streaming normal text through unchanged. Markers split across stream
 * deltas are handled via a small carry buffer.
 *
 * Fenced and inline code are masked before tag detection so literal tag names in
 * documentation (e.g. `` `</think>` ``) are not misrouted to thinking UI.
 */
const SENTINEL = "\u0000";
const FENCE_RE = /```[\s\S]*?```/g;
const INLINE_CODE_RE = /`[^`\n]*`/g;

export class ThinkTagFilter {
  private static readonly OPEN = "<think>";
  private static readonly CLOSE = "</think>";

  private carry = "";
  private inThink = false;
  private emittedVisible = false;
  private activeSpans: string[] = [];
  // Set when the stream was *assumed* to start mid-reasoning via
  // beginInThinking() (no in-band opening <think>). Until a closing </think>
  // confirms the guess, buffer the routed reasoning so it can be recovered as
  // the visible answer if the close tag never arrives.
  private speculative = false;
  private speculativeBuffer = "";

  constructor(private readonly onThinking?: (text: string) => void) {}

  get hasEmittedVisible(): boolean {
    return this.emittedVisible;
  }

  /**
   * Start the stream inside a reasoning block. Used for "reasoning-first"
   * models (Kimi, GLM, …) whose chat template injects the opening `<think>`
   * into the prompt, so the streamed output begins mid-thought and only ever
   * emits a closing `</think>`. Without this the leading reasoning would be
   * mistaken for the answer and leak into visible chat. No-op once any visible
   * text has been emitted.
   */
  beginInThinking(): void {
    if (!this.emittedVisible) {
      this.inThink = true;
      this.speculative = true;
    }
  }

  /** Feed one streamed content chunk; returns the visible text to render. */
  push(chunk: string): string {
    const text = this.carry + chunk;
    this.carry = "";

    const { processable, hold: fenceHold } = splitIncompleteFence(text);
    const hold = trailingPartialTag(processable, [
      ThinkTagFilter.OPEN,
      ThinkTagFilter.CLOSE,
    ]);
    const tagHold =
      hold > 0 ? processable.slice(processable.length - hold) : "";
    const ready =
      hold > 0 ? processable.slice(0, processable.length - hold) : processable;
    this.carry = fenceHold + tagHold;

    const { masked, spans } = maskCode(ready);
    this.activeSpans = spans;
    return unmaskCode(this.processTags(masked), spans);
  }

  /** Flush any buffered text at end of stream. */
  flush(): string {
    if (this.inThink) {
      if (this.speculative) {
        // beginInThinking() guessed the stream started mid-reasoning, but no
        // closing </think> ever arrived. The content was the real answer (e.g.
        // reasoning disabled, or edits emitted directly in `content`), not
        // reasoning — recover it as visible text so it is not hidden inside the
        // collapsed thinking UI or dropped entirely.
        let tail = this.carry;
        if (tail && ThinkTagFilter.CLOSE.startsWith(tail)) {
          // Dangling partial closing tag fragment (e.g. "</thi") — never real
          // answer content, so drop it.
          tail = "";
        }
        const recovered = this.speculativeBuffer + tail;
        this.speculativeBuffer = "";
        this.carry = "";
        this.inThink = false;
        this.speculative = false;
        if (recovered) {
          this.emittedVisible = true;
        }
        return recovered;
      }
      this.carry = "";
      return "";
    }
    const tail = this.carry;
    this.carry = "";
    return tail;
  }

  private processTags(text: string): string {
    const OPEN = ThinkTagFilter.OPEN;
    const CLOSE = ThinkTagFilter.CLOSE;
    let out = "";
    let i = 0;

    while (i < text.length) {
      if (this.inThink) {
        const close = text.indexOf(CLOSE, i);
        if (close === -1) {
          const rest = text.slice(i);
          const hold = trailingPartialTag(rest, [CLOSE]);
          const thinking = hold > 0 ? rest.slice(0, rest.length - hold) : rest;
          this.reportThinking(thinking);
          this.carry =
            this.carry + (hold > 0 ? rest.slice(rest.length - hold) : "");
          return out;
        }
        this.reportThinking(text.slice(i, close));
        i = close + CLOSE.length;
        this.inThink = false;
        // Close tag confirmed the reasoning guess — disarm the safety net so
        // flush() does not re-emit the (genuine) reasoning as the answer.
        this.speculative = false;
        this.speculativeBuffer = "";
        continue;
      }

      const open = text.indexOf(OPEN, i);
      const close = text.indexOf(CLOSE, i);

      if (close !== -1 && (open === -1 || close < open)) {
        const preceding = text.slice(i, close);
        if (this.emittedVisible && preceding) {
          out += preceding;
        } else {
          this.reportThinking(preceding);
        }
        i = close + CLOSE.length;
        continue;
      }

      if (open === -1) {
        const rest = text.slice(i);
        const hold = trailingPartialTag(rest, [OPEN, CLOSE]);
        const visible = hold > 0 ? rest.slice(0, rest.length - hold) : rest;
        if (visible) {
          out += visible;
          this.emittedVisible = true;
        }
        this.carry = this.carry + (hold > 0 ? rest.slice(rest.length - hold) : "");
        return out;
      }

      const visible = text.slice(i, open);
      if (visible) {
        out += visible;
        this.emittedVisible = true;
      }
      this.inThink = true;
      i = open + OPEN.length;
    }

    return out;
  }

  private reportThinking(text: string): void {
    if (!text) {
      return;
    }
    // Reasoning text is processed in code-masked form; restore any fenced or
    // inline code so the thinking UI shows real code, not sentinel markers.
    const restored = unmaskCode(text, this.activeSpans);
    if (this.speculative) {
      // Keep a copy so flush() can recover it as the answer if no closing
      // </think> ever confirms this was reasoning.
      this.speculativeBuffer += restored;
    }
    if (this.onThinking) {
      this.onThinking(restored);
    }
  }
}

function maskCode(text: string): { masked: string; spans: string[] } {
  const spans: string[] = [];
  const stash = (match: string): string => {
    spans.push(match);
    return `${SENTINEL}${spans.length - 1}${SENTINEL}`;
  };
  const masked = text
    .replace(FENCE_RE, stash)
    .replace(INLINE_CODE_RE, stash);
  return { masked, spans };
}

function unmaskCode(masked: string, spans: string[]): string {
  return masked.replace(
    new RegExp(`${SENTINEL}(\\d+)${SENTINEL}`, "g"),
    (_, index) => spans[Number(index)] ?? "",
  );
}

/** Hold an unclosed trailing fenced code block across stream deltas. */
function splitIncompleteFence(text: string): {
  processable: string;
  hold: string;
} {
  let fenceStart = -1;
  let inFence = false;
  let i = 0;
  while (i < text.length) {
    if (text.startsWith("```", i)) {
      if (inFence) {
        inFence = false;
        fenceStart = -1;
      } else {
        inFence = true;
        fenceStart = i;
      }
      i += 3;
      continue;
    }
    i++;
  }
  if (inFence && fenceStart >= 0) {
    return {
      processable: text.slice(0, fenceStart),
      hold: text.slice(fenceStart),
    };
  }
  return { processable: text, hold: "" };
}

function trailingPartialTag(text: string, markers: string[]): number {
  let best = 0;
  for (const marker of markers) {
    const maxK = Math.min(marker.length - 1, text.length);
    for (let k = maxK; k > 0; k--) {
      if (text.slice(text.length - k) === marker.slice(0, k)) {
        best = Math.max(best, k);
        break;
      }
    }
  }
  return best;
}
