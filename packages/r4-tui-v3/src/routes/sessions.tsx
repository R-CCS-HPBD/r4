/**
 * Wave D — /sessions pane view: server-side session records with pane-scoped
 * verbs (Tab in, Enter resumes, n new, r rename → seeds draft, d archive,
 * p pin, x export). Resume replays from the server, never client memory.
 *
 * P2/P3 (plan 2026-09-09): filter chips (s state / v venue / A archives,
 * riding the wire), venue glyph + state chip + sync badge per row, archived
 *  rows stay visible with dim styling until the next refetch, remote-only
 *  rows render with their badge and local-row actions disabled-with-reason
 *  (resume excepted — bridge pull-fallback lands remote-only rows first).
 */

import { For, Show } from "solid-js";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { paneContentWidth } from "../lib/term-width";
import { paneBounds } from "../lib/pane-measure";
import { PANE_CHROME_COLS, sessionRowCells } from "../lib/session-row";
import {
  archiveSession,
  exportSession,
  fetchSessions,
  pinSession,
} from "../bridge/session-bridge";
import { isRemoteOnly, venueGlyph, venueWord } from "../lib/session-filters";
import type { SessionRecord } from "../types";

/** Inner columns of the docked pane: its MEASURED box (the percentage
 * estimate lays out narrower — D-2), the estimate only before the first
 * measurement. */
function paneInnerCols(): number {
  const measured = paneBounds().width;
  return measured > 0 ? measured - PANE_CHROME_COLS : paneContentWidth(PANE_CHROME_COLS);
}

function stateChipColor(state: string | undefined): string {
  switch (state) {
    case "ongoing":
      return palette.statusOk;
    case "completed":
      return palette.statusInfo;
    case "error":
      return palette.statusErr;
    case "stopped":
      return palette.fgDim;
    default:
      return palette.fgDim;
  }
}

function syncBadgeColor(sync: SessionRecord["sync"]): string {
  switch (sync) {
    case "remote-only":
      return palette.statusWarn;
    case "degraded":
      return palette.statusErr;
    case "mirrored":
      return palette.fgDim;
    default:
      return palette.fgDim;
  }
}

/** Remote-only rows: resume is ALLOWED (bridge pull-fallback pulls the row
 *  into the local store server-side); rename/pin/archive/export still need a
 *  local row. Mentions resume honestly instead of implying a block. */
export function remoteOnlyReasonLine(): string {
  return "remote-only (server journal) — resume pulls from journal · rename/pin/archive/export need a local row";
}

export function SessionsRoute() {
  const selected = () => workspace.sessionsList[workspace.sessionsCursor];
  /** Selected row gated to local-usable rows (remote-only = no local verbs). */
  const selectedLocal = () => {
    const record = selected();
    return record && !isRemoteOnly(record) ? record : undefined;
  };
  const filters = () => workspace.sessionFilters;
  const remote = () => workspace.sessionsRemote;

  const cycleState = () => {
    actions.cycleSessionStateFilter();
    void fetchSessions();
  };
  const cycleVenue = () => {
    actions.cycleSessionVenueFilter();
    void fetchSessions();
  };
  const toggleArchives = () => {
    actions.toggleSessionArchived();
    void fetchSessions();
  };

  return (
    <box flexDirection="column" overflow="hidden" flexGrow={1}>
      {/* Filter chips — each shows its key hint AND the active value
          (console honesty: chips always show the active filter). */}
      <box height={1} width="100%" overflow="hidden" flexDirection="row" gap={2}>
        <text flexShrink={0} fg={palette.statusOk} onMouseDown={() => actions.openVenuePicker()}>
          [n] new
        </text>
        <text flexShrink={0} fg={filters().state !== "all" ? palette.accent : palette.fgDim} onMouseDown={cycleState}>
          {`[s] state: ${filters().state}`}
        </text>
        <text flexShrink={0} fg={filters().venue !== "all" ? palette.accent : palette.fgDim} onMouseDown={cycleVenue}>
          {`[v] venue: ${filters().venue}`}
        </text>
        <text
          flexShrink={0}
          fg={filters().includeArchived ? palette.accent : palette.fgDim}
          onMouseDown={toggleArchives}
        >
          {`[A] archives: ${filters().includeArchived ? "on" : "off"}`}
        </text>
        <Show when={remote().available === false}>
          <text flexShrink={0} fg={palette.statusWarn}>
            {truncate(`remote: unavailable${remote().reason ? ` (${remote().reason})` : ""}`, 40)}
          </text>
        </Show>
        <Show when={remote().available === true}>
          <text flexShrink={0} fg={palette.fgDim}>remote: ok</text>
        </Show>
      </box>
      <Show
        when={workspace.sessionsList.length > 0}
        fallback={
          <box flexDirection="column" padding={1}>
            <text fg={palette.fgDim}>(no sessions match the active filters)</text>
            <text fg={palette.fgDim} marginTop={1}>
              start chatting — sessions persist server-side; n = new session (asks venue)
            </text>
          </box>
        }
      >
        <For each={workspace.sessionsList}>
          {(record, idx) => <SessionRow record={record} focused={idx() === workspace.sessionsCursor} />}
        </For>
      </Show>
      {/* Footer: verbs follow the same interaction grammar as pane rows. */}
      <Show when={selected()}>
        {(record) => (
          <box flexDirection="column" marginTop={1} paddingTop={1} overflow="hidden">
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.accent2}>
                {truncate(
                  `${record().id.slice(0, 8)} — ${record().model} · venue: ${venueWord(record().venue)}${record().state ? ` · ${record().state}` : ""}${record().sync ? ` · ${record().sync}` : ""}`,
                  paneInnerCols(),
                )}
              </text>
            </box>
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.fgDim}>
                {isRemoteOnly(record())
                  ? remoteOnlyReasonLine()
                  : "Tab in · ↵ resume · n new · r rename · p pin · d archive · x export · s state · v venue · A archives"}
              </text>
            </box>
          </box>
        )}
      </Show>
      {/* Verb rows are clickable too (mouse grammar == keyboard grammar). */}
      <Show when={selectedLocal()}>
        {(record) => (
          <box flexDirection="row" gap={2} marginTop={0} overflow="hidden">
            <text fg={palette.statusOk} onMouseDown={() => void actions.tabOpenSession(record().id, record().name)}>
              ↵ resume
            </text>
            <text
              fg={palette.accent}
              onMouseDown={() => {
                actions.setPendingChatDraft(`/sessions rename ${record().id.slice(0, 8)} `);
              }}
            >
              r rename
            </text>
            <text fg={palette.accent} onMouseDown={() => void pinSession(record().id, !record().pinned)}>
              {record().pinned ? "p unpin" : "p pin"}
            </text>
            <text fg={palette.statusWarn} onMouseDown={() => void archiveSession(record().id)}>
              d archive
            </text>
            <text fg={palette.fg} onMouseDown={() => void exportSession(record().id)}>
              x export
            </text>
            <Show when={record().venue === "frontend"}>
              <text
                fg={palette.statusErr}
                onMouseDown={() =>
                  void import("../bridge/venue-bridge").then((m) => m.stopRemoteSession(record().id))
                }
              >
                t stop remote
              </text>
            </Show>
          </box>
        )}
      </Show>
    </box>
  );
}

/** One session row; every cell is sized from the measured pane (D-2) and
 * never shrinks, so cells cannot paint over each other. */
function SessionRow(props: { record: SessionRecord; focused: boolean }) {
  const record = props.record;
  const remoteOnly = () => isRemoteOnly(record);
  const archived = () => Boolean(record.archived_at);
  const cells = () => sessionRowCells(record, paneInnerCols());
  const headFg = () => {
    if (props.focused) return palette.accent;
    return archived() || remoteOnly() ? palette.fgDim : palette.fg;
  };
  return (
    <box
      height={1}
      width="100%"
      overflow="hidden"
      flexDirection="row"
      gap={1}
      backgroundColor={props.focused ? palette.bgActive : undefined}
      onMouseDown={() => void actions.tabOpenSession(record.id, record.name)}
    >
      <text flexShrink={0} fg={headFg()}>
        {cells().head}
      </text>
      <text flexShrink={0} fg={remoteOnly() ? palette.fgDim : palette.accent2}>
        {venueGlyph(record.venue)}
      </text>
      <Show when={cells().state}>
        <text flexShrink={0} fg={stateChipColor(record.state)}>
          {cells().state}
        </text>
      </Show>
      <Show when={cells().sync}>
        <text flexShrink={0} fg={syncBadgeColor(record.sync)}>
          {cells().sync}
        </text>
      </Show>
      <text flexShrink={0} fg={palette.fgDim}>
        {cells().date}
      </text>
    </box>
  );
}
