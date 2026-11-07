/**
 * Terminal-safe display text (c2-tui 2026-10-21).
 *
 * Model replies, tool output and `!` command output are untrusted text, and
 * OpenTUI paints a string's characters into cells verbatim — an ESC inside
 * one reaches the terminal as a live control sequence. A reply carrying
 * "ESC ] 52 ; c ; <base64> BEL" wrote the user's clipboard (OSC 52, honoured
 * by kitty / iTerm2 / WezTerm / tmux), "ESC ] 0 ; …" retitled the window and
 * SGR / cursor sequences garbled the transcript row. Here SGR colour codes
 * (harmless styling, common in pasted logs and `ls --color` output) are
 * dropped and every other control character becomes its visible Control
 * Picture (␛ ␇ ␈ …), so nothing is hidden and nothing is executed. Newlines
 * and tabs stay; CR / CRLF become newlines.
 */

/** C0 controls except \t and \n, DEL, and the C1 range. */
const CONTROL = /[\x00-\x08\x0b-\x1f\x7f-\x9f]/;
const CONTROL_ALL = /[\x00-\x08\x0b-\x1f\x7f-\x9f]/g;
/** SGR (colour / weight) only: ESC [ params m. */
const SGR_ALL = /\x1b\[[0-9;:]*m/g;

/** The visible stand-in for one control character. */
function picture(ch: string): string {
  const code = ch.charCodeAt(0);
  if (code < 0x20) return String.fromCharCode(0x2400 + code);
  if (code === 0x7f) return "␡";
  return "�";
}

/** `text` with no character a terminal would execute (see the module doc). */
export function terminalSafe(text: string): string {
  if (!CONTROL.test(text)) return text;
  return text
    .replace(/\r\n?/g, "\n")
    .replace(SGR_ALL, "")
    .replace(CONTROL_ALL, picture);
}

/** JSON.parse reviver: every string value made terminal-safe. */
export function terminalSafeReviver(_key: string, value: unknown): unknown {
  return typeof value === "string" ? terminalSafe(value) : value;
}

/** JSON.parse whose every string value is terminal-safe — for subprocess
 * output (the `r4 … --json` CLI adapter, c3-tui D-3): a job, endpoint or
 * source name carrying `\u001b]52;…` decodes to a live escape otherwise. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- mirrors JSON.parse
export function parseTerminalSafe(raw: string): any {
  return JSON.parse(raw, terminalSafeReviver);
}
