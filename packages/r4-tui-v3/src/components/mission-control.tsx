/**
 * Mission Control — Top-level dashboard
 *
 * Answers: "What is the state of my research project right now?"
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */
import { Show, createMemo } from "solid-js";
import { agentRuntime } from "../stores/agent-runtime";
import { workspace, actions, activeTab } from "../stores";
import { getActiveProjectJobs } from "../commands/project-job-events";
import { isDemoVisuals } from "../lib/console-mode";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Row cap tracks the real pane width — was fixed 72/80. */
const mcCap = () => panePanelWidth(1, 6);
import {
  handleResearchCancelJob,
  handleResearchRunDemo,
  handleResearchStartMenu,
} from "../lib/space-shortcuts";
import type { KeyEvent } from "@opentui/core";
import { Panel } from "../ui/Panel";

function Action(props: { label: string; fg?: string; onClick: () => void }) {
  return (
    <box height={1} overflow="hidden" onMouseDown={props.onClick}>
      <text fg={props.fg ?? palette.accent}>{props.label}</text>
    </box>
  );
}

export function MissionControl() {
  const mc = createMemo(() => agentRuntime.missionControl);
  const demo = createMemo(() => isDemoVisuals());
  const activeAgentCount = createMemo(() => mc().activeAgents.length);
  const pendingApprovalCount = createMemo(() => mc().pendingApprovals.length);
  const budgetRemaining = createMemo(() => mc().computeBudget.remaining);
  const budgetRunway = createMemo(() => mc().computeBudget.estimatedRunway);
  const successProgress = createMemo(() => mc().successCriteriaProgress);
  const activeJobs = createMemo(() => [...getActiveProjectJobs().entries()]);
  const hypCount = createMemo(() => mc().hypotheses.length);

  const projectLine = createMemo(() => {
    const p = activeTab().project;
    if (!p) return "Project: (no project) — press A → New";
    const name = p.name && p.name !== p.id ? ` · ${p.name}` : "";
    return truncate(`Project: ${p.id}${name}`, mcCap());
  });

  const budgetColor = createMemo(() => {
    const runway = budgetRunway();
    if (runway >= 14) return palette.statusOk;
    if (runway >= 7) return palette.statusWarn;
    return palette.statusErr;
  });
  const progressColor = createMemo(() => {
    const p = successProgress();
    if (p >= 80) return palette.statusOk;
    if (p >= 50) return palette.statusWarn;
    return palette.statusInfo;
  });
  const goalLine = createMemo(() => {
    const goal = mc().researchGoal || "No research goal set";
    return truncate(`Goal: ${goal}`, mcCap());
  });
  const successLine = createMemo(() => {
    const p = successProgress();
    const w = 15;
    const filled = Math.floor((Math.min(100, p) / 100) * w);
    const bar = `${"█".repeat(filled)}${"░".repeat(w - filled)}`;
    return `Success: ${bar} ${p.toFixed(0)}%`;
  });
  const statsLine = createMemo(() => {
    const parts = [
      `Agents: ${activeAgentCount()}`,
      `Approvals: ${pendingApprovalCount()}`,
    ];
    if (hypCount() > 0) parts.push(`Hypotheses: ${hypCount()}`);
    if (demo()) {
      parts.unshift(`Budget: ${budgetRemaining()} GPU-hrs`);
    }
    const jobs = activeJobs();
    if (jobs[0]) {
      parts.push(`Job: ${jobs[0][0].slice(0, 8)}… (${jobs[0][1]})`);
    }
    const blockers = mc().blockers;
    if (blockers.length > 0) {
      parts.push(`Blockers: ${blockers.length}`);
    }
    return truncate(parts.join(" · "), 80);
  });

  const handleKey = (event: KeyEvent) => {
    if (event.name === "a" && !event.ctrl && !event.meta) {
      if (handleResearchStartMenu()) event.preventDefault();
      return;
    }
    if (event.name === "r" && !event.ctrl && !event.meta) {
      if (handleResearchRunDemo()) event.preventDefault();
      return;
    }
    if (event.name === "c" && !event.ctrl && !event.meta) {
      if (handleResearchCancelJob()) event.preventDefault();
    }
  };

  return (
    <Panel
      width="100%"
      height={demo() ? 6 : 5}
      accent={palette.accent}
      title="Mission Control"
      flexDirection="column"
      padding={0}
      overflow="hidden"
      onKeyDown={handleKey}
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgBright}>{projectLine()}</text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.accent}>{goalLine()}</text>
      </box>
      <Show when={demo()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={progressColor()}>{successLine()}</text>
        </box>
      </Show>
      <box height={1} width="100%" overflow="hidden">
        <text fg={demo() ? budgetColor() : palette.fgDim}>{statsLine()}</text>
      </box>
      <box flexDirection="row" height={1} width="100%" overflow="hidden" gap={2}>
        <Action label="[A]ctions" onClick={() => actions.openStartActionsMenu()} />
        <Show when={demo()}>
          <Action
            label="[R]un demo"
            onClick={() => {
              handleResearchRunDemo();
            }}
          />
        </Show>
        <Action
          label="[C]ancel"
          fg={palette.statusErr}
          onClick={() => {
            handleResearchCancelJob();
          }}
        />
        <box height={1} overflow="hidden">
          <text fg={palette.fgDim}>Tab cycle panes</text>
        </box>
      </box>
    </Panel>
  );
}
