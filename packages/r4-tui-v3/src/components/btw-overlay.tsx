/**
 * /btw side-question overlay (Claude-Code parity). Shows one exchange at a
 * time out of the 5 newest; [ ] / ←→ / Tab step, x clears, Esc closes. The
 * running turn is never touched — this is a side channel.
 */

import { For, Show, createMemo } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { KeyTrap } from "./key-trap";
import { truncate } from "../lib/text";
import { Floating } from "../ui/Floating";

const ANSWER_ROWS = 14;

function wrapLines(text: string, width: number): string[] {
  const out: string[] = [];
  for (const raw of text.split("\n")) {
    let line = raw;
    while (line.length > width) {
      out.push(line.slice(0, width));
      line = line.slice(width);
    }
    out.push(line);
  }
  return out;
}

const BTW_KEYS: Record<string, () => void> = {
  escape: () => actions.closeBtw(),
  q: () => actions.closeBtw(),
  x: () => actions.clearBtw(),
  "[": () => actions.stepBtw(-1),
  left: () => actions.stepBtw(-1),
  "]": () => actions.stepBtw(1),
  right: () => actions.stepBtw(1),
  tab: () => actions.stepBtw(1),
};

/** [ ] ←/→ Tab/Shift+Tab step · x clears · Esc/q closes. */
function btwKeyAction(event: KeyEvent): (() => void) | null {
  if (event.name === "tab" && event.shift) return () => actions.stepBtw(-1);
  return BTW_KEYS[event.name] ?? null;
}

export function BtwOverlay() {
  const current = createMemo(() => workspace.btw.exchanges[workspace.btw.index]);
  const body = createMemo(() => {
    const ex = current();
    if (!ex) return ["(no side questions yet — /btw <question>)"];
    if (ex.error) return [`error: ${ex.error}`];
    if (ex.answer === null) return ["… answering from the conversation (no tools)"];
    return wrapLines(ex.answer, 96).slice(0, ANSWER_ROWS);
  });

  const handleKey = (event: KeyEvent) => {
    const action = btwKeyAction(event);
    if (!action) return;
    event.preventDefault();
    action();
  };

  return (
    <Floating
      position="absolute"
      width="76%"
      height={ANSWER_ROWS + 8}
      left="12%"
      top="12%"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <text fg={palette.accent} height={1}>
        {`btw — side question ${workspace.btw.index + 1}/${Math.max(1, workspace.btw.exchanges.length)} (not added to the conversation)`}
      </text>
      <Show when={current()}>
        {(ex) => (
          <box height={1} marginTop={1} overflow="hidden">
            <text fg={palette.fgBright}>{`Q: ${truncate(ex().question, 100)}`}</text>
          </box>
        )}
      </Show>
      <box flexDirection="column" height={ANSWER_ROWS} marginTop={1} overflow="hidden">
        <For each={body()}>
          {(line) => (
            <box height={1} overflow="hidden">
              <text fg={palette.fg}>{line}</text>
            </box>
          )}
        </For>
      </box>
      <text fg={palette.fgDim} height={1}>
        [ ] or ←/→ step · x clear · Esc close (the running turn keeps going)
      </text>
      <KeyTrap focused={true} captureText onKeyDown={handleKey} />
    </Floating>
  );
}
