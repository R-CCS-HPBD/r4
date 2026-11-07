/**
 * Terminal pane: KeyEvent → PTY byte translation.
 */

import { describe, expect, test } from "bun:test";
import { keyToBytes, wrapBracketedPaste, type KeyLike } from "../src/terminal/keys";

const k = (partial: Partial<KeyLike> & { name: string }): KeyLike => ({
  ctrl: false,
  meta: false,
  shift: false,
  ...partial,
});

const NORMAL = { applicationCursorKeys: false };
const APP = { applicationCursorKeys: true };

describe("terminal key translation", () => {
  test("printable letters and digits pass through", () => {
    expect(keyToBytes(k({ name: "a" }), NORMAL)).toBe("a");
    expect(keyToBytes(k({ name: "q", shift: true }), NORMAL)).toBe("q"); // name carries case; shift is separate
    expect(keyToBytes(k({ name: "7" }), NORMAL)).toBe("7");
  });

  test("ctrl+letters map to control bytes", () => {
    expect(keyToBytes(k({ name: "c", ctrl: true }), NORMAL)).toBe("\x03");
    expect(keyToBytes(k({ name: "C", ctrl: true, shift: true }), NORMAL)).toBe("\x03");
    expect(keyToBytes(k({ name: "l", ctrl: true }), NORMAL)).toBe("\x0c");
    expect(keyToBytes(k({ name: "d", ctrl: true }), NORMAL)).toBe("\x04");
    expect(keyToBytes(k({ name: "[", ctrl: true }), NORMAL)).toBe("\x1b");
    expect(keyToBytes(k({ name: "space", ctrl: true }), NORMAL)).toBe("\x00");
  });

  test("alt+char is ESC-prefixed (readline/vim meta)", () => {
    expect(keyToBytes(k({ name: "b", meta: true }), NORMAL)).toBe("\x1bb");
    expect(keyToBytes(k({ name: "f", meta: true }), NORMAL)).toBe("\x1bf");
  });

  test("enter / escape / tab / backspace", () => {
    expect(keyToBytes(k({ name: "return" }), NORMAL)).toBe("\r");
    expect(keyToBytes(k({ name: "enter" }), NORMAL)).toBe("\r");
    expect(keyToBytes(k({ name: "escape" }), NORMAL)).toBe("\x1b");
    expect(keyToBytes(k({ name: "tab" }), NORMAL)).toBe("\t");
    expect(keyToBytes(k({ name: "tab", shift: true }), NORMAL)).toBe("\x1b[Z");
    expect(keyToBytes(k({ name: "backspace" }), NORMAL)).toBe("\x7f");
  });

  test("arrows honour application-cursor mode and modifiers", () => {
    expect(keyToBytes(k({ name: "up" }), NORMAL)).toBe("\x1b[A");
    expect(keyToBytes(k({ name: "up" }), APP)).toBe("\x1bOA");
    expect(keyToBytes(k({ name: "down", ctrl: true }), NORMAL)).toBe("\x1b[1;5B");
    expect(keyToBytes(k({ name: "left", shift: true }), NORMAL)).toBe("\x1b[1;2D");
    expect(keyToBytes(k({ name: "right", meta: true, ctrl: true }), NORMAL)).toBe("\x1b[1;7C");
  });

  test("home/end/delete/paging/function keys", () => {
    expect(keyToBytes(k({ name: "home" }), NORMAL)).toBe("\x1b[H");
    expect(keyToBytes(k({ name: "home" }), APP)).toBe("\x1bOH");
    expect(keyToBytes(k({ name: "end" }), NORMAL)).toBe("\x1b[F");
    expect(keyToBytes(k({ name: "delete" }), NORMAL)).toBe("\x1b[3~");
    expect(keyToBytes(k({ name: "insert" }), NORMAL)).toBe("\x1b[2~");
    expect(keyToBytes(k({ name: "pageup" }), NORMAL)).toBe("\x1b[5~");
    expect(keyToBytes(k({ name: "pagedown" }), NORMAL)).toBe("\x1b[6~");
    expect(keyToBytes(k({ name: "f1" }), NORMAL)).toBe("\x1bOP");
    expect(keyToBytes(k({ name: "f5" }), NORMAL)).toBe("\x1b[15~");
    expect(keyToBytes(k({ name: "f12" }), NORMAL)).toBe("\x1b[24~");
  });

  test("unicode sequences pass through verbatim", () => {
    expect(keyToBytes(k({ name: "é", sequence: "é" }), NORMAL)).toBe("é");
    expect(keyToBytes(k({ name: "unknown-key", sequence: "世" }), NORMAL)).toBe("世");
  });

  test("ctrl+alt+letter is ESC + control byte (readline convention)", () => {
    expect(keyToBytes(k({ name: "q", ctrl: true, meta: true }), NORMAL)).toBe("\x1b\x11");
    expect(keyToBytes(k({ name: "t", ctrl: true, meta: true }), NORMAL)).toBe("\x1b\x14");
  });

  test("unknown combos are null (console keeps them)", () => {
    expect(keyToBytes(k({ name: "f12", ctrl: true, shift: true }), NORMAL)).toBe(null);
    expect(keyToBytes(k({ name: "f5", meta: true }), NORMAL)).toBe(null);
  });

  test("bracketed paste framing", () => {
    expect(wrapBracketedPaste("echo hi", false)).toBe("echo hi");
    expect(wrapBracketedPaste("echo hi", true)).toBe("\x1b[200~echo hi\x1b[201~");
  });
});
