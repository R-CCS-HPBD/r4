/**
 * Keyboard ↑↓ list selection — shared by Tasks / Knowledge / Infrastructure.
 * Pair with <KeyTrap> (hidden focus, no cursor / no typing).
 */

export type ListKeyHandler = {
  onMove: (index: number) => void;
  onEnter?: (index: number) => void;
  onEscape?: () => void;
  /** Extra keys (e.g. Tab). Return true if handled. */
  onOther?: (name: string) => boolean;
};

export function clampIndex(index: number, length: number): number {
  if (length <= 0) return 0;
  return Math.max(0, Math.min(index, length - 1));
}

/** Advance selection; returns new index. */
export function moveListIndex(
  current: number,
  length: number,
  direction: "up" | "down",
): number {
  if (length <= 0) return 0;
  if (direction === "down") return Math.min(current + 1, length - 1);
  return Math.max(current - 1, 0);
}

export function handleListKey(
  eventName: string,
  opts: {
    selected: number;
    length: number;
    ctrl?: boolean;
  } & ListKeyHandler,
): boolean {
  const { selected, length, ctrl, onMove, onEnter, onEscape, onOther } = opts;
  if (eventName === "down" || (eventName === "n" && ctrl)) {
    onMove(moveListIndex(selected, length, "down"));
    return true;
  }
  if (eventName === "up" || (eventName === "p" && ctrl)) {
    onMove(moveListIndex(selected, length, "up"));
    return true;
  }
  if (eventName === "return" || eventName === "enter") {
    onEnter?.(selected);
    return true;
  }
  if (eventName === "escape") {
    onEscape?.();
    return Boolean(onEscape);
  }
  if (onOther?.(eventName)) return true;
  return false;
}
