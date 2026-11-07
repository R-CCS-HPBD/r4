/**
 * Terminal pane: KeyEvent → PTY byte translation (pure; no OpenTUI import so
 * it is unit-testable and reusable from the interceptor and tests).
 *
 * The pane owns every keystroke while focused — this table is the whole
 * contract. Anything unmapped returns null and the interceptor lets the key
 * fall through to the normal console keymap (help/quit escape hatches).
 */

export interface KeyLike {
  name: string;
  ctrl: boolean;
  meta: boolean;
  shift: boolean;
  /** Raw parsed sequence when the parser preserved one (unicode IME chars). */
  sequence?: string;
}

/** CSI/SS3 arrows honour the shell's application-cursor mode (DECCKM). */
export interface KeyMode {
  applicationCursorKeys: boolean;
}

const CTRL_SPECIALS = new Map<string, string>([
  [" ", "\x00"],
  ["[", "\x1b"],
  ["\\", "\x1c"],
  ["]", "\x1d"],
  ["^", "\x1e"],
  ["_", "\x1f"],
  ["/", "\x1f"],
  ["?", "\x7f"],
]);

function arrow(code: string, mode: KeyMode): string {
  return mode.applicationCursorKeys ? `\x1bO${code}` : `\x1b[${code}`;
}

function modifiedArrow(code: string, key: KeyLike, mode: KeyMode): string {
  // xterm modifier encoding: shift=2, alt=3, shift+alt=4, ctrl=5,
  // shift+ctrl=6, alt+ctrl=7, shift+alt+ctrl=8.
  const mod = 1 + (key.shift ? 1 : 0) + (key.meta ? 2 : 0) + (key.ctrl ? 4 : 0);
  if (mod === 1) return arrow(code, mode);
  if (mode.applicationCursorKeys) return `\x1bO${code}`;
  return `\x1b[1;${mod}${code}`;
}

const F1_12 = new Map<string, string>([
  ["f1", "\x1bOP"],
  ["f2", "\x1bOQ"],
  ["f3", "\x1bOR"],
  ["f4", "\x1bOS"],
  ["f5", "\x1b[15~"],
  ["f6", "\x1b[17~"],
  ["f7", "\x1b[18~"],
  ["f8", "\x1b[19~"],
  ["f9", "\x1b[20~"],
  ["f10", "\x1b[21~"],
  ["f11", "\x1b[23~"],
  ["f12", "\x1b[24~"],
]);

/** Map one key event to PTY bytes; null = not a terminal key (pass through). */
export function keyToBytes(key: KeyLike, mode: KeyMode): string | null {
  const name = key.name;
  // Single printable characters (letters, digits, punctuation, unicode).
  // OpenTUI lowercases letter names — the CASE lives in `sequence` (shift+P
  // is name "p", sequence "P"). Prefer the sequence whenever it carries one
  // printable char; falling back to the name would silently lowercase input.
  if (name.length === 1 && !key.ctrl && !key.meta) {
    if (key.sequence && key.sequence.length === 1 && !key.sequence.startsWith("\x1b")) {
      return key.sequence;
    }
    return name;
  }
  if (name.length === 1 && key.ctrl && !key.meta) {
    const lower = name.toLowerCase();
    if (lower >= "a" && lower <= "z") return String.fromCharCode(lower.charCodeAt(0) - 96);
    if (lower >= "2" && lower <= "6") {
      // Ctrl+2 = NUL (as on most terminals) through Ctrl+6 = RS.
      return ["\x00", "\x1b", "\x1c", "\x1d", "\x1e"][Number(lower) - 2] ?? null;
    }
    return CTRL_SPECIALS.get(name) ?? null;
  }
  if (name.length === 1 && key.meta && !key.ctrl) {
    // Alt+char = ESC prefix (readline/vim meta bindings).
    return `\x1b${name}`;
  }
  if (name.length === 1 && key.ctrl && key.meta) {
    const lower = name.toLowerCase();
    if (lower >= "a" && lower <= "z") return `\x1b${String.fromCharCode(lower.charCodeAt(0) - 96)}`;
    return null;
  }
  if (name.length > 1 && !key.ctrl && !key.meta && key.sequence && !key.sequence.startsWith("\x1b")) {
    // Named printable fallback with a preserved sequence (IME / unicode input).
    if (name === "space" || name === "return" || name === "tab" || name === "escape") {
      // handled by the explicit table below
    } else {
      return key.sequence;
    }
  }
  switch (name) {
    case "return":
    case "enter":
      return "\r";
    case "escape":
      return "\x1b";
    case "tab":
      return key.shift ? "\x1b[Z" : "\t";
    case "backspace":
      return "\x7f";
    case "delete":
      return "\x1b[3~";
    case "insert":
      return "\x1b[2~";
    case "home":
      return key.ctrl || key.shift || key.meta
        ? `\x1b[1;${1 + (key.shift ? 1 : 0) + (key.meta ? 2 : 0) + (key.ctrl ? 4 : 0)}H`
        : mode.applicationCursorKeys
          ? "\x1bOH"
          : "\x1b[H";
    case "end":
      return key.ctrl || key.shift || key.meta
        ? `\x1b[1;${1 + (key.shift ? 1 : 0) + (key.meta ? 2 : 0) + (key.ctrl ? 4 : 0)}F`
        : mode.applicationCursorKeys
          ? "\x1bOF"
          : "\x1b[F";
    case "pageup":
      return "\x1b[5~";
    case "pagedown":
      return "\x1b[6~";
    case "up":
      return modifiedArrow("A", key, mode);
    case "down":
      return modifiedArrow("B", key, mode);
    case "right":
      return modifiedArrow("C", key, mode);
    case "left":
      return modifiedArrow("D", key, mode);
    case "space":
      if (key.ctrl) return "\x00";
      if (key.meta) return "\x1b ";
      return " ";
    default:
      break;
  }
  if (!key.ctrl && !key.meta && F1_12.has(name)) return F1_12.get(name)!;
  return null;
}

/** Bracketed-paste framing when the shell opted in (CSI ? 2004 h). */
export function wrapBracketedPaste(bytes: string, enabled: boolean): string {
  return enabled ? `\x1b[200~${bytes}\x1b[201~` : bytes;
}
