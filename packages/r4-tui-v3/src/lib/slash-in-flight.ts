/**
 * Count of companion slash commands awaiting their dispatch_slash_command
 * answer. Ctrl+C reads it: the bridge runs those commands under the chat-turn
 * pump and honours cancel_turn, but the keymap only interrupted while a chat
 * turn streamed, so a slow slash command could not be stopped (2026-10-17).
 */

let running = 0;

export async function trackSlashInFlight<T>(work: () => Promise<T>): Promise<T> {
  running += 1;
  try {
    return await work();
  } finally {
    running -= 1;
  }
}

export function slashInFlight(): boolean {
  return running > 0;
}
