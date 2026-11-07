/**
 * Floating — chrome for overlays, pickers, palettes and popups.
 *
 * Box vocabulary (2026-09-30 redesign): floating surfaces draw NO lines at
 * all — a third background tone (palette.bgActive) separates them from the
 * content below. Three tones total across the app:
 *   bgPanel → in-screen panels (Panel tone="base")
 *   bgHover → focused/hero panels + selection rows inside floats
 *   bgActive → floating surfaces only
 *
 * `title` renders as a first-row text (fg = `accent` ?? palette.accent) — the
 * native title anchored on the border, which no longer exists.
 * Selection rows INSIDE a float must highlight with palette.bgHover:
 * palette.bgActive would blend into the surface.
 */

import { Show, splitProps, type JSX } from "solid-js";
import type { BoxProps } from "@opentui/solid";
import { palette } from "../themes";
import { useTerminalDims } from "../lib/terminal-dims";

/** Below this terminal width a centered (absolute) overlay spans the full
 * width (c3-tui 2026-10-21): at 40 columns the 50% palette was 20 columns
 * wide, clipped its rows, and chat text showed at both of its edges. */
export const FULL_WIDTH_OVERLAY_COLS = 60;

/** Does an overlay of this `position` take the full width at `termCols`? */
export function overlaySpansWidth(position: unknown, termCols: number): boolean {
  return position === "absolute" && termCols < FULL_WIDTH_OVERLAY_COLS;
}

export type FloatingProps = Omit<BoxProps, "border" | "borderStyle" | "borderColor" | "focusedBorderColor" | "backgroundColor" | "title" | "titleColor"> & {
  /** Optional first-row caption (was the on-border title). */
  title?: JSX.Element;
  /** Title row accent. Default: palette.accent. */
  accent?: string;
};

export function Floating(props: FloatingProps) {
  const [local, rest] = splitProps(props, ["title", "accent", "children", "width", "left"]);
  const dims = useTerminalDims();
  const full = () => overlaySpansWidth(rest.position, dims().width);
  return (
    <box
      border={false}
      backgroundColor={palette.bgActive}
      {...rest}
      width={full() ? "100%" : local.width}
      left={full() ? 0 : local.left}
    >
      <Show when={local.title}>
        <box height={1} flexShrink={0} overflow="hidden">
          <text fg={local.accent ?? palette.accent}>{local.title}</text>
        </box>
      </Show>
      {local.children}
    </box>
  );
}
