/**
 * Agent-detail overlay (T5 topic-6, 2026-09-19).
 *
 * Enter on a work/agents row: full detail on that agent (role / state / goal /
 * budget / permissions / reasoning / evidence) as a floating card, Esc closes.
 * Honest states follow capability detection — slots appear only when the
 * agent's AgentRuntime actually carries them (never fabricated).
 */

import { For, Show, createMemo } from "solid-js";
import { workspace, actions } from "../stores";
import { agentRuntime } from "../stores/agent-runtime";
import { palette } from "../themes";
import { Floating } from "../ui/Floating";

function agentRows(a: import("../types").AgentRuntime): string[] {
  const list = agentCoreLines(a);
  list.push(...agentOptionalLines(a));
  list.push(...agentReasoningLines(a));
  list.push(...agentEvidenceLines(a));
  return list;
}

function agentCoreLines(a: import("../types").AgentRuntime): string[] {
  return [
    `agent    · ${a.role}`,
    `state    · ${a.state}`,
    `goal     · ${a.goal}`,
    `egress   · ${a.permissions.egress}`,
    `fs       · ${a.permissions.filesystem}`,
    `compute  · ${a.permissions.compute}`,
  ];
}

function agentOptionalLines(a: import("../types").AgentRuntime): string[] {
  const out: string[] = [];
  if (a.currentTask) out.push(`task     · ${a.currentTask}`);
  if (a.currentObjective) out.push(`object   · ${a.currentObjective}`);
  const budget = agentBudgetLine(a);
  if (budget) out.push(`budget   · ${budget}`);
  if (a.confidence !== undefined) out.push(`conf     · ${(a.confidence * 100).toFixed(0)}%`);
  if (a.progress !== undefined) out.push(`prog     · ${(a.progress * 100).toFixed(0)}%`);
  if (a.errorMessage) out.push(`error    · ${a.errorMessage}`);
  return out;
}

function agentBudgetLine(a: import("../types").AgentRuntime): string | undefined {
  const parts: string[] = [];
  if (a.budget.maxTokens !== undefined) parts.push(`maxTokens=${a.budget.maxTokens}`);
  if (a.budget.maxIterations !== undefined) parts.push(`maxIterations=${a.budget.maxIterations}`);
  if (a.budget.maxWallClock) parts.push(`maxWallClock=${a.budget.maxWallClock}`);
  if (a.budget.maxGpus !== undefined) parts.push(`maxGpus=${a.budget.maxGpus}`);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

function agentReasoningLines(a: import("../types").AgentRuntime): string[] {
  if (!a.reasoning || a.reasoning.length === 0) return [];
  return a.reasoning.slice(0, 6).map((thought) => `    rationale · ${thought}`);
}

function agentEvidenceLines(a: import("../types").AgentRuntime): string[] {
  if (!a.currentEvidence || a.currentEvidence.length === 0) return [];
  return a.currentEvidence.slice(0, 4).map((ev) => `    evidence · ${ev}`);
}

export function AgentDetailOverlay() {
  const aid = () => workspace.agentDetailId;
  const agent = createMemo(() => {
    const id = aid();
    if (!id) return undefined;
    return agentRuntime.agents.find((a) => a.id === id);
  });

  const rows = createMemo(() => {
    const a = agent();
    return a ? agentRows(a) : [];
  });

  const close = () => actions.setAgentDetailId(null);

  return (
    <Show when={aid()}>
      <Floating
        position="absolute"
        width="68%"
        left="16%"
        top="10%"
        flexDirection="column"
        padding={2}
      >
        <text fg={palette.accent}>agent — Esc closes</text>
        <box height={1} marginTop={1} />
        <For each={rows()}>
          {(row) => (
            <box height={1} overflow="hidden">
              <text fg={palette.fg}>{row}</text>
            </box>
          )}
        </For>
        <box height={1} marginTop={1}>
          <text fg={palette.fgDim}>Esc close</text>
        </box>
      </Floating>
    </Show>
  );
}
