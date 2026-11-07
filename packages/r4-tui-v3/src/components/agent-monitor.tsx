/**
 * Agent Monitor — running / blocked / completed agents.
 * Collapses to a narrow rail when empty (unless Agents focus).
 */

import { For, Show, createMemo } from "solid-js";
import { agentRuntime } from "../stores/agent-runtime";
import { actions, workspace } from "../stores";
import { isDemoVisuals } from "../lib/console-mode";
import { palette, statusColor, statusIcon } from "../themes";
import { truncate } from "../lib/text";
import type { AgentRuntime } from "../types";
import { Panel } from "../ui/Panel";

export function formatAgentMetrics(agent: Pick<AgentRuntime, "progress" | "confidence">): string {
  const parts: string[] = [];
  if (agent.progress !== undefined) {
    parts.push(`Prog ${Math.round(agent.progress)}%`);
  }
  if (agent.confidence !== undefined) {
    parts.push(`Conf ${Math.round(agent.confidence * 100)}%`);
  }
  return parts.join(" · ");
}

/** Empty-state copy — never push Run demo outside demo visuals. */
export function agentsEmptyMessage(demoVisuals = isDemoVisuals()): string {
  return demoVisuals ? "Run demo to see agents" : "No agents yet";
}

export function hasAnyAgents(agents: readonly { state: string }[] = agentRuntime.agents): boolean {
  return agents.length > 0;
}

/** Full panel when agents exist or Agents pane is focused. */
export function agentsPanelExpanded(
  agents: readonly { state: string }[] = agentRuntime.agents,
  focus: string = workspace.researchFocus,
): boolean {
  return hasAnyAgents(agents) || focus === "agents";
}

function AgentCard(props: { agent: AgentRuntime; compact?: boolean }) {
  const stateColor = () => statusColor(props.agent.state);
  const metrics = () => formatAgentMetrics(props.agent);
  const task = () => props.agent.currentTask ?? props.agent.goal;
  const selected = () => workspace.selectedObjectId === `agent:${props.agent.id}`;

  return (
    <box
      flexDirection="column"
      marginBottom={1}
      overflow="hidden"
      width="100%"
      backgroundColor={selected() ? palette.bgActive : undefined}
      onMouseDown={() => actions.selectObject(`agent:${props.agent.id}`)}
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={stateColor()}>
          {truncate(`${statusIcon(props.agent.state)} ${props.agent.role} · ${props.agent.state}`, 28)}
        </text>
      </box>
      <Show when={!props.compact}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{truncate(task(), 28)}</text>
        </box>
      </Show>
      <Show when={metrics()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.statusInfo}>{truncate(metrics(), 28)}</text>
        </box>
      </Show>
    </box>
  );
}

export function AgentMonitor() {
  const runningAgents = createMemo(() =>
    agentRuntime.agents.filter((a) => a.state === "running" || a.state === "planning"),
  );
  const waitingAgents = createMemo(() => agentRuntime.agents.filter((a) => a.state === "waiting"));
  const completedAgents = createMemo(() => agentRuntime.agents.filter((a) => a.state === "completed"));
  const blockedAgents = createMemo(() =>
    agentRuntime.agents.filter((a) => a.state === "blocked" || a.state === "failed"),
  );
  const expanded = createMemo(() =>
    agentsPanelExpanded(agentRuntime.agents, workspace.researchFocus),
  );
  const focused = () => workspace.researchFocus === "agents";

  return (
    <Show
      when={expanded()}
      fallback={
        <Panel
          width={8}
          flexGrow={0}
          flexShrink={0}
          height="100%"
          minHeight={12}
          alignSelf="stretch"
          accent={palette.accent}
          title="Ag"
          flexDirection="column"
          padding={0}
          overflow="hidden"
          onMouseDown={() => actions.setResearchFocus("agents")}
        >
          <box height={1} width="100%" overflow="hidden" paddingLeft={1}>
            <text fg={palette.fgDim}>0</text>
          </box>
        </Panel>
      }
    >
      <Panel
        width="25%"
        flexGrow={0}
        flexShrink={0}
        height="100%"
        minHeight={12}
        alignSelf="stretch"
        accent={focused() ? palette.borderFocus : palette.accent}
        title="Agents"
        tone={focused() ? "raised" : "base"}
        flexDirection="column"
        padding={1}
        overflow="hidden"
      >
        <Show when={focused()}>
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.fgDim}>↑↓ select</text>
          </box>
        </Show>
        <scrollbox flexGrow={1} minHeight={0} stickyScroll stickyStart="top" scrollY overflow="hidden" width="100%">
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.statusInfo}>{`Running (${runningAgents().length})`}</text>
          </box>
          <For each={runningAgents()}>{(agent) => <AgentCard agent={agent} />}</For>
          <Show when={runningAgents().length === 0}>
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.fgDim}>{agentsEmptyMessage()}</text>
            </box>
          </Show>

          <Show when={waitingAgents().length > 0}>
            <box height={1} width="100%" overflow="hidden" marginTop={1}>
              <text fg={palette.statusWarn}>{`Waiting (${waitingAgents().length})`}</text>
            </box>
            <For each={waitingAgents()}>{(agent) => <AgentCard agent={agent} />}</For>
          </Show>

          <Show when={blockedAgents().length > 0}>
            <box height={1} width="100%" overflow="hidden" marginTop={1}>
              <text fg={palette.statusErr}>{`Blocked (${blockedAgents().length})`}</text>
            </box>
            <For each={blockedAgents()}>{(agent) => <AgentCard agent={agent} compact />}</For>
          </Show>

          <Show when={completedAgents().length > 0}>
            <box height={1} width="100%" overflow="hidden" marginTop={1}>
              <text fg={palette.statusOk}>{`Done (${completedAgents().length})`}</text>
            </box>
            <For each={completedAgents().slice(0, 2)}>
              {(agent) => <AgentCard agent={agent} compact />}
            </For>
          </Show>
        </scrollbox>
      </Panel>
    </Show>
  );
}
