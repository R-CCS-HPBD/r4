/**
 * Panel — the standard visual container for the v3 chrome.
 *
 * Box vocabulary (2026-09-29 redesign, ≤3 chrome colors):
 * - No full frame: a single solid ("heavy") bar on the LEFT edge marks the
 *   box, colored by `accent` (focus/category hint from existing theme hues).
 * - Background carries the separation instead — three tones:
 *     "base"   → palette.bgPanel   (default)
 *     "raised" → palette.bgHover   (focused / hero pane)
 *     "user"   → palette.msgUserBg (composer strip — same zone as user msgs)
 * - `title` renders as a first-row text (fg = `titleColor` ?? `accent`),
 *   because the native title sits on the top border, which no longer exists.
 *
 * Floating surfaces (overlays, popups, palettes) draw no lines at all and
 * sit on palette.bgActive — see ui/Floating.tsx.
 */

import { Show, splitProps, type JSX } from "solid-js";
import type { BoxProps } from "@opentui/solid";
import { palette } from "../themes";

export type PanelTone = "base" | "raised" | "user";

export type PanelProps = Omit<BoxProps, "border" | "borderStyle" | "borderColor" | "backgroundColor" | "title" | "titleColor"> & {
  /** First-row caption. */
  title?: JSX.Element;
  /** Left-bar color (category / focus hint). Default: palette.border. */
  accent?: string;
  /** Title row color. Default: `accent`, falling back to palette.accent. */
  titleColor?: string;
  /** Background tone: "base" (bgPanel, default) or "raised" (bgHover). */
  tone?: PanelTone;
};

export function Panel(props: PanelProps) {
  const [local, rest] = splitProps(props, ["title", "accent", "titleColor", "tone", "children"]);
  const bar = () => local.accent ?? palette.border;
  const bg = () =>
    local.tone === "user" ? palette.msgUserBg : local.tone === "raised" ? palette.bgHover : palette.bgPanel;
  const titleFg = () => local.titleColor ?? local.accent ?? palette.accent;

  return (
    <box border={["left"]} borderStyle="heavy" borderColor={bar()} backgroundColor={bg()} {...rest}>
      <Show when={local.title}>
        <box height={1} flexShrink={0} overflow="hidden">
          <text fg={titleFg()}>{local.title}</text>
        </box>
      </Show>
      {local.children}
    </box>
  );
}
