/**
 * Infrastructure — endpoints, jobs, models. Tab / ↑↓ select; s/c/r/l act on selection.
 */

import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { workspace, actions } from "../stores";
import { ModelsPanel } from "../components/models-panel";
import type { PanelListScroll } from "../components/panel-list";
import { palette, statusColor, statusIcon } from "../themes";
import { KeyTrap } from "../components/key-trap";
import { claimKeys } from "../keymap/key-claims";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import { PUBLIC_ENDPOINT_GLYPH, endpointLabel, endpointStateText } from "../lib/endpoint-label";

/** Full-width route rows track the real pane width — was fixed 96. */
const infraCap = () => panePanelWidth(1, 6);
import {
  handleInfrastructureShortcut,
  infraAvailableActionsLine,
  type InfraShortcutKey,
} from "../lib/space-shortcuts";
import { clampIndex, handleListKey } from "../lib/selectable-list";
import { sparkline } from "../lib/sparkline";
import type { KeyEvent } from "@opentui/core";
import type { UiEndpoint, UiJob } from "../types";
import { Panel } from "../ui/Panel";

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

/** One Endpoints row. A configured public source (no model, no live state)
 * reads `◇ <source> · public · <host> · configured`, never "unknown"
 * (wave 82); a platform row reads `<icon> <model> · <status> · <tier>`. */
function endpointRowText(ep: UiEndpoint, selected: boolean): string {
  const cursor = selected ? "▸" : " ";
  if (ep.isPublic) return `${cursor} ${PUBLIC_ENDPOINT_GLYPH} ${endpointLabel(ep)} · ${endpointStateText(ep)}`;
  return `${cursor} ${statusIcon(ep.status)} ${endpointLabel(ep)} · ${ep.status} · ${ep.tier}`;
}

function endpointRowColor(ep: UiEndpoint): string {
  return ep.isPublic ? palette.fgDim : statusColor(ep.status);
}

type InfraColumn = "endpoints" | "jobs";

const MODEL_PAGE_KEYS = new Map<string, 1 | -1>([
  ["pagedown", 1],
  ["pageup", -1],
]);

function StatChip(props: { label: string; value: string; color: string }) {
  return (
    <box flexDirection="row" gap={1} marginRight={3} overflow="hidden">
      <text fg={palette.fgDim}>{props.label}</text>
      <text fg={props.color}>{props.value}</text>
    </box>
  );
}

/** Panel chrome of a list column: the active one is accented and marked. */
function columnChrome(active: boolean, label: string) {
  if (!active) return { accent: palette.border, title: label, titleColor: palette.fgDim, tone: "base" as const };
  return { accent: palette.accent, title: `▸ ${label}`, titleColor: palette.accent, tone: "raised" as const };
}

/** A focused infrastructure pane keeps Tab for its list switch (plan H-6
 * `tab` claim; TUI audit r3). */
function infraListsOwnTab(listFocused: () => boolean): boolean {
  return workspace.paneFocus && workspace.paneView === "infrastructure" && listFocused();
}

export function InfrastructureRoute() {
  const load = () => (workspace.loadHistory.length > 0 ? workspace.loadHistory : [0]);
  const current = () => {
    const h = load();
    return h[h.length - 1] ?? 0;
  };

  const [column, setColumn] = createSignal<InfraColumn>("endpoints");
  const [selected, setSelected] = createSignal(0);
  const [listFocused, setListFocused] = createSignal(true);
  // The global Tab (pane focus toggle) runs first and took the key, so the
  // header's "Tab switches lists" never worked and the Jobs list was not
  // keyboard-reachable (TUI audit r3). A focused pane keeps Tab; Esc or a
  // click leave it, as in the research pane.
  onCleanup(claimKeys("infrastructure-lists", ["tab"], () => infraListsOwnTab(listFocused)));

  const activeList = createMemo(() =>
    column() === "endpoints" ? workspace.endpoints : workspace.jobs,
  );

  const syncSelection = (index: number) => {
    const list = activeList();
    const i = clampIndex(index, list.length);
    setSelected(i);
    const row = list[i];
    if (row) actions.selectObject(row.id);
  };

  const switchColumn = (next: InfraColumn) => {
    setColumn(next);
    setSelected(0);
    const list = next === "endpoints" ? workspace.endpoints : workspace.jobs;
    if (list[0]) actions.selectObject(list[0].id);
    else actions.selectObject(null);
  };

  onMount(() => {
    if (workspace.endpoints.length > 0) {
      switchColumn("endpoints");
    } else if (workspace.jobs.length > 0) {
      switchColumn("jobs");
    }
  });

  createEffect(() => {
    const list = activeList();
    if (list.length === 0) return;
    if (!list.some((r) => r.id === workspace.selectedObjectId)) {
      syncSelection(0);
    }
  });

  // The Models list has no trap of its own: this trap owns the pane's keys
  // and pages the list (PgUp/PgDn) — one key owner (TUI audit r2 #15).
  let modelsScroll: PanelListScroll | undefined;
  const pageModels = (name: string): boolean => {
    const direction = MODEL_PAGE_KEYS.get(name);
    if (!direction || !modelsScroll) return false;
    modelsScroll.page(direction);
    return true;
  };

  /** Tab (column) and PgUp/PgDn (models list): true when handled. */
  const handleLayoutKey = (name: string): boolean => {
    if (name !== "tab") return pageModels(name);
    switchColumn(column() === "endpoints" ? "jobs" : "endpoints");
    return true;
  };

  const handleKey = (event: KeyEvent) => {
    const name = event.name;
    if (handleLayoutKey(name)) {
      event.preventDefault();
      return;
    }
    if (name === "s" || name === "c" || name === "r" || name === "l") {
      if (handleInfrastructureShortcut(name as InfraShortcutKey)) {
        event.preventDefault();
        return;
      }
    }
    if (
      handleListKey(name, {
        selected: selected(),
        length: activeList().length,
        ctrl: Boolean(event.ctrl),
        onMove: (i) => {
          event.preventDefault();
          syncSelection(i);
        },
        onEnter: (i) => {
          event.preventDefault();
          syncSelection(i);
        },
      })
    ) {
      /* handled */
    }
  };

  const actionsLine = createMemo(() => infraAvailableActionsLine());

  return (
    <box flexDirection="column" gap={0} width="100%" height="100%" overflow="hidden">
      <Line fg={palette.fgBright}>Infrastructure</Line>
      <Line fg={palette.fgDim}>
        Platform endpoints & jobs — not RC1 research. Tab switches lists · ↑↓ selects · letters act.
      </Line>

      <box flexDirection="row" height={1} marginTop={1} overflow="hidden">
        <StatChip label="Endpoints" value={String(workspace.endpoints.length)} color={palette.infrastructure} />
        <StatChip label="Jobs" value={String(workspace.jobs.length)} color={palette.tasks} />
        <StatChip label="Models" value={String(workspace.models.length)} color={palette.knowledge} />
        <StatChip label="Load" value={`${current()} active`} color={palette.statusInfo} />
        {/* Wave G tier-1: inline sparkline of load history (one complete row). */}
        <box height={1} overflow="hidden">
          <text fg={palette.fgDim}>{sparkline(load(), 26)}</text>
        </box>
      </box>

      <box
        height={1}
        width="100%"
        marginTop={1}
        overflow="hidden"
        backgroundColor={palette.bgActive}
      >
        <text fg={palette.accent}>{truncate(actionsLine(), infraCap())}</text>
      </box>
      <Show when={workspace.spaceFeedback}>
        <Line fg={palette.statusWarn}>{truncate(workspace.spaceFeedback!, infraCap())}</Line>
      </Show>

      <box flexDirection="row" gap={2} marginTop={1} flexGrow={1} overflow="hidden">
        <Panel
          flexDirection="column"
          gap={0}
          flexGrow={1}
          overflow="hidden"
          accent={columnChrome(column() === "endpoints", "Endpoints").accent}
          title={columnChrome(column() === "endpoints", "Endpoints").title}
          titleColor={columnChrome(column() === "endpoints", "Endpoints").titleColor}
          tone={columnChrome(column() === "endpoints", "Endpoints").tone}
          padding={1}
        >
          <For each={workspace.endpoints}>
            {(ep: UiEndpoint, idx) => {
              const isSel = () => column() === "endpoints" && idx() === selected();
              const line = () => truncate(endpointRowText(ep, isSel()), 42);
              return (
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={isSel() ? palette.bgActive : undefined}
                  onMouseDown={() => {
                    setColumn("endpoints");
                    setListFocused(true);
                    setSelected(idx());
                    actions.selectObject(ep.id);
                  }}
                >
                  <text fg={isSel() ? palette.fgBright : endpointRowColor(ep)}>{line()}</text>
                </box>
              );
            }}
          </For>
          <Show when={workspace.endpoints.length === 0}>
            <Line fg={palette.fgDim}>None yet — /endpoint up</Line>
          </Show>
        </Panel>

        <Panel
          flexDirection="column"
          gap={0}
          flexGrow={1}
          overflow="hidden"
          accent={columnChrome(column() === "jobs", "Jobs").accent}
          title={columnChrome(column() === "jobs", "Jobs").title}
          titleColor={columnChrome(column() === "jobs", "Jobs").titleColor}
          tone={columnChrome(column() === "jobs", "Jobs").tone}
          padding={1}
        >
          <For each={workspace.jobs}>
            {(job: UiJob, idx) => {
              const isSel = () => column() === "jobs" && idx() === selected();
              const line = () =>
                truncate(
                  `${isSel() ? "▸" : " "} ${statusIcon(job.status)} ${job.type} · ${job.status} · ${job.id.slice(0, 8)}`,
                  42,
                );
              return (
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={isSel() ? palette.bgActive : undefined}
                  onMouseDown={() => {
                    setColumn("jobs");
                    setListFocused(true);
                    setSelected(idx());
                    actions.selectObject(job.id);
                  }}
                >
                  <text fg={isSel() ? palette.fgBright : statusColor(job.status)}>{line()}</text>
                </box>
              );
            }}
          </For>
          <Show when={workspace.jobs.length === 0}>
            <Line fg={palette.fgDim}>No jobs reported</Line>
          </Show>
        </Panel>

        <Panel
          flexDirection="column"
          gap={0}
          width="28%"
          overflow="hidden"
          title={`Models (per source · ● ready ○ unavailable)`}
          titleColor={palette.fgDim}
          padding={1}
        >
          {/* One key owner (r2 #15): the list's own trap, mounted when the
              endpoint poll lands, took ↑↓ from the Endpoints/Jobs trap. */}
          <ModelsPanel keyboard={false} onScrollApi={(api) => (modelsScroll = api)} />
          <Line fg={palette.fgDim}>/models · /endpoint up</Line>
        </Panel>
      </box>

      <KeyTrap paneScoped focused={listFocused()} onKeyDown={handleKey} />
    </box>
  );
}
