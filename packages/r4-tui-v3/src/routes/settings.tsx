/**
 * Settings — preferences, connection readout, inventory, keyboard reference.
 * W2: skills + MCP inventory are selectable lists (↑↓ + Enter for details).
 * Issue #217 (2026-10-27): a Preferences list at the top shows each existing
 * preference with its value; Enter or a click toggles it or opens the
 * surface that owns it (lib/settings-preferences.ts). Tab cycles
 * Preferences → Skills → MCP while the pane has the keys.
 */

import { For, Show, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { t, fg, bold } from "@opentui/core";
import { palette } from "../themes";
import { workspace, actions, activeTab } from "../stores";
import { KeyTrap } from "../components/key-trap";
import type { KeyEvent } from "@opentui/core";
import { handleListKey, clampIndex } from "../lib/selectable-list";
import { PanelList, type PanelSection } from "../components/panel-list";
import { getCompanionBridge } from "../bridge";
import { isOfflineBackend } from "../lib/console-mode";
import { skillNoticeText, mcpServerNoticeText } from "../lib/inventory-notice";
import type { McpServerEntry } from "../lib/mcp-report";
import { getRegisteredBindings } from "../keymap";
import { groupBindings, whichKeyRows } from "../lib/which-key";
import type { WhichKeyInput } from "../lib/which-key";
import { claimKeys } from "../keymap/key-claims";
import {
  activatePreference,
  nextSettingsPanel,
  preferenceRowText,
  preferenceRows,
  type PreferenceRow,
  type SettingsPanelId,
} from "../lib/settings-preferences";

type Binding = { key: string; description: string };
type BindingGroup = { title: string; items: Binding[] };

/**
 * L4-post: the static BINDING_GROUPS table retired — Settings' keyboard
 * section renders keymap-registry-derived rows (never drifts).
 */


function inventoryFg(n: number): string {
  return n > 0 ? palette.statusOk : palette.fgDim;
}

type PanelId = SettingsPanelId;

/** Which panel has the keys, and the Preferences cursor — shared by the
 * Preferences list (top of the pane) and the one key trap below. */
interface PanelNav {
  focus: () => PanelId;
  setFocus: (id: PanelId) => void;
  prefCursor: () => number;
  setPrefCursor: (index: number) => void;
  rows: () => PreferenceRow[];
}

function createPanelNav(): PanelNav {
  const [focus, setFocus] = createSignal<PanelId>("prefs");
  const [prefCursor, setPrefCursor] = createSignal(0);
  // The status line and rule count live in files, not the store: re-read
  // them whenever the transcript grows (every /statusline or /settings
  // rules change posts a notice there).
  const rows = createMemo(() => {
    void activeTab().chatMessages.length;
    return preferenceRows();
  });
  return { focus, setFocus, prefCursor, setPrefCursor, rows };
}

/** A focused Settings pane keeps Tab for its panel cycle (the
 * infrastructure precedent, plan H-6 `tab` claim): the global Tab runs
 * first and toggled the pane focus, so "Tab panel" never worked. Esc or a
 * click leave the pane. */
function settingsOwnsTab(): boolean {
  return workspace.paneFocus && workspace.paneView === "settings";
}

/** One keyboard-or-mouse list the trap drives. */
interface ListPanel {
  cursor: number;
  length: number;
  move: (index: number) => void;
  enter: (index: number) => void;
}

/** Fetch the MCP inventory once per session (settings panel is its surface). */
async function refreshMcpServers(): Promise<void> {
  if (workspace.mcpServers !== null || isOfflineBackend()) return;
  try {
    const res = (await getCompanionBridge().request("list_mcp_servers", {}, 15_000)) as {
      servers?: McpServerEntry[];
    };
    actions.setMcpServers(res.servers ?? []);
  } catch {
    actions.setMcpServers([]);
  }
}

/** One selectable row — ▸ marker + highlighted background when active+cursor. */
function InventoryRow(props: {
  label: string;
  active: boolean;
  cursor: boolean;
  onMouseSelect: () => void;
}) {
  const hot = () => props.active && props.cursor;
  return (
    <box
      height={1}
      width="100%"
      overflow="hidden"
      backgroundColor={hot() ? palette.bgActive : undefined}
      onMouseDown={props.onMouseSelect}
    >
      {/* No handler on the text: a mouse-down bubbles to the box, and a
          second handler ran a Preferences toggle twice (net no change). */}
      <text fg={hot() ? palette.fgBright : palette.fg}>
        {`${props.cursor ? "▸" : " "} ${props.label}`}
      </text>
    </box>
  );
}

/** Preferences (issue #217): each row shows its value and where it lives;
 * Enter (keys: the trap in InventoryPanels) or a click acts on it. */
function PreferencesSection(props: { nav: PanelNav }) {
  const nav = props.nav;
  const pick = (index: number) => {
    nav.setFocus("prefs");
    nav.setPrefCursor(index);
    const row = nav.rows()[index];
    if (row) void activatePreference(row.key);
  };
  return (
    <box flexDirection="column" gap={0} marginTop={1}>
      <box flexDirection="row" flexWrap="wrap" columnGap={2}>
        <text flexShrink={0} fg={nav.focus() === "prefs" ? palette.accent : palette.fg} onMouseDown={() => nav.setFocus("prefs")}>
          Preferences
        </text>
        <text flexShrink={0} fg={palette.fgDim}>↑↓ select · Enter change · Tab next panel</text>
      </box>
      <For each={nav.rows()}>
        {(row, idx) => (
          <InventoryRow
            label={preferenceRowText(row)}
            active={nav.focus() === "prefs"}
            cursor={idx() === nav.prefCursor()}
            onMouseSelect={() => pick(idx())}
          />
        )}
      </For>
      <text fg={palette.fgDim}>{nav.rows()[nav.prefCursor()]?.hint ?? ""}</text>
    </box>
  );
}

function InventoryPanels(props: { nav: PanelNav }) {
  const nav = props.nav;
  const focus = nav.focus;
  const setFocus = nav.setFocus;
  const [mcpCursor, setMcpCursor] = createSignal(0);
  onCleanup(claimKeys("settings-panels", ["tab"], settingsOwnsTab));
  const [skillsCollapsed, setSkillsCollapsed] = createSignal<Record<string, boolean>>({});

  onMount(() => {
    void refreshMcpServers();
  });

  const skills = () => workspace.skills;
  const mcpServers = () => workspace.mcpServers ?? [];

  const toggleSkillsSection = (source: string) =>
    setSkillsCollapsed((state) => ({ ...state, [source]: !state[source] }));

  /** Skills grouped by source — section headers collapse the group. */
  const skillsSections = (): PanelSection[] => {
    const skillsArr = skills();
    type SkillItem = (typeof skillsArr)[number];
    const bySource = new Map<string, SkillItem[]>();
    for (const skill of skillsArr) {
      const bucket = bySource.get(skill.source) ?? [];
      bucket.push(skill);
      bySource.set(skill.source, bucket);
    }
    return [...bySource.entries()].map(([source, rows]) => ({
      title: `${source} (${rows.length})`,
      collapsed: Boolean(skillsCollapsed()[source]),
      onToggle: () => toggleSkillsSection(source),
      items: rows.map((skill) => ({
        key: `${source}:${skill.name}`,
        label: `${skill.active ? "●" : "○"} ${skill.name}${skill.hasMcp ? " · +mcp" : ""}`,
        fg: skill.active ? palette.statusOk : palette.fg,
        onSelect: () => actions.sendSystemNotice(skillNoticeText(skill)),
      })),
    }));
  };

  /** The inventory list on screen: the focused one, else the last one. */
  const shown = createMemo<"skills" | "mcp">((prev) => {
    const f = focus();
    return f === "prefs" ? prev : f;
  }, "skills");
  const cyclePanel = () => setFocus(nextSettingsPanel(focus()));

  // The trap drives Preferences, MCP, and Skills only while the skills
  // list is empty (a non-empty list owns its keys through PanelList).
  const panels: Record<PanelId, () => ListPanel> = {
    prefs: () => ({
      cursor: nav.prefCursor(),
      length: nav.rows().length,
      move: (index) => nav.setPrefCursor(index),
      enter: (index) => {
        const row = nav.rows()[index];
        if (row) void activatePreference(row.key);
      },
    }),
    skills: () => ({ cursor: 0, length: 0, move: () => {}, enter: () => {} }),
    mcp: () => ({
      cursor: mcpCursor(),
      length: mcpServers().length,
      move: (index) => setMcpCursor(index),
      enter: (index) => {
        const server = mcpServers()[index];
        if (server) actions.sendSystemNotice(mcpServerNoticeText(server));
      },
    }),
  };

  const onListKey = (event: KeyEvent) => {
    const panel = panels[focus()]();
    handleListKey(event.name, {
      selected: panel.cursor,
      length: panel.length,
      ctrl: Boolean(event.ctrl),
      onMove: (index) => {
        event.preventDefault();
        panel.move(clampIndex(index, panel.length));
      },
      onEnter: (index) => {
        event.preventDefault();
        panel.enter(index);
      },
      onOther: (name) => {
        if (name !== "tab") return false;
        event.preventDefault();
        cyclePanel();
        return true;
      },
    });
  };

  return (
    <box flexDirection="column" gap={0} marginTop={1}>
      <box flexDirection="row" flexWrap="wrap" columnGap={2}>
        <text flexShrink={0} fg={focus() === "skills" ? palette.accent : palette.fgDim} onMouseDown={() => setFocus("skills")}>
          Skills ({skills().length})
        </text>
        <text flexShrink={0} fg={focus() === "mcp" ? palette.accent : palette.fgDim} onMouseDown={() => setFocus("mcp")}>
          MCP servers ({mcpServers().length})
        </text>
        <text flexShrink={0} fg={palette.fgDim}>↑↓ select · Enter detail · Tab next panel</text>
      </box>
      <Show when={shown() === "skills"}>
        <Show
          when={skills().length > 0}
          fallback={<text fg={palette.fgDim}>No skills reported by the backend.</text>}
        >
          {/* keyboard only while Skills has focus: its trap mounts then
              (KeyTrap focus is read at mount) and never competes with the
              Preferences/MCP trap below. */}
          <PanelList sections={skillsSections()} maxRows={16} keyboard={focus() === "skills"} onTab={cyclePanel} />
        </Show>
      </Show>
      <Show when={shown() === "mcp"}>
        <Show
          when={workspace.mcpServers !== null}
          fallback={<text fg={palette.fgDim}>{isOfflineBackend() ? "offline/demo backend — no gateway inventory" : "loading MCP inventory…"}</text>}
        >
          <Show
            when={mcpServers().length > 0}
            fallback={<text fg={palette.fgDim}>No MCP servers registered on this gateway.</text>}
          >
            <For each={mcpServers()}>
              {(server, idx) => {
                const isLocal = String(server.source ?? "").startsWith("user-config");
                const chip = isLocal ? `${server.enabled === false ? "○" : "●"} ` : "";
                return (
                  <InventoryRow
                    label={`${chip}${server.name ?? server.server_id ?? "(unnamed)"} · ${server.status ?? "unknown"}${server.source ? ` · ${server.source}` : ""}`}
                    active={focus() === "mcp"}
                    cursor={idx() === mcpCursor()}
                    onMouseSelect={() => setMcpCursor(idx())}
                  />
                );
              }}
            </For>
          </Show>
        </Show>
      </Show>
      {/* One key owner per list (round 2): while the Skills PanelList is up
          its own trap scrolls/highlights/selects; a second focused trap here
          stole the keys and moved an invisible flat-order cursor. */}
      <box height={1} width={1} overflow="hidden">
        <KeyTrap paneScoped focused={focus() !== "skills" || skills().length === 0} onKeyDown={onListKey} />
      </box>
    </box>
  );
}

export function backendLabelFor(demoMode: boolean): string {
  if (demoMode) return "Offline fixtures";
  return "Live (r4 CLI + Companion chat)";
}

function backendLabel(): string {
  return backendLabelFor(workspace.demoMode);
}

export function keyboardRows(limit: number = 40): string[] {
  const all = whichKeyRows(groupBindings(getRegisteredBindings() as WhichKeyInput[]));
  return all.length <= limit ? all : [...all.slice(0, limit), `… +${all.length - limit} more keys (F12 shows everything)`];
}

export function SettingsRoute() {
  const offline = () => workspace.demoMode;
  const nav = createPanelNav();

  return (
    <scrollbox
      width="100%"
      height="100%"
      stickyScroll
      stickyStart="top"
      scrollY
      overflow="hidden"
      padding={1}
    >
      <box flexDirection="column" gap={1} width="100%">
        <text children={t`${bold(fg(palette.accent)("Settings"))}`} />
        <text fg={palette.fgDim}>Preferences, connection status and shortcuts. F12 lists every key.</text>

        <PreferencesSection nav={nav} />

        <box flexDirection="column" gap={0} marginTop={1}>
          <text children={t`${bold("Backend")}`} />
          <text fg={palette.fg}>Source: {backendLabel()}</text>
          <Show when={offline()}>
            <text fg={palette.fgDim}>
              Start with R4_TUI_BACKEND=cli for live polls and chat.
            </text>
          </Show>
          <Show when={!offline()}>
            <text fg={palette.fgDim}>
              Demo UI: relaunch with --demo or R4_TUI_DEMO=1
            </text>
          </Show>
        </box>

        <box flexDirection="column" gap={0} marginTop={1}>
          <text children={t`${bold("Connection")}`} />
          <box flexDirection="row" flexWrap="wrap" columnGap={2}>
            <text flexShrink={0} fg={workspace.connection.gateway === "connected" ? palette.statusOk : palette.statusWarn}>
              Gateway: {workspace.connection.gateway}
            </text>
            <text flexShrink={0} fg={workspace.connection.umrs === "connected" ? palette.statusOk : palette.statusWarn}>
              UMRS: {workspace.connection.umrs}
            </text>
            <text flexShrink={0} fg={workspace.connection.hpc === "connected" ? palette.statusOk : palette.statusWarn}>
              HPC: {workspace.connection.hpc}
            </text>
          </box>
        </box>

        <box flexDirection="column" gap={0} marginTop={1}>
          <text children={t`${bold("Inventory")}`} />
          {/* label: value pairs never shrink (a squeezed row split "Endpoints"
              from its ":" in the ~46-col side pane); whole pairs wrap. The
              model id alone may shrink — a long one wraps on its own line. */}
          <box flexDirection="row" flexWrap="wrap" columnGap={2}>
            <text flexShrink={0} fg={inventoryFg(workspace.endpoints.length)}>
              Endpoints: {workspace.endpoints.length}
            </text>
            <text flexShrink={0} fg={inventoryFg(workspace.models.length)}>Models: {workspace.models.length}</text>
            <text flexShrink={0} fg={inventoryFg(workspace.jobs.length)}>Jobs: {workspace.jobs.length}</text>
            <text flexShrink={0} fg={inventoryFg(workspace.skills.length)}>Skills: {workspace.skills.length}</text>
            <text fg={palette.fgDim}>Model: {activeTab().currentModel ?? "auto"}</text>
          </box>
        </box>

        <box flexDirection="column" gap={0} marginTop={1}>
          <text children={t`${bold("Skills & MCP — selectable")}`} />
          <InventoryPanels nav={nav} />
        </box>

        <box flexDirection="column" gap={0} marginTop={1}>
          <text children={t`${bold("Keyboard")}`} />
          <text fg={palette.fgDim}>Live keymap rows — F12 for the full view; cannot drift:</text>
          <For each={keyboardRows()}>
            {(row) => (
              <box height={1} overflow="hidden" width="100%">
                <text fg={row.startsWith("◇") ? palette.accent2 : palette.fgDim}>{row}</text>
              </box>
            )}
          </For>
        </box>
      </box>
    </scrollbox>
  );
}
