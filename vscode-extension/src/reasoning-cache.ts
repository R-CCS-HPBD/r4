/** Max in-memory reasoning entries before LRU eviction. */
export const MAX_REASONING_CACHE_SIZE = 256;

/**
 * Persists streamed reasoning across turns so multi-turn tool-call
 * conversations can inject `reasoning_content` back into assistant messages.
 */
export interface ReasoningEntry {
  text: string;
  timestamp: number;
}

export function createToolReasoningKey(toolCallId: string): string {
  return `tool:${toolCallId}`;
}

export function createPostToolReasoningKey(toolCallIds: readonly string[]): string {
  return `post-tool:${JSON.stringify(toolCallIds)}`;
}

export function pruneReasoningCache(
  cache: Map<string, ReasoningEntry>,
  clearAll: boolean,
): void {
  if (clearAll) {
    cache.clear();
    return;
  }
  if (cache.size <= MAX_REASONING_CACHE_SIZE) {
    return;
  }
  const sorted = [...cache.entries()].sort((a, b) => a[1].timestamp - b[1].timestamp);
  const toRemove = sorted.slice(0, sorted.length - MAX_REASONING_CACHE_SIZE);
  for (const [key] of toRemove) {
    cache.delete(key);
  }
}

export function cacheStreamedReasoning(
  isThinkingModel: boolean,
  accumulatedReasoning: string,
  emittedToolCallIds: readonly string[],
  trailingToolResultIds: readonly string[],
  cache: Map<string, ReasoningEntry>,
): void {
  if (!isThinkingModel || !accumulatedReasoning) {
    return;
  }
  const entry: ReasoningEntry = {
    text: accumulatedReasoning,
    timestamp: Date.now(),
  };
  if (emittedToolCallIds.length > 0) {
    for (const toolCallId of emittedToolCallIds) {
      cache.set(createToolReasoningKey(toolCallId), entry);
    }
  } else if (trailingToolResultIds.length > 0) {
    cache.set(createPostToolReasoningKey(trailingToolResultIds), entry);
  }
  pruneReasoningCache(cache, false);
}
