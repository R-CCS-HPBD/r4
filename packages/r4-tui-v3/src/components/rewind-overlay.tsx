/**
 * Rewind menu (Claude-Code parity: Esc Esc on an empty prompt, or /rewind).
 * Lists earlier user prompts newest-first; Enter rolls back to the chosen one
 * (it returns to the input; sending replaces the conversation from there).
 */

import { For, createMemo, createSignal } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { workspace, actions, activeTab } from "../stores";
import { palette } from "../themes";
import { rewindCandidates } from "../lib/tui-parity";
import { KeyTrap } from "./key-trap";
import { Floating } from "../ui/Floating";

const VISIBLE_ROWS = 10;

/** ↑/Ctrl+P = -1, ↓/Ctrl+N = +1, anything else 0. */
function navDelta(event: KeyEvent): number {
  if (event.name === "down" || (event.name === "n" && event.ctrl)) return 1;
  if (event.name === "up" || (event.name === "p" && event.ctrl)) return -1;
  return 0;
}

export function RewindOverlay() {
  const [selected, setSelected] = createSignal(0);
  const items = createMemo(() => rewindCandidates(activeTab().chatMessages));
  const safe = createMemo(() => Math.min(selected(), Math.max(0, items().length - 1)));
  const windowStart = createMemo(() => Math.max(0, safe() - VISIBLE_ROWS + 1));
  const visible = createMemo(() => items().slice(windowStart(), windowStart() + VISIBLE_ROWS));

  const close = () => {
    actions.closeRewindMenu();
    setSelected(0);
  };

  const choose = () => {
    const item = items()[safe()];
    setSelected(0);
    if (item) actions.rewindTo(item.index);
  };

  const handleKey = (event: KeyEvent) => {
    const move = navDelta(event);
    if (move !== 0) {
      event.preventDefault();
      setSelected((s) => Math.min(Math.max(s + move, 0), Math.max(0, items().length - 1)));
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      choose();
      return;
    }
    if (event.name === "escape") {
      event.preventDefault();
      close();
    }
  };

  return (
    <Floating
      position="absolute"
      width="70%"
      height={VISIBLE_ROWS + 6}
      left="15%"
      top="15%"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <text fg={palette.accent} height={1}>
        Rewind to an earlier prompt ({items().length})
      </text>
      <box flexDirection="column" width="100%" height={VISIBLE_ROWS} overflow="hidden" marginTop={1}>
        <For each={visible()}>
          {(item, idx) => {
            const isSel = () => windowStart() + idx() === safe();
            return (
              <box height={1} width="100%" backgroundColor={isSel() ? palette.bgHover : undefined}>
                <text fg={isSel() ? palette.fgBright : palette.fg}>
                  {`${isSel() ? "▸" : " "} #${item.index} ${item.preview}`}
                </text>
              </box>
            );
          }}
        </For>
      </box>
      <text fg={palette.fgDim} height={1}>
        Up/Down · Enter rewind here · Esc cancel
      </text>
      <KeyTrap focused={true} captureText onKeyDown={handleKey} />
    </Floating>
  );
}
