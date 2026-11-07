/**
 * Decorative ASCII banner.
 */

import { createMemo, For } from "solid-js";
import figlet from "figlet";
import { palette } from "../themes";
import { t, fg } from "@opentui/core";

export function Banner(props: { text: string; font?: string; color?: string }) {
  const lines = createMemo(() => {
    try {
      return figlet.textSync(props.text, { font: props.font ?? "Small" }).split("\n");
    } catch {
      return [props.text];
    }
  });

  return (
    <box flexDirection="column">
      <For each={lines()}>
        {(line) => <text children={t`${fg(props.color ?? palette.accent)(line)}`} />}
      </For>
    </box>
  );
}
