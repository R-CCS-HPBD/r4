// Stream delta coalescing for assistantDelta events (pattern: OpenHands
// streaming-delta-batcher.ts + mergeStreamingDeltaEvent, MIT).
//
// Token bursts dispatch per chunk and re-render React on every token.
// This wrapper keys buffers by sessionId and emits at most one merged
// assistantDelta per session per interval. Ordering contract: any non-delta
// event flushes pending deltas first, preserving causal order across event types.

export interface DeltaLike {
  type: 'assistantDelta';
  sessionId: string;
  text: string;
}

export interface BatcherOptions {
  intervalMs?: number;
  maxBytes?: number;
}

export function createStreamDeltaDispatcher<TEvent extends { type: string }>(
  dispatch: (event: TEvent | DeltaLike) => void,
  opts: BatcherOptions = {},
): (event: TEvent | DeltaLike) => void {
  const intervalMs = Number.isFinite(opts.intervalMs) ? (opts.intervalMs as number) : 32;
  const maxBytes = Number.isFinite(opts.maxBytes) ? (opts.maxBytes as number) : 65536;
  const buffers = new Map<string, string>();
  let timer: ReturnType<typeof setTimeout> | null = null;

  const flush = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (buffers.size === 0) return;
    for (const [sessionId, text] of buffers) {
      dispatch({ type: 'assistantDelta', sessionId, text });
    }
    buffers.clear();
  };

  const schedule = () => {
    if (timer) return;
    timer = setTimeout(flush, intervalMs);
  };

  return (event: TEvent | DeltaLike): void => {
    if (event.type !== 'assistantDelta') {
      flush();
      dispatch(event);
      return;
    }
    const delta = event as DeltaLike;
    const sessionId = String(delta.sessionId ?? '');
    if (!delta.text && !sessionId) {
      dispatch(event);
      return;
    }
    const merged = (buffers.get(sessionId) || '') + delta.text;
    buffers.set(sessionId, merged);
    if (merged.length >= maxBytes) {
      flush();
      return;
    }
    schedule();
  };
}
