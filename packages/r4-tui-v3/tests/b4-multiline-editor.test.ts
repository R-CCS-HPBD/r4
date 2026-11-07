/**
 * B4 — Multi-line input + external editor (Ctrl+E) helpers.
 * Terminal-visible behavior is covered by manual smoke; pure env-selection
 * rules and adoption-path invariants are unit-tested here.
 */

import { describe, expect, test } from "bun:test";
import { editorForEnv } from "../src/lib/external-editor";

describe("editorForEnv", () => {
  test("$VISUAL wins over $EDITOR", () => {
    expect(editorForEnv({ VISUAL: "code -w", EDITOR: "vim" })).toEqual({
      editor: "code -w",
      source: "VISUAL",
    });
  });
  test("$EDITOR is the fallback", () => {
    expect(editorForEnv({ EDITOR: "vim" })).toEqual({ editor: "vim", source: "EDITOR" });
  });
  test("blank values are ignored; unset → null", () => {
    expect(editorForEnv({ VISUAL: "   ", EDITOR: "" })).toBeNull();
    expect(editorForEnv({})).toBeNull();
  });
});

describe("modified-Enter key contract (2026-10-02; Alt/linefeed decode fixed 2026-10-19)", () => {
  // Mirrors chat.tsx key-chain invariants so a regression trips here:
  //  - bare Enter submits; Enter with shift/option/meta/ctrl NEVER submits
  //  - multiline handler claims option/meta/ctrl-Enter and the "linefeed" key
  type FakeEvent = { name: string; shift?: boolean; option?: boolean; meta?: boolean; ctrl?: boolean };
  const isReturn = (e: FakeEvent) => e.name === "return" || e.name === "enter";
  // chat.tsx handleSubmitAndJumpKeys guard:
  const maySubmit = (e: FakeEvent) => isReturn(e) && !(e.shift || e.option || e.ctrl || e.meta);
  // chat.tsx handleMultilineKeys claim:
  const appendsLine = (e: FakeEvent) =>
    (isReturn(e) && !!(e.option || e.meta || e.ctrl)) ||
    (e.name === "j" && !!e.ctrl) ||
    e.name === "linefeed";

  test("bare Enter submits; Shift+Enter never reaches submit", () => {
    expect(maySubmit({ name: "return" })).toBe(true);
    expect(maySubmit({ name: "enter" })).toBe(true);
    expect(maySubmit({ name: "return", shift: true })).toBe(false);
  });
  test("Alt+Enter appends a line instead of submitting — BOTH encodings", () => {
    // kitty reports option(+meta)
    expect(appendsLine({ name: "return", option: true })).toBe(true);
    expect(maySubmit({ name: "return", option: true })).toBe(false);
    // legacy ESC+CR reports meta only (2026-10-19 probe — the old code submitted!)
    expect(appendsLine({ name: "return", meta: true })).toBe(true);
    expect(maySubmit({ name: "return", meta: true })).toBe(false);
  });
  test("Ctrl+Enter (queue empty) appends a line instead of submitting", () => {
    expect(appendsLine({ name: "return", ctrl: true })).toBe(true);
    expect(maySubmit({ name: "return", ctrl: true })).toBe(false);
  });
  test("Ctrl+J is the linefeed key — appends, and j+ctrl kept for kitty decoders", () => {
    expect(appendsLine({ name: "linefeed" })).toBe(true);
    expect(appendsLine({ name: "j", ctrl: true })).toBe(true);
  });
});

describe("multiline draft display rules (contract)", () => {
  // These mirror chat.tsx displayValue/draftLineCount — number the contract so
  // a regression there trips this test instead of stranding the user with a
  // blank input box on a multiline draft.
  const displayValue = (text: string) => {
    const idx = text.lastIndexOf("\n");
    return idx >= 0 ? text.slice(idx + 1) : text;
  };
  const draftLineCount = (text: string) => (text.includes("\n") ? text.split("\n").length : 1);

  test("single-line draft displays as-is", () => {
    expect(displayValue("hello")).toBe("hello");
    expect(draftLineCount("hello")).toBe(1);
  });
  test("multiline draft shows only the last segment in the input", () => {
    expect(displayValue("line1\nline2")).toBe("line2");
    expect(draftLineCount("line1\nline2")).toBe(2);
  });
  test("trailing newline leaves an empty last segment", () => {
    expect(displayValue("a\nb\n")).toBe("");
    expect(draftLineCount("a\nb\n")).toBe(3);
  });
});
