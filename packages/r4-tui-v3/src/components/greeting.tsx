/**
 * Boot greeting panel (V2/G2 wave).
 *
 * Framework-level greeting for the empty-chat state (shown ONLY while the
 * conversation has nothing beyond the startup seed message), NOT a splash —
 * the splash already handles launch-brand and self-dismisses. This is the
 * honest idling surface: four lines max, never new chrome, never a second
 * discovery surface (the canonical command index stays the palette).
 */

import { Show } from "solid-js";
import { workspace, activeTab } from "../stores";
import { palette } from "../themes";

export function GreetingPanel() {
  const show = () => activeTab().chatMessages.length <= 1;
  return (
    <Show when={show()}>
      <box
        marginTop={1}
        marginBottom={1}
        width="100%"
        flexDirection="column"
        paddingLeft={1}
        overflow="hidden"
      >
        <box height={1} overflow="hidden">
          <text fg={palette.accent}>welcome to r4 console</text>
        </box>
        <box height={1} overflow="hidden" marginTop={1}>
          <text fg={palette.fgDim}>· type anything to chat — /help (F12) · /tour for a guided run</text>
        </box>
        <box height={1} overflow="hidden">
          <text fg={palette.fgDim}>· Ctrl+P command palette — verbs/chords live there</text>
        </box>
      </box>
    </Show>
  );
}
