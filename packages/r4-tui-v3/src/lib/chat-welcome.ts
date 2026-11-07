/**
 * Chat welcome lines — honest for demo, light for live (no plumbing TMI).
 */

export function welcomeChatContent(opts: { demoMode: boolean }): string {
  if (opts.demoMode) {
    return "Hi — I'm R4. Demo mode: I won't create plans, index files, or update Tasks/Knowledge. Ctrl+P lists shortcuts.";
  }
  return "Hi — I'm R4. Ask anything, or type /help. Ctrl+P lists keyboard shortcuts.";
}

export const WELCOME_MESSAGE_ID = "welcome";
