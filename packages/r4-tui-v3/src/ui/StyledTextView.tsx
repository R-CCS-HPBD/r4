/**
 * Reactive-safe styled text (opentui 0.3.4 workaround, pinned 2026-10-01).
 *
 * The solid reconciler APPENDS a reactive StyledText child's chunks to the
 * renderable's text buffer on every re-render instead of replacing them —
 * any `<text children={t`…signal…`}>` (or JSX-child equivalent) duplicates
 * its content once per signal change (the /skills arrow-key duplication).
 *
 * `<StyledTextView text={t`…`}/>` instead assigns the renderable's `content`
 * property inside a render effect: the `content` setter REPLACES the whole
 * buffer, so signal-driven updates stay single while keeping per-chunk
 * colors. Use this for every styled text that reads a signal or prop that
 * can change after mount; plain-string children never needed this guard.
 */

import { createRenderEffect, onMount, splitProps } from "solid-js";
import type { StyledText, RGBA } from "@opentui/core";

interface TextRenderableLike {
  content: StyledText | string;
}

export function StyledTextView(props: {
  text: StyledText | string;
  fg?: string | RGBA;
  marginTop?: number;
  onMouseDown?: () => void;
}) {
  const [local, rest] = splitProps(props, ["text"]);
  let node: TextRenderableLike | undefined;
  // Register the effect in onMount: ref callbacks run first, and reading
  // local.text INSIDE the effect keeps its signal deps tracked (reading it
  // only when the node exists would leave the effect dependency-free).
  onMount(() => {
    createRenderEffect(() => {
      const value = local.text;
      if (node) node.content = value;
    });
  });
  return <text {...rest} ref={(el: TextRenderableLike) => (node = el)} />;
}
