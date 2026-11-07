/**
 * Minimal SSE reader over fetch.
 *
 * EventSource cannot carry Authorization headers, and the companion turn
 * stream sits behind the NGINX auth sidecar — so the stream is opened with
 * gatewayFetch and parsed manually. Reconnect/reattach policy lives in the
 * caller; this reader resolves once when the stream closes.
 */

export interface SseReadOptions {
  /** Called once per `data:` payload (raw string, not JSON-decoded). */
  onEvent: (data: string) => void;
}

const SSE_TERMINATOR = "\n\n";
const DATA_PREFIX = "data:";

/**
 * Read a text/event-stream response to completion.
 * Resolves when the stream closes cleanly; rejects on HTTP or transport errors.
 */
export async function readEventStream(response: Response, opts: SseReadOptions): Promise<void> {
  if (!response.ok) {
    throw new Error(`stream open failed: HTTP ${response.status}`);
  }
  const body = response.body;
  if (!body) {
    throw new Error("stream open failed: empty response body");
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buffer.indexOf(SSE_TERMINATOR)) >= 0) {
        const rawEvent = buffer.slice(0, cut);
        buffer = buffer.slice(cut + SSE_TERMINATOR.length);
        for (const line of rawEvent.split("\n")) {
          if (line.startsWith(DATA_PREFIX)) {
            opts.onEvent(line.slice(DATA_PREFIX.length).trimStart());
          }
        }
      }
    }
    // Flush a trailing event that ended without the blank-line terminator.
    const tail = buffer.trim();
    if (tail.startsWith(DATA_PREFIX)) {
      opts.onEvent(tail.slice(DATA_PREFIX.length).trimStart());
    }
  } finally {
    reader.releaseLock();
  }
}
