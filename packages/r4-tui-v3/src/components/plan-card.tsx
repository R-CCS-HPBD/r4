/**
 * Wave C — plan card (plan mode confirmation).
 * Rendered inline like the approval card; verbs: a / m / e / n (Esc = n).
 * ADR-104 D-1: accepting changes the session mode (a → autopilot, m → chat);
 * the companion switches it and the badge follows the snapshot.
 * The exact keys are on screen — nothing ambiguous at the keyboard.
 */

import { Show } from "solid-js";
import { actions, activeTab } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { chatContentWidth } from "../lib/term-width";
import { respondPlan, type PlanAction } from "../bridge/plan-bridge";
import { Panel } from "../ui/Panel";

export function PlanCard() {
  const respond = (action: PlanAction) => {
    void respondPlan(action);
  };
  return (
    <Panel
      flexDirection="column"
      accent={palette.accent}
      padding={1}
      width="100%"
      overflow="hidden"
      marginBottom={1}
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.accent}>Plan awaiting confirmation</text>
      </box>
      <Show when={activeTab().planPendingRequest.trim().length > 0}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{truncate(activeTab().planPendingRequest, chatContentWidth(6))}</text>
        </box>
      </Show>
      <box height={1} width="100%" overflow="hidden" marginTop={1}>
        <text fg={palette.fgDim}>
          Keys: a = accept, switch to autopilot · m = accept, switch to chat (you approve each tool) · e = edit request · n/Esc = keep planning
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.statusOk} onMouseDown={() => respond("execute_auto")}>
          [a] accept — switch to autopilot and run
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.accent} onMouseDown={() => respond("execute_ask")}>
          [m] accept — switch to chat (manual approval) and run
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text
          fg={palette.fg}
          onMouseDown={() => {
            actions.setPendingChatDraft(activeTab().planPendingRequest);
            void respond("keep_planning");
          }}
        >
          [e] edit the request
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.statusErr} onMouseDown={() => respond("keep_planning")}>
          [n] discard — keep planning
        </text>
      </box>
    </Panel>
  );
}
