/**
 * Agent activity strip — also the affordance to open/close the Activity tray.
 */

import { For, Show, createMemo } from "solid-js";
import { workspace, actions } from "../stores";
import { agentRuntime } from "../stores/agent-runtime";
import { isDemoVisuals } from "../lib/console-mode";
import { palette, statusColor } from "../themes";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";

/** Per-agent share of the full-width strip (3 visible + hint) — was fixed 36. */
const agentCap = () => Math.max(36, Math.floor(termWidth() / 3) - 8);

export function AgentActivityStrip() {
  const active = createMemo(() => {
    const fromRuntime = agentRuntime.agents.filter(
      (a) => a.state === "running" || a.state === "planning" || a.state === "waiting",
    );
    if (fromRuntime.length > 0) {
      return fromRuntime.map((a) => ({
        id: a.id,
        label: a.role,
        detail: a.currentTask ?? a.currentObjective ?? a.state,
        status: a.state,
      }));
    }
    return isDemoVisuals()
      ? workspace.agents
          .filter((a) => a.status !== "idle")
          .map((a) => ({
            id: a.id,
            label: a.name,
            detail: a.currentTask ?? a.status,
            status: a.status,
          }))
      : [];
  });

  // Cap visible agents so the strip does not overflow into unreadable mush.
  const visible = () => active().slice(0, 3);

  const idleHint = () =>
    workspace.activityOpen ? "F9 close Activity" : "No active agents · F9 Activity";

  return (
    <box
      width="100%"
      height={1}
      flexDirection="row"
      gap={2}
      paddingLeft={1}
      paddingRight={1}
      overflow="hidden"
      onMouseDown={() => actions.toggleActivityConsole()}
    >
      <Show when={isDemoVisuals() && active().length > 0}>
        <text fg={palette.fgDim}>[demo]</text>
      </Show>
      <Show when={active().length > 0} fallback={<text fg={palette.fgDim}>{idleHint()}</text>}>
        <For each={visible()}>
          {(agent) => (
            <text fg={statusColor(agent.status)}>
              {truncate(`● ${agent.label}: ${agent.detail}`, agentCap())}
            </text>
          )}
        </For>
        <Show when={active().length > 3}>
          <text fg={palette.fgDim}>{`+${active().length - 3} more`}</text>
        </Show>
        <text fg={palette.fgDim}>
          {workspace.activityOpen ? " · F9 close" : " · F9"}
        </text>
      </Show>
    </box>
  );
}
