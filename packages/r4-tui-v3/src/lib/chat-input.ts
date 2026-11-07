/**
 * Chat composer input routing (pure).
 */

/**
 * True when a chat-input value beginning with "/" should be diverted into
 * the command palette.
 *
 * Only a bare command-name fragment diverts ("/", "/log", "/sessions") —
 * i.e. what the user TYPES starting at the slash. A value containing
 * whitespace is a FULL slash line with args (pasted or menu-seeded, e.g.
 * "/login <JWT>", "/source add <url>") and must stay in the composer so
 * Enter dispatches it verbatim via dispatchSlashFromChat.
 *
 * Diverting full lines was fatal for token args: the palette received the
 * whole "/login eyJ…" line as a fuzzy query, matched no command row, and
 * Enter dead-ended on "No commands match." — a pasted JWT could never be
 * recognised as a command (user report 2026-09-11).
 */
export function shouldDivertSlashToPalette(value: string): boolean {
  if (!value.startsWith("/")) return false;
  return !/\s/.test(value);
}
