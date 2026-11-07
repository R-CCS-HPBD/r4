/**
 * Research Timeline — Phase 7.
 *
 * Compact event list. Single-string rows avoid OpenTUI text overwrite glitches.
 */

import { For, Show, createMemo } from "solid-js";
import { agentRuntime } from "../stores/agent-runtime";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { localTimeHHMMSS } from "../lib/time";
import type { TimelineEntry, TimelineEntryType } from "../types";
import { Panel } from "../ui/Panel";

const entryTypeColors: Record<TimelineEntryType, string> = {
  artifact_upload: palette.infrastructure,
  knowledge_extraction: palette.knowledge,
  graph_update: palette.accent2,
  hypothesis_created: palette.accent,
  experiment_started: palette.tasks,
  experiment_completed: palette.statusOk,
  finding_registered: palette.statusInfo,
  review_completed: palette.knowledge,
  knowledge_correction: palette.statusWarn,
  publication_created: palette.dashboard,
};

const entryTypeIcons: Record<TimelineEntryType, string> = {
  artifact_upload: "↑",
  knowledge_extraction: "✦",
  graph_update: "◇",
  hypothesis_created: "H",
  experiment_started: "E",
  experiment_completed: "✓",
  finding_registered: "F",
  review_completed: "R",
  knowledge_correction: "!",
  publication_created: "▣",
};

export function formatTimelineLine(entry: Pick<TimelineEntry, "timestamp" | "type" | "title">): string {
  const time = localTimeHHMMSS(entry.timestamp);
  const icon = entryTypeIcons[entry.type] ?? "•";
  const label = entry.type.replace(/_/g, " ");
  return `${time} ${icon} ${label} · ${entry.title}`;
}

function TimelineEntryView(props: { entry: TimelineEntry }) {
  const color = () => entryTypeColors[props.entry.type] ?? palette.fg;
  return (
    <box height={1} width="100%" overflow="hidden" marginBottom={0}>
      <text fg={color()}>{truncate(formatTimelineLine(props.entry), 42)}</text>
    </box>
  );
}

export function ResearchTimeline() {
  const timeline = createMemo(() => agentRuntime.timeline);

  return (
    <Panel
      width="33%"
      height="100%"
      flexShrink={0}
      alignSelf="stretch"
      accent={palette.tasks}
      title="Timeline"
      tone="raised"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <scrollbox flexGrow={1} minHeight={0} stickyScroll stickyStart="top" scrollY overflow="hidden" width="100%">
        <For each={timeline().slice(0, 12)}>
          {(entry) => <TimelineEntryView entry={entry} />}
        </For>
        <Show when={timeline().length === 0}>
          <text fg={palette.fgDim}>No timeline events yet.</text>
        </Show>
      </scrollbox>
    </Panel>
  );
}
