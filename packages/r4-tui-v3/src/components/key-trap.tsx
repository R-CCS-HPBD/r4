/**
 * Invisible focus target for ↑↓ / letter shortcuts on non-chat panes.
 * Real <input> is required for OpenTUI focus; never accept typed text
 * (otherwise chars spill from the 1-col trap onto neighboring cells).
 *
 * Absolutely positioned so Yoga never shrinks sibling panes when focus moves.
 *
 * Keys owned by the global Research keymap (Tab, Enter, space-scoped
 * letters) must NOT be preventDefault'd here when used as a nav trap —
 * otherwise graph focus swallows Tab/v/A/R/C before the keymap runs.
 *
 * When captureText is true (Live verify / overlays), printable keys are
 * always preventDefault'd so they never bleed or re-fire shortcuts.
 */

import type { KeyEvent, PasteEvent } from "@opentui/core";
import { createEffect, onCleanup } from "solid-js";
import { workspace } from "../stores";
import { palette } from "../themes";

const KEYTRAP_MODIFIERS = ["ctrl", "meta", "super", "hyper"] as const;
const KEYTRAP_NAV_OWNS = new Set(["escape", "tab", "return", "enter"]);

function isModified(event: KeyEvent): boolean {
  return KEYTRAP_MODIFIERS.some((k) => Boolean(event[k as keyof KeyEvent]));
}

function isSingleAlphaNum(name: string): boolean {
  return name.length === 1 && /[a-z0-9]/.test(name);
}

function isPrintableSeq(seq: string | undefined): seq is string {
  if (!seq || seq.length !== 1) return false;
  const code = seq.charCodeAt(0);
  return code >= 32 && code !== 127;
}

/** Keys that Textarea/Input would insert if not preventDefault'd. */
export function keyTrapWouldInsert(event: KeyEvent): boolean {
  if (isModified(event)) return false;
  const name = (event.name ?? "").toLowerCase();
  return (
    event.name === "space" ||
    isSingleAlphaNum(name) ||
    isPrintableSeq(event.sequence)
  );
}

/** Printable character to insert, or "" if none. */
export function keyTrapInsertChar(event: KeyEvent): string {
  if (isModified(event)) return "";
  const seq = event.sequence;
  if (isPrintableSeq(seq)) return seq as string;
  const name = event.name ?? "";
  return event.name === "space" ? " " : isSingleAlphaNum(name) ? name : "";
}

/** Keys the global Research keymap (or shell) owns — never swallow (nav trap). */
export function keyTrapOwnedByGlobalKeymap(event: KeyEvent): boolean {
  if (isModified(event)) return true;
  const name = (event.name ?? "").toLowerCase();
  if (KEYTRAP_NAV_OWNS.has(name)) return true;
  if (/^f\d{1,2}$/.test(name)) return true;
  if (isSingleAlphaNum(name)) return true;
  const seq = event.sequence;
  return seq !== undefined && seq.length === 1 && /[a-zA-Z0-9]/.test(seq);
}

/** Decode pasted bytes to a string (PasteEvent has bytes, not text). Line
 * breaks come back as `\n`: xterm/VTE send pasted newlines as CR (CRLF from
 * some clipboards), which the single-line input then silently dropped
 * (composer#4: "l1\rl2\rl3" arrived as "l1l2l3"). */
export function pastedText(event: PasteEvent): string {
  const bytes = event.bytes;
  if (!(bytes instanceof Uint8Array)) return "";
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes).replace(/\r\n?/g, "\n");
}

export type KeyTrapProps = {
  focused?: boolean;
  /** When true, printable keys are always consumed (Live verify / modal menus). */
  captureText?: boolean;
  /** Pane-embedded trap (2026-10-06 focus model): effective focus = focused
   *  AND the pane owning the keys (workspace.paneFocus). A docked pane no
   *  longer steals the composer — chat stays live until the user Tabs into
   *  or clicks the pane. Overlay/modal traps must NOT set this. */
  paneScoped?: boolean;
  onKeyDown: (event: KeyEvent) => void;
  onPaste?: (text: string) => void;
};

const KEYTRAP_EDIT_KEYS = new Set(["backspace", "delete", "return", "enter"]);

function _captureTextTrap(event: KeyEvent): void {
  const name = (event.name ?? "").toLowerCase();
  const consumed =
    keyTrapWouldInsert(event) ||
    KEYTRAP_EDIT_KEYS.has(name) ||
    (event.ctrl && name === "u") ||
    event.sequence === "\x15" ||
    isSingleAlphaNum(name);
  if (consumed) event.preventDefault();
}

function _navTrap(event: KeyEvent): void {
  if (keyTrapOwnedByGlobalKeymap(event)) return;
  if (keyTrapWouldInsert(event) || event.name === "up" || event.name === "down") {
    event.preventDefault();
  }
}

export function KeyTrap(props: KeyTrapProps) {
  // Effective focus: the prop AND (for pane-scoped traps) the pane owning
  // the keys. Mount-time focus stays synchronous (the focused PROP below —
  // pickers/overlays assert keys work in the same render pass). The focused
  // PROP alone is unreliable on false→true FLIPS (OpenTUI does not reliably
  // re-grab focus on a pre-existing node — the /models dead-keys symptom),
  // so only EDGE transitions drive focus()/blur() explicitly, deferred to a
  // macrotask and never touching a disposed renderable (renderer teardown
  // destroys the EditBuffer — a stale timer then crashes the next suite).
  const effectiveFocused = () =>
    (props.focused ?? true) && !(props.paneScoped && !workspace.paneFocus);
  let el: { focus(): void; blur?(): void } | undefined;
  let firstRun = true;
  let disposed = false;
  onCleanup(() => {
    disposed = true;
  });
  createEffect(() => {
    const want = effectiveFocused();
    if (firstRun) {
      firstRun = false;
      return;
    }
    setTimeout(() => {
      if (disposed) return;
      if (want) el?.focus();
      else el?.blur?.();
    }, 0);
  });
  const onKeyDown = (event: KeyEvent) => {
    props.onKeyDown(event);
    if (props.captureText) _captureTextTrap(event);
    else _navTrap(event);
  };
  const onPaste = (event: PasteEvent) => {
    event.preventDefault();
    if (props.onPaste) props.onPaste(pastedText(event));
  };
  // JSX-runtime: catalogue forwards these; dtype surface currently narrows
  // InputProps without onPaste/onKeyDown accessor keys we use as base
  // RenderableOptions handlers. Semantics preserved from before. (focused is
  // the MOUNT value; subsequent edges move through the effect above.)
  const spread = {
    position: "absolute",
    left: 0,
    top: 0,
    value: "",
    width: 1,
    showCursor: false,
    backgroundColor: palette.bg,
    textColor: palette.bg,
    cursorColor: palette.bg,
    onKeyDown,
    onPaste,
  } as import("@opentui/solid").JSX.IntrinsicElements["input"];
  return (
    <input
      {...spread}
      focused={effectiveFocused()}
      ref={(r: { focus(): void; blur?(): void }) => {
        el = r;
      }}
    />
  );
}
