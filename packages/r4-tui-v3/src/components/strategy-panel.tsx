/**
 * Strategy Panel — Phase 15.
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For, Show, createMemo } from "solid-js";
import { strategyStore, strategyActions } from "../stores/research-strategy";
import { knowledgeStore } from "../stores/knowledge";
import { workspace } from "../stores";
import { isDemoVisuals } from "../lib/console-mode";
import { StateWrapper } from "./state-wrapper";
import { STRATEGY_EMPTY } from "../lib/empty-hints";
import { palette } from "../themes";
import { Panel } from "../ui/Panel";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Row cap tracks the real panel width (34% of the detail pane) — was fixed 40. */
const stratCap = () => panePanelWidth(0.34);
import type { KeyEvent } from "@opentui/core";

function scoreColor(score: number): string {
  if (score >= 0.8) return palette.statusOk;
  if (score >= 0.5) return palette.statusWarn;
  return palette.statusErr;
}

function Line(props: { fg?: string; children: string; marginTop?: number }) {
  return (
    <box height={1} width="100%" overflow="hidden" marginTop={props.marginTop}>
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

export function StrategyPanel() {
  const assessment = createMemo(() => strategyStore.currentAssessment);
  const candidates = createMemo(() => strategyActions.getActionCandidates().slice(0, 5));
  const budget = createMemo(() => strategyStore.budgetAllocation);
  const demo = createMemo(() => isDemoVisuals());
  const livePlan = createMemo(() => knowledgeStore.plan);
  const showLive = createMemo(() => demo() || Boolean(livePlan()));

  const handleKey = (event: KeyEvent) => {
    if (!demo()) return;
    // Avoid r — Mission [R]un steals it. Use e / n when panel focused.
    if (event.name === "e") {
      strategyActions.evaluateStrategy();
    } else if (event.name === "n") {
      strategyActions.rankActions();
    }
  };

  return (
    <Panel
      width="34%"
      accent={workspace.researchFocus === "strategy" ? palette.borderFocus : palette.border}
      title="Strategy Engine"
      titleColor={palette.accent}
      tone={workspace.researchFocus === "strategy" ? "raised" : "base"}
      flexDirection="column"
      padding={1}
      overflow="hidden"
      onKeyDown={handleKey}
    >
      <StateWrapper state={showLive() ? "live" : "empty"} emptyMessage={STRATEGY_EMPTY}>
        <Show when={demo()}>
          <box flexDirection="row" gap={2} height={1} overflow="hidden">
            <box height={1} overflow="hidden" onMouseDown={() => strategyActions.rankActions()}>
              <text fg={palette.accent}>[Rank]</text>
            </box>
            <box height={1} overflow="hidden" onMouseDown={() => strategyActions.evaluateStrategy()}>
              <text fg={palette.accent}>[Eval]</text>
            </box>
            <text fg={palette.fgDim}>n/e (global on Strategy tab)</text>
          </box>
        </Show>

        <Show when={!demo() && livePlan()}>
          <Line fg={palette.accent2}>Plan (live)</Line>
          <Line>{truncate(livePlan()!.researchGoal, 40)}</Line>
          <Line fg={palette.fgDim}>
            {truncate(
              `Status ${livePlan()!.status} · ${livePlan()!.hypotheses?.length ?? 0} hyp · ${livePlan()!.experiments?.length ?? 0} exp`,
              40,
            )}
          </Line>
        </Show>

        <Show when={demo()}>
          <Show
            when={assessment()}
            fallback={<Line fg={palette.fgDim}>No assessment yet. Click [Eval] or press e.</Line>}
          >
            <Line fg={scoreColor(assessment()!.overallScore)}>
              {`Score: ${(assessment()!.overallScore * 100).toFixed(0)}%  Risk: ${assessment()!.riskLevel}`}
            </Line>

            <Show when={assessment()!.strengths.length > 0}>
              <Line fg={palette.statusOk} marginTop={1}>
                Strengths:
              </Line>
              <For each={assessment()!.strengths.slice(0, 3)}>
                {(s) => <Line fg={palette.statusOk}>{truncate(`+ ${s}`, stratCap())}</Line>}
              </For>
            </Show>

            <Show when={assessment()!.weaknesses.length > 0}>
              <Line fg={palette.statusErr} marginTop={1}>
                Weaknesses:
              </Line>
              <For each={assessment()!.weaknesses.slice(0, 3)}>
                {(w) => <Line fg={palette.statusErr}>{truncate(`- ${w}`, stratCap())}</Line>}
              </For>
            </Show>
          </Show>

          <Show
            when={candidates().length > 0}
            fallback={<Line fg={palette.fgDim} marginTop={1}>No candidates. Click [Rank] or press n.</Line>}
          >
            <Line fg={palette.accent2} marginTop={1}>
              Top Actions:
            </Line>
            <For each={candidates()}>
              {(c) => (
                <Line fg={scoreColor(c.score)}>
                  {truncate(`${(c.score * 100).toFixed(0)}% ${c.actionType.replace(/_/g, " ")}`, stratCap())}
                </Line>
              )}
            </For>
          </Show>

          <Show
            when={budget()}
            fallback={<Line fg={palette.fgDim} marginTop={1}>No budget allocation.</Line>}
          >
            <Line fg={palette.tasks} marginTop={1}>
              Budget:
            </Line>
            <For each={budget()!.allocations.slice(0, 3)}>
              {(a) => (
                <Line fg={palette.fgDim}>
                  {truncate(`${a.agentRole} ${a.allocated} GPU-hrs`, stratCap())}
                </Line>
              )}
            </For>
          </Show>
        </Show>
      </StateWrapper>
    </Panel>
  );
}
