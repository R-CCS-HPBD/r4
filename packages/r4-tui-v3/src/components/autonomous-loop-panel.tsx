/**
 * Autonomous Loop Panel — Phase 15.
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For, Show, createMemo } from "solid-js";
import { loopStore, loopActions } from "../stores/autonomous-loop";
import { isDemoVisuals } from "../lib/console-mode";
import { LOOP_EMPTY } from "../lib/empty-hints";
import { knowledgeStore } from "../stores/knowledge";
import { palette } from "../themes";
import { Panel } from "../ui/Panel";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Row cap tracks the real panel width (⅓ of the detail pane) — was fixed 40/48. */
const loopCap = () => panePanelWidth(0.33);
import type { KeyEvent } from "@opentui/core";
import type { ResearchLoop } from "../types";

const PHASE_COLORS: Record<string, string> = {
  assessing: palette.statusInfo,
  selecting: palette.statusInfo,
  dispatching: palette.statusInfo,
  executing: palette.statusInfo,
  adapting: palette.statusInfo,
  paused_budget: palette.statusWarn,
  paused_approval: palette.statusWarn,
  completed: palette.statusOk,
  failed: palette.statusErr,
};

function phaseColor(phase: string): string {
  return PHASE_COLORS[phase] ?? palette.fgDim;
}

function Line(props: { fg?: string; children: string; marginTop?: number; height?: number }) {
  return (
    <box height={props.height ?? 1} width="100%" overflow="hidden" marginTop={props.marginTop}>
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

/** Live-loop headline + kind line into one row component (keeps panel lean). */
function LoopHeadline(props: {
  liveLoop: ResearchLoop | null;
  phase: string;
  iteration: number;
  maxIterations: number;
}) {
  const live = props.liveLoop;
  const headline = live
    ? `RC1 ${live.simulated ? "sim" : "real"} · ${live.phase ?? "running"} · iter ${live.currentIteration ?? "?"}/${live.maxIterations ?? "?"}${live.totalFindings ? ` · ${live.totalFindings}f` : ""}`
    : `${props.phase}  Iter: ${props.iteration}/${props.maxIterations}`;
  const kind = !live
    ? "kind: local ephemeral strategy loop (demo) — no durable backend"
    : live.simulated
      ? "kind: [sim] RC1 phased — findings are placeholders"
      : "kind: [real] RC1 phased — iterations run durable UMRS workflows";
  return (
    <>
      <Line fg={phaseColor(live ? String(live.phase ?? props.phase) : props.phase)}>
        {truncate(headline, loopCap())}
      </Line>
      <Line fg={palette.fgDim}>{truncate(kind, loopCap())}</Line>
    </>
  );
}

export function AutonomousLoopPanel() {
  const phase = createMemo(() => loopStore.phase);
  const iteration = createMemo(() => loopStore.iteration);
  const maxIterations = createMemo(() => loopStore.maxIterations);
  const history = createMemo(() => loopStore.history.slice(0, 5));
  const lastAdaptation = createMemo(() =>
    loopStore.plannerAdaptations[loopStore.plannerAdaptations.length - 1],
  );
  const demo = createMemo(() => isDemoVisuals());
  const liveLoop = createMemo(() => knowledgeStore.loop);

  const handleKey = (event: KeyEvent) => {
    if (!demo()) return;
    // Avoid s/r — Mission and infra steal letters. Global: i start, x stop, o iter (n = strategy rank).
    if (event.name === "i") {
      loopActions.startLoop();
    } else if (event.name === "x") {
      loopActions.stopLoop();
    } else if (event.name === "o" || event.name === "n") {
      loopActions.runIteration();
    }
  };

  return (
    <Panel
      width="33%"
      accent={palette.tasks}
      title="Workflow builder (local)"
      flexDirection="column"
      padding={1}
      overflow="hidden"
      onKeyDown={handleKey}
    >
      <Show when={demo() || liveLoop()} fallback={<Line fg={palette.fgDim} height={2}>{LOOP_EMPTY}</Line>}>
        <LoopHeadline
          liveLoop={liveLoop()}
          phase={phase()}
          iteration={iteration()}
          maxIterations={maxIterations()}
        />
      </Show>

      <Show when={demo()}>
        <box flexDirection="row" gap={1} height={1} overflow="hidden">
          <box height={1} overflow="hidden" onMouseDown={() => loopActions.startLoop()}>
            <text fg={palette.statusOk}>[Start]</text>
          </box>
          <box height={1} overflow="hidden" onMouseDown={() => loopActions.stopLoop()}>
            <text fg={palette.statusWarn}>[Stop]</text>
          </box>
          <box height={1} overflow="hidden" onMouseDown={() => loopActions.runIteration()}>
            <text fg={palette.accent}>[Iter]</text>
          </box>
          <text fg={palette.fgDim}>i/x/o</text>
        </box>
      </Show>

      <Show when={demo() && lastAdaptation()}>
        <Line fg={palette.accent2} marginTop={1}>
          Last Adaptation:
        </Line>
        <Line>{truncate(lastAdaptation()!.adaptation, loopCap())}</Line>
      </Show>

      <Show when={demo() && history().length > 0}>
        <Line fg={palette.fgDim} marginTop={1}>
          History:
        </Line>
        <For each={history()}>
          {(h) => (
            <Line fg={phaseColor(h.phase)}>
              {truncate(`#${h.iteration} ${h.phase}`, loopCap())}
            </Line>
          )}
        </For>
      </Show>
    </Panel>
  );
}
