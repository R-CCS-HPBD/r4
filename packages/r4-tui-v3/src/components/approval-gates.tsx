/**
 * Approval Gates — Phase 8.
 *
 * Keyboard (no mouse): Tab → Approvals · ↑↓ select · y approve · n reject.
 * Mission keeps a/r/c. Mouse A/R/C still work on cards.
 */

import { For, Show, createMemo } from "solid-js";
import { agentRuntime, agentActions } from "../stores/agent-runtime";
import { workspace, actions, activeTab } from "../stores";
import { answerToolApprovalGate } from "../bridge/approval-bridge";
import {
  isReadinessGate,
  projectIdFromReadinessGate,
} from "../lib/readiness-sync";
import { palette, statusIcon } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import { t, bold, dim } from "@opentui/core";
import { StyledTextView } from "../ui/StyledTextView";
import type { KeyEvent } from "@opentui/core";
import type { ApprovalGate, ApprovalType } from "../types";
import { Panel } from "../ui/Panel";

/** Row cap tracks the real panel width (⅓ of the detail pane) — was fixed 36. */
const gateCap = () => panePanelWidth(0.33);

const approvalTypeIcons: Record<ApprovalType, string> = {
  knowledge_extraction: "✦",
  hypothesis_generation: "H",
  experiment_design: "D",
  experiment_execution: "E",
  publication_draft: "▣",
};

function ApprovalCard(props: { gate: ApprovalGate }) {
  const icon = () => approvalTypeIcons[props.gate.type] ?? "•";
  const selected = () => workspace.selectedApprovalId === props.gate.id;
  const paneFocused = () => workspace.researchFocus === "approvals";

  const settledFromCard = () => {
    agentActions.refreshMissionControl();
    actions.setSelectedApprovalId(null);
  };

  const handleApprove = () => {
    // The mirror of a live tool-approval card answers the companion (or
    // names the tab holding it) — never just the local gate (r2 #13).
    if (answerToolApprovalGate(props.gate.id, "yes")) return settledFromCard();
    if (isReadinessGate(props.gate.objectId)) {
      const fromGate = projectIdFromReadinessGate(props.gate.objectId);
      const projectId = fromGate ?? activeTab().project?.id;
      if (projectId) {
        void actions.dispatchSlashFromChat(
          `/project run ${projectId} --simulate --approve --max-iterations 2`,
        );
      }
    }
    agentActions.resolveApproval(props.gate.id, "approved");
    agentActions.refreshMissionControl();
    actions.setSelectedApprovalId(null);
  };

  const handleReject = () => {
    if (answerToolApprovalGate(props.gate.id, "no")) return settledFromCard();
    agentActions.resolveApproval(props.gate.id, "rejected");
    actions.sendSystemNotice(`[approval] rejected: ${props.gate.title}`);
    agentActions.refreshMissionControl();
    actions.setSelectedApprovalId(null);
  };

  // y/n only — Mission owns a/r/c. Global research.ops.* also handles y/n.
  const handleKey = (event: KeyEvent) => {
    if (!selected() && !paneFocused()) return;
    if (event.name === "y") {
      event.preventDefault();
      handleApprove();
    } else if (event.name === "n") {
      event.preventDefault();
      handleReject();
    }
  };

  return (
    <Panel
      flexDirection="column"
      marginBottom={1}
      accent={selected() ? palette.borderFocus : palette.statusWarn}
      tone={selected() ? "raised" : "base"}
      padding={1}
      onKeyDown={handleKey}
      onMouseDown={() => actions.setSelectedApprovalId(props.gate.id)}
      overflow="hidden"
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.statusWarn}>
          {truncate(
            `${selected() ? "→ " : ""}${icon()} APPROVAL · ${props.gate.type.replace(/_/g, " ")}`,
            gateCap(),
          )}
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fg}>{truncate(props.gate.title, gateCap())}</text>
      </box>
      <Show when={props.gate.description}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{truncate(props.gate.description!, gateCap())}</text>
        </box>
      </Show>
      <box flexDirection="row" gap={2} height={1} marginTop={1} overflow="hidden">
        <box height={1} overflow="hidden" onMouseDown={handleApprove}>
          <text fg={palette.statusOk}>[y] approve</text>
        </box>
        <box height={1} overflow="hidden" onMouseDown={handleReject}>
          <text fg={palette.statusErr}>[n] reject</text>
        </box>
      </box>
    </Panel>
  );
}

function ResolvedApproval(props: { gate: ApprovalGate }) {
  const statusColor = () => {
    switch (props.gate.status) {
      case "approved":
        return palette.statusOk;
      case "rejected":
        return palette.statusErr;
      case "modified":
        return palette.statusWarn;
      default:
        return palette.fgDim;
    }
  };

  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={statusColor()}>
        {truncate(`${statusIcon(props.gate.status)} ${props.gate.title} · ${props.gate.status}`, gateCap())}
      </text>
    </box>
  );
}

export function ApprovalGates() {
  const pendingApprovals = createMemo(() =>
    agentRuntime.approvalGates.filter((g) => g.status === "pending"),
  );
  const resolvedApprovals = createMemo(() =>
    agentRuntime.approvalGates.filter((g) => g.status !== "pending"),
  );
  const focused = () => workspace.researchFocus === "approvals";

  return (
    <Panel
      width="33%"
      height="100%"
      flexShrink={0}
      alignSelf="stretch"
      accent={focused() ? palette.borderFocus : palette.statusWarn}
      title="Approval Gates"
      tone={focused() ? "raised" : "base"}
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <Show when={focused() && pendingApprovals().length > 0}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>↑↓ · y/n</text>
        </box>
      </Show>
      <scrollbox flexGrow={1} minHeight={0} stickyScroll stickyStart="top" scrollY overflow="hidden" width="100%">
        <StyledTextView fg={palette.statusWarn} text={t`${bold("Pending")} (${pendingApprovals().length})`} />
        <For each={pendingApprovals()}>{(gate) => <ApprovalCard gate={gate} />}</For>
        <Show when={pendingApprovals().length === 0}>
          <text fg={palette.fgDim}>No pending approvals.</text>
        </Show>

        <Show when={resolvedApprovals().length > 0}>
          <text fg={palette.fgDim} marginTop={1} children={t`${dim("Resolved:")}`} />
          <For each={resolvedApprovals().slice(0, 5)}>
            {(gate) => <ResolvedApproval gate={gate} />}
          </For>
        </Show>
      </scrollbox>
    </Panel>
  );
}
