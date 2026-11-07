/**
 * Team-detail overlay (T5 topic-1, 2026-09-19; live roster rewrite 2026-09-27).
 *
 * Enter on a teams row: full detail on that session — a live roster where the
 * user sees each agent's role, model, status and latest pushed output, and can
 * click an agent to read the VERBATIM prompt it runs under (its source named:
 * "default role prompt" or "spec override"). Esc closes.
 *
 * Honesty rules (unchanged): rows come only from the polled status and the
 * pushed team_event rings; absent fields leave no text (no fabricated slots);
 * a long prompt is clipped with an explicit note, never silently cut.
 */

import { useTerminalDims } from "../lib/terminal-dims";
import { For, Show, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { workspace, actions } from "../stores";
import { teamsStore, teamsActions, type TeamAgentRow } from "../stores/teams";
import {
  liveAgentsFromRing,
  activityRowsFor,
  clipOneLine,
  finalMessageFor,
  wrapLines,
} from "../lib/team-live";
import { palette, statusIcon } from "../themes";
import { Floating } from "../ui/Floating";

/** Progress bar for the whole run (whole-team pace, honest: iterations only). */
export function progressBar(iteration: number, max: number, width = 12): string {
  if (max <= 0) return "";
  const filled = Math.round((Math.min(iteration, max) / max) * width);
  return `▰`.repeat(filled) + `▱`.repeat(width - filled);
}

const RUNNING_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"];

/** Animated frame index for a currently-working agent (rotates over time). */
export function runningFrame(tick: number): string {
  return RUNNING_FRAMES[Math.abs(tick) % RUNNING_FRAMES.length]!;
}

const PROMPT_LINE_CAP = 26;

/** Drawable text width inside the 72%-wide overlay: border (2) + padding
 * (4) + this panel's indent. Never wider than the real char cell width —
 * rows must not clip mid-content (2026-09-29 user report). */
function wrapWidthFor(dimsWidth: number | undefined, indent: number): number {
  return Math.max(24, Math.floor((dimsWidth ?? 80) * 0.72) - 6 - indent);
}

function rosterLine(props: {
  id: string;
  state: TeamAgentRow;
  liveStatus?: string;
  selected: boolean;
  expanded: boolean;
  tick: number;
}): string {
  const status = props.liveStatus ?? props.state.status;
  const frame =
    status === "thinking" ? runningFrame(props.tick) : statusIcon(status);
  const bits = [
    `${props.expanded ? "▾" : props.selected ? "▸" : " "} ${frame} ${props.id} · ${status}`,
  ];
  if (props.state.model) bits[0] += ` · ${props.state.model}`;
  if (props.state.messages_sent !== undefined) bits[0] += ` · sent ${props.state.messages_sent}`;
  return bits[0];
}

function AgentRosterRow(props: {
  id: string;
  state: TeamAgentRow;
  liveStatus?: string;
  lastPreview?: string;
  selected: boolean;
  expanded: boolean;
  tick: number;
  onSelect: () => void;
}) {
  const errors = () => (props.state.errors ?? []).length;
  const summary = () => {
    const bits: string[] = [];
    if (props.state.current_task) bits.push(clipOneLine(props.state.current_task, 48));
    if (props.lastPreview !== undefined) bits.push(clipOneLine(props.lastPreview, 72));
    if (errors() > 0) {
      const last = (props.state.errors ?? [])[errors() - 1];
      bits.push(`${last ? `last error: ${clipOneLine(last, 72)}` : `errors ${errors()}`}`);
    }
    return bits.join(" · ");
  };
  return (
    <box flexDirection="column">
      {/* Click target on the header row ONLY (same selection law as the
          details expander): the summary text below must stay drag-copyable. */}
      <box height={1} overflow="hidden" onMouseDown={props.onSelect}>
        <text fg={props.selected ? palette.fgBright ?? palette.fg : palette.fg}>
          {rosterLine(props)}
        </text>
      </box>
      <Show when={summary()}>
        <box height={1} overflow="hidden">
          <text fg={palette.fgDim}>{`    ${summary()}`}</text>
        </box>
      </Show>
    </box>
  );
}

/** One activity fact as wrapped rows — the full preview, every character
 * (the bridge already bounds what it carries; nothing silently cut). */
function ActivityRow(props: { line: ReturnType<typeof activityRowsFor>[number] }) {
  const dims = useTerminalDims();
  const dur = () =>
    props.line.duration_ms ? ` ${(props.line.duration_ms / 1000).toFixed(1)}s` : "";
  const glyph = () =>
    props.line.activity_kind === "tool_call" ? "▸"
    : props.line.activity_kind === "tool_result" ? "↩"
    : props.line.activity_kind === "thinking" ? "·"
    : "▸";
  const head = () => `      ${glyph()} ${props.line.name ? `${props.line.name} ` : ""}`;
  const body = () =>
    wrapLines((props.line.preview ?? "") + dur(), wrapWidthFor(dims().width, 8));
  return (
    <box flexDirection="column">
      <box height={1} overflow="hidden">
        <text fg={palette.fgDim}>{head()}</text>
      </box>
      <For each={body()}>
        {(row) => (
          <box height={1} overflow="hidden">
            <text fg={palette.fgDim}>{`        ${row}`}</text>
          </box>
        )}
      </For>
    </box>
  );
}

function ActivityPanel(props: { role: string; sid: string }) {
  const rows = createMemo(() =>
    activityRowsFor(teamsStore.live[props.sid] ?? [], props.role, 12),
  );
  return (
    <Show when={rows().length > 0}>
      <box flexDirection="column" marginTop={1}>
        <text fg={palette.accent2}>{`    work — ${props.role} (thinking · tools · responses)`}</text>
        <For each={rows()}>{(line) => <ActivityRow line={line} />}</For>
      </box>
    </Show>
  );
}

/** The role's latest full answer — the conclusion users asked to READ at
 * the end of a run (2026-09-29), wrapped whole from the live ring. */
function FinalAnswerPanel(props: { role: string; sid: string }) {
  const dims = useTerminalDims();
  const finalText = createMemo(() =>
    finalMessageFor(teamsStore.live[props.sid] ?? [], props.role),
  );
  return (
    <Show when={finalText() !== undefined}>
      <box flexDirection="column" marginTop={1}>
        <text fg={palette.accent2}>{`    final answer — ${props.role}`}</text>
        <For each={wrapLines(finalText() ?? "", wrapWidthFor(dims().width, 6))}>
          {(row) => (
            <box height={1} overflow="hidden">
              <text fg={palette.fg}>{`      ${row}`}</text>
            </box>
          )}
        </For>
      </box>
    </Show>
  );
}

function PromptPanel(props: { role: string; state: TeamAgentRow; crewName?: string }) {
  const dims = useTerminalDims();
  const sourceLabel = () => {
    if (props.state.prompt_source !== "override") return "default role prompt";
    return props.crewName ? `${props.crewName} crew (tailored or pack baseline)` : "spec override";
  };
  const promptLines = createMemo(() => {
    const prompt = props.state.prompt;
    if (!prompt) return undefined;
    const lines = prompt
      .split("\n")
      .flatMap((line) => wrapLines(line, wrapWidthFor(dims().width, 6)));
    const clipped = lines.length > PROMPT_LINE_CAP;
    return {
      lines: clipped ? lines.slice(0, PROMPT_LINE_CAP) : lines,
      clipped,
      total: lines.length,
    };
  });
  return (
    <Show when={promptLines()} fallback={
      <box height={1}>
        <text fg={palette.fgDim}>
          {`    prompt — not carried by this bridge (run status update pending)`}
        </text>
      </box>
    }>
      {(lines) => (
        <box flexDirection="column" marginTop={1}>
          <text fg={palette.accent2}>
            {`    prompt — ${props.role} (${sourceLabel()})`}
          </text>
          <For each={lines().lines}>
            {(line) => (
              <box height={1} overflow="hidden">
                <text fg={palette.fgDim}>{`      ${line}`}</text>
              </box>
            )}
          </For>
          <Show when={lines().clipped}>
            <text fg={palette.fgDim}>
              {`      … (${lines().total - PROMPT_LINE_CAP} more lines — shown verbatim up to this point)`}
            </text>
          </Show>
        </box>
      )}
    </Show>
  );
}

export function TeamDetailOverlay() {
  const sid = () => workspace.teamDetailId;
  const status = () => (sid() ? teamsStore.status[sid()!] : undefined);

  /** Selection state lives in the workspace: the GLOBAL keymap drives cursor
   * + expansion (overlay verbs were KeyTrap-dead with the composer focused,
   * reported 2026-09-27), and the overlay stays a passive render surface. */
  const cursor = () => workspace.teamDetailCursor;
  const expandedRole = () => workspace.teamDetailExpanded;

  /** Animation tick for the running-agent frames (only while open). */
  const [tick, setTick] = createSignal(0);
  onMount(() => {
    const timer = setInterval(() => setTick((t) => t + 1), 250);
    onCleanup(() => clearInterval(timer));
  });

  const liveByRole = createMemo(() => {
    const id = sid();
    return new Map(
      liveAgentsFromRing(id ? (teamsStore.live[id] ?? []) : []).map((a) => [a.role, a]),
    );
  });

  const toggleRole = (role: string) => {
    actions.teamOverlayToggleExpanded(role);
  };

  const header = createMemo(() => {
    const s = status();
    if (!s) return undefined;
    const bar = progressBar(s.iteration, s.max_iterations);
    return [
      `${s.name}`,
      `status: ${s.status}${bar ? `  ${bar} iter ${s.iteration}/${s.max_iterations}` : ` — iteration: ${s.iteration} / ${s.max_iterations}`}`,
    ];
  });

  const roster = createMemo(() => {
    const entries = Object.entries(status()?.agents ?? {});
    const pos = entries.length ? cursor() % entries.length : 0;
    return { entries, selectedIndex: pos };
  });

  return (
    <Show when={sid()}>
      <Floating
        position="absolute"
        width="72%"
        left="14%"
        top="8%"
        flexDirection="column"
        padding={2}
      >
        <text fg={palette.accent}>teamup session — Esc closes · j/k select · enter expands (final · prompt · work · click works too)</text>
        <box height={1} marginTop={1} />
        <Show when={header()} fallback={<text fg={palette.fgDim}>loading status…</text>}>
          <For each={header()!}>
            {(line) => (
              <box height={1} overflow="hidden">
                <text fg={palette.fg}>{line}</text>
              </box>
            )}
          </For>
        </Show>
        <box height={1} marginTop={1}>
          <text fg={palette.accent2}>agents</text>
        </box>
        <scrollbox flexGrow={1} scrollY overflow="hidden">
          <For each={roster().entries}>
            {([role, state], index) => (
              <box flexDirection="column">
                <AgentRosterRow
                  id={role}
                  state={state}
                  liveStatus={liveByRole().get(role)?.status}
                  lastPreview={liveByRole().get(role)?.lastPreview}
                  selected={index() === roster().selectedIndex}
                  expanded={expandedRole() === role}
                  tick={tick()}
                  onSelect={() => toggleRole(role)}
                />
                <Show when={expandedRole() === role}>
                  <FinalAnswerPanel role={role} sid={sid()!} />
                  <PromptPanel role={role} state={state} crewName={status()?.crew_pack ?? undefined} />
                  <ActivityPanel role={role} sid={sid()!} />
                </Show>
              </box>
            )}
          </For>
        </scrollbox>
        <box height={1} marginTop={1}>
          <text fg={palette.fgDim}>r run · p pause · R resume · c cancel · l logs · o report · m tailor · O agent tab · Esc close</text>
        </box>
      </Floating>
    </Show>
  );
}

export function refreshTeamDetailIfOpen(sid: string): void {
  if (workspace.teamDetailId === sid) {
    void teamsActions.status(sid);
  }
}
