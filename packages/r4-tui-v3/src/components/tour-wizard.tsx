/**
 * /tour wizard — a step-by-step walkthrough where the user completes the
 * example project `demo` through real commands. Enter hands the step's
 * command to the composer (you run it — nothing fires hidden), tour advances.
 */

import { For, Show } from "solid-js";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { chatContentWidth } from "../lib/term-width";
import { KeyTrap } from "./key-trap";
import type { KeyEvent } from "@opentui/core";
import { tourStepAt, tourStepCount, tourProgressBar } from "../lib/tour-steps";
import { Floating } from "../ui/Floating";

/** Wrap explanatory text into visible lines. */
function wrap(text: string, width = 92): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= width) cur = next;
    else {
      if (cur) lines.push(cur);
      cur = w;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

export function TourWizardOverlay() {
  const index = () => workspace.tourStep;
  const step = () => tourStepAt(index());
  const count = tourStepCount();

  const isLast = () => index() >= count - 1;

  /** n / →: the next step; on the last step it closes the tour, as the
   * footer says ("Enter/n = close tour"; n was a silent no-op there). */
  const next = () => {
    if (isLast()) actions.closeTour();
    else actions.setTourStep(index() + 1);
  };

  const emit = () => {
    const command = step().command.trim();
    // A step without a command has nothing to hand over: Enter advances
    // like n (it used to close the tour silently on step 1).
    if (!command) return next();
    // Prepend-era grammar: pending draft lands in the composer untouched.
    actions.setPendingChatDraft(step().command);
    actions.sendSystemNotice(
      `tour: command "${truncate(command, chatContentWidth(10))}" is in the composer — press Enter to run it`,
    );
    if (!isLast()) actions.setTourStep(index() + 1);
    actions.closeTour();
  };

  const close = () => actions.closeTour();
  const back = () => actions.setTourStep(Math.max(index() - 1, 0));
  const skip = () => actions.setTourStep(count - 1);
  const KEY_ACTIONS: Record<string, () => void> = {
    escape: close,
    q: close,
    return: emit,
    enter: emit,
    n: next,
    right: next,
    b: back,
    left: back,
    s: skip,
  };

  const handleKey = (event: KeyEvent) => {
    const run = KEY_ACTIONS[event.name ?? ""];
    if (!run) return;
    event.preventDefault();
    run();
  };

  return (
    <Show when={workspace.tourOpen}>
      <Floating
        position="absolute"
        width="72%"
        left="14%"
        top="15%"
        flexDirection="column"
        padding={1}
        gap={1}
        overflow="hidden"
      >
        <box flexDirection="row" height={1} width="100%" overflow="hidden">
          <text fg={palette.accent}>{`r4/umrs guided tour — step ${index() + 1} of ${count}`}</text>
          <box flexGrow={1} />
          <text fg={palette.fgDim}>{tourProgressBar(index())}</text>
        </box>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgBright}>{step().title}</text>
        </box>
        <For each={wrap(step().why)}>
          {(line) => (
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.fg}>{line}</text>
            </box>
          )}
        </For>
        <Show when={step().command.trim()}>
          <box height={1} width="100%" overflow="hidden" backgroundColor={palette.bgHover} onMouseDown={emit}>
            <text fg={palette.accent2}>{`  $ ${truncate(step().command, 84)}`}</text>
          </box>
          <Show when={step().commandHint}>
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.fgDim}>{`  (${step().commandHint})`}</text>
            </box>
          </Show>
        </Show>
        <box height={1} width="100%" overflow="hidden" marginTop={1}>
          <text fg={palette.fgDim}>
            {step().command.trim()
              ? "Enter = put command in composer · n next · b back · s skip to end · Esc quit"
              : isLast()
                ? "Enter/n = close tour · b back · Esc quit"
                : "Enter/n next · b back · s skip to end · Esc quit"}
          </text>
        </box>
        <KeyTrap focused={true} captureText onKeyDown={handleKey} />
      </Floating>
    </Show>
  );
}
