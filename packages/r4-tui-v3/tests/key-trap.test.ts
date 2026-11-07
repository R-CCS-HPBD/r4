import { describe, expect, it } from "bun:test";
import {
  keyTrapWouldInsert,
  keyTrapOwnedByGlobalKeymap,
} from "../src/components/key-trap";
import type { KeyEvent } from "@opentui/core";

function fakeKey(
  partial: Pick<KeyEvent, "name"> &
    Partial<Pick<KeyEvent, "sequence" | "ctrl" | "meta" | "super" | "hyper">>,
): KeyEvent {
  return {
    name: partial.name,
    ctrl: partial.ctrl ?? false,
    meta: partial.meta ?? false,
    shift: false,
    option: false,
    sequence: partial.sequence ?? "",
    number: false,
    raw: "",
    eventType: "press",
    source: "raw",
    super: partial.super,
    hyper: partial.hyper,
    get defaultPrevented() {
      return false;
    },
    get propagationStopped() {
      return false;
    },
    preventDefault() {},
    stopPropagation() {},
  } as KeyEvent;
}

describe("keyTrapWouldInsert", () => {
  it("treats printable sequences and space as insertable", () => {
    expect(keyTrapWouldInsert(fakeKey({ name: "s", sequence: "s" }))).toBe(true);
    expect(keyTrapWouldInsert(fakeKey({ name: "d", sequence: "d" }))).toBe(true);
    expect(keyTrapWouldInsert(fakeKey({ name: "a", sequence: "a" }))).toBe(true);
    expect(keyTrapWouldInsert(fakeKey({ name: "a", sequence: "" }))).toBe(true);
    expect(keyTrapWouldInsert(fakeKey({ name: "space", sequence: " " }))).toBe(true);
  });

  it("keyTrapInsertChar recovers name when sequence is empty", async () => {
    const { keyTrapInsertChar } = await import("../src/components/key-trap");
    expect(keyTrapInsertChar(fakeKey({ name: "a", sequence: "" }))).toBe("a");
    expect(keyTrapInsertChar(fakeKey({ name: "a", sequence: "a" }))).toBe("a");
  });

  it("ignores navigation and modified keys", () => {
    expect(keyTrapWouldInsert(fakeKey({ name: "up", sequence: "\x1b[A" }))).toBe(false);
    expect(keyTrapWouldInsert(fakeKey({ name: "return", sequence: "\r" }))).toBe(false);
    expect(keyTrapWouldInsert(fakeKey({ name: "tab", sequence: "\t" }))).toBe(false);
    expect(keyTrapWouldInsert(fakeKey({ name: "s", sequence: "s", ctrl: true }))).toBe(false);
  });
});

describe("keyTrapOwnedByGlobalKeymap", () => {
  it("owns Tab, Enter, Esc, letters, and chords", () => {
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "tab", sequence: "\t" }))).toBe(true);
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "return", sequence: "\r" }))).toBe(true);
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "escape", sequence: "\x1b" }))).toBe(true);
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "v", sequence: "v" }))).toBe(true);
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "a", sequence: "a" }))).toBe(true);
    expect(
      keyTrapOwnedByGlobalKeymap(fakeKey({ name: "s", sequence: "s", ctrl: true })),
    ).toBe(true);
  });

  it("does not own bare arrows (pane KeyTrap handles up/down)", () => {
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "up", sequence: "\x1b[A" }))).toBe(false);
    expect(keyTrapOwnedByGlobalKeymap(fakeKey({ name: "down", sequence: "\x1b[B" }))).toBe(false);
  });
});
