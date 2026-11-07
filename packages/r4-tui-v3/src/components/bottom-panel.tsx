/**
 * Bottom Panel — tabbed container for Timeline/Approvals/Workflow | Strategy | Explain.
 * Tabs switch via alt+shift+1–3. Activity tray (when open) uses 1–5 for its own tabs.
 */

import { For, Show } from "solid-js";
import { ResearchTimeline } from "./research-timeline";
import { ApprovalGates } from "./approval-gates";
import { WorkflowGraphView } from "./workflow-graph-view";
import { StrategyPanel } from "./strategy-panel";
import { BlackboardPanel } from "./blackboard-panel";
import { AutonomousLoopPanel } from "./autonomous-loop-panel";
import { ExplainabilityPanel } from "./explainability-panel";
import { palette } from "../themes";
import { workspace, actions } from "../stores";

type TabId = "ops" | "strategy" | "explain";

const TABS: { id: TabId; label: string }[] = [
  { id: "ops", label: "Ops" },
  { id: "strategy", label: "Strategy" },
  { id: "explain", label: "Explain" },
];

export function BottomPanel() {
  const tab = () => workspace.researchBottomTab;

  return (
    <box
      flexDirection="column"
      width="100%"
      height={14}
      flexShrink={0}
      gap={0}
      overflow="hidden"
    >
      <box flexDirection="row" gap={2} height={1} overflow="hidden">
        <For each={TABS}>
          {(tabItem) => (
            <box
              height={1}
              overflow="hidden"
              backgroundColor={tab() === tabItem.id ? palette.bgActive : undefined}
              onMouseDown={() => {
                actions.setResearchBottomTab(tabItem.id);
                // Keep researchFocus in sync with alt+shift keymap (↑↓ / KeyTrap).
                if (tabItem.id === "ops") actions.setResearchFocus("approvals");
                else if (tabItem.id === "strategy") actions.setResearchFocus("strategy");
                else actions.setResearchFocus("explain");
              }}
            >
              <text fg={tab() === tabItem.id ? palette.fgBright : palette.fgDim}>
                {tabItem.label}
              </text>
            </box>
          )}
        </For>
      </box>

      <box
        flexDirection="row"
        width="100%"
        flexGrow={1}
        alignItems="stretch"
        gap={0}
        overflow="hidden"
      >
        <Show when={tab() === "ops"}>
          <ResearchTimeline />
          <ApprovalGates />
          <WorkflowGraphView />
        </Show>
        <Show when={tab() === "strategy"}>
          <StrategyPanel />
          <BlackboardPanel />
          <AutonomousLoopPanel />
        </Show>
        <Show when={tab() === "explain"}>
          <ExplainabilityPanel />
        </Show>
      </box>
    </box>
  );
}
