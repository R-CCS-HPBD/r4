/**
 * Blackboard Panel — Phase 15.
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For, Show, createMemo } from "solid-js";
import { blackboardStore, blackboardActions } from "../stores/scientific-blackboard";
import { knowledgeStore } from "../stores/knowledge";
import { workspace } from "../stores";
import { isDemoVisuals } from "../lib/console-mode";
import { StateWrapper } from "./state-wrapper";
import { Panel } from "../ui/Panel";
import { BLACKBOARD_EMPTY } from "../lib/empty-hints";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import type { KeyEvent } from "@opentui/core";

/** Row cap tracks the real panel width (34% of the detail pane) — was fixed 40. */
const bbCap = () => panePanelWidth(0.34);

const entryTypeColors: Record<string, string> = {
  observation: palette.statusInfo,
  hypothesis: palette.accent2,
  experiment: palette.tasks,
  question: palette.accent,
  critique: palette.statusWarn,
  finding: palette.statusOk,
  idea: palette.knowledge,
  review: palette.knowledge,
  publication: palette.dashboard,
};

const entryTypeIcons: Record<string, string> = {
  observation: "O",
  hypothesis: "H",
  experiment: "E",
  question: "?",
  critique: "!",
  finding: "F",
  idea: "I",
  review: "R",
  publication: "P",
};

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

export function BlackboardPanel() {
  const recentEntries = createMemo(() => blackboardStore.entries.slice(0, 8));
  const unresolvedCritiques = createMemo(() =>
    blackboardActions.getEntriesWithUnresolvedCritiques().length,
  );
  const demo = createMemo(() => isDemoVisuals());
  const liveKnowledge = createMemo(
    () => workspace.knowledge.length > 0 || Boolean(knowledgeStore.plan),
  );
  const showLive = createMemo(() => demo() || liveKnowledge());

  const handleKey = (event: KeyEvent) => {
    if (!demo()) return;
    if (event.name === "w") {
      blackboardActions.writeEntry({
        type: "observation",
        title: "Test observation from TUI",
        content: "This is a test observation written via the Blackboard panel.",
        authorRole: "literature",
        tags: ["test", "tui"],
      });
    }
  };

  return (
    <Panel
      width="34%"
      accent={palette.knowledge}
      title="Scientific Blackboard"
      tone="raised"
      flexDirection="column"
      padding={1}
      overflow="hidden"
      onKeyDown={handleKey}
    >
      <StateWrapper state={showLive() ? "live" : "empty"} emptyMessage={BLACKBOARD_EMPTY}>
        <Show when={demo()}>
          <box height={1} width="100%" overflow="hidden" onMouseDown={() => {
            blackboardActions.writeEntry({
              type: "observation",
              title: "Test observation from TUI",
              content: "This is a test observation written via the Blackboard panel.",
              authorRole: "literature",
              tags: ["test", "tui"],
            });
          }}>
            <text fg={palette.accent}>[Write] observation · or w</text>
          </box>
        </Show>

        <Show when={!demo() && liveKnowledge()}>
          <Line fg={palette.knowledge}>Project knowledge (live)</Line>
          <Line fg={palette.fgDim}>
            {truncate(
              `${workspace.knowledge.length} KB · ${workspace.tasks.length} tasks · plan ${knowledgeStore.plan?.status ?? "—"}`,
              bbCap(),
            )}
          </Line>
        </Show>

        <Show when={demo()}>
          <Line fg={palette.fgDim}>
            {`Entries: ${blackboardStore.entries.length}  Critiques: ${unresolvedCritiques()}`}
          </Line>

          <Show
            when={recentEntries().length > 0}
            fallback={<Line fg={palette.fgDim}>Blackboard empty. Press w to write (--demo).</Line>}
          >
            <For each={recentEntries()}>
              {(entry) => {
                const color = entryTypeColors[entry.type] ?? palette.fg;
                const icon = entryTypeIcons[entry.type] ?? "?";
                const conf =
                  entry.confidence !== undefined
                    ? ` ${(entry.confidence * 100).toFixed(0)}%`
                    : "";
                return (
                  <Line fg={color}>
                    {truncate(`${icon} ${entry.title}${conf}`, bbCap())}
                  </Line>
                );
              }}
            </For>
          </Show>
        </Show>
      </StateWrapper>
    </Panel>
  );
}
