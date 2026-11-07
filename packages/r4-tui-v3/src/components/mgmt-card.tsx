/**
 * Management card renderer (mgmt-card.v1 on the v3 surface).
 * Bordered summary card inside the transcript describing ONE management
 * answer (workflow list/run/status/results, loop, knowledge status…).
 * Interactive actions live on a later wave — this wave is read-only display.
 */
import { For, Show, createMemo } from "solid-js";
import { t, fg } from "@opentui/core";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { chatContentWidth } from "../lib/term-width";
import { actions, type WorkspaceState } from "../stores";
import type { Space } from "../types";
import {
  knowledgeGraphContentRows,
  knowledgeGraphListRows,
  knowledgeInventoryRows,
  knowledgeStatusRows,
  loopIterationRows,
  loopListRows,
  loopStatusRows,
  sessionViewRows,
  statusIcon,
  wfDescribeRows,
  wfDetailSteps,
  wfListRows,
  wfResultsRows,
  wfStatusRows,
  type MgmtRow,
} from "../lib/mgmt-card-format";
import { describeWithSharing } from "../lib/workflow-sharing";
import { umrsWorkflowStore } from "../stores/umrs-workflow";
import type { MgmtCard } from "../types";
import { Panel } from "../ui/Panel";

/** request-op card line per approval answer (ADR-104 D-3 words; yes/no are
 * older cards' words). */
const REQUEST_OP_STATUS: Record<string, string> = {
  once: "gate approved (once)",
  yes: "gate approved",
  always: "gate approved (always this session)",
};

function CardTitle({ title, icon }: { title: string; icon: string }) {
  return (
    <box height={1}>
      <text fg={palette.accent}>
        {icon} {title}
      </text>
    </box>
  );
}

/** Status tint used by the styled-text badge fragments. */
function statusFg(status?: string): string {
  const s = (status ?? "").toLowerCase();
  if (s === "failed" || s === "error") return palette.statusErr;
  if (s === "running") return palette.statusInfo;
  if (s.startsWith("succ") || s === "ok") return palette.statusOk;
  return palette.fgDim;
}

/** Plain-string badge content fed into styled-text rows. */
function statusText(status?: string): string {
  return (`${statusIcon(status) ?? ""} ${status ?? ""}`).trimEnd();
}

function KvRows({ rows }: { rows: MgmtRow[] }) {
  return (
    <box flexDirection="column">
      <For each={rows}>
        {(row) => (
          <box height={1}>
            <text
              children={t`${fg(palette.fgDim)(`${row.key}: `)}${truncate(row.value, chatContentWidth(10))}`}
            />
          </box>
        )}
      </For>
    </box>
  );
}

/**
 * V4 (V5 = card-driven intent): which detail-pane view explains this card.
 * The card is the button — clicking the card opens that pane.
 */
export function cardPaneTarget(card: MgmtCard): Exclude<WorkspaceState["paneView"], null> | null {
  const type = card.type ?? "";
  if (type.startsWith("wf-")) return "work";
  if (type.startsWith("loop")) return "research";
  if (type.startsWith("knowledge")) return "knowledge";
  if (type === "session-view") return "research";
  if (type === "job") return "infrastructure";
  // request-op + unknowns stay inert (no phantom panes).
  return null;
}

const PANE_VERB_LABEL: Partial<Record<string, string>> = {
  workflows: "workflows pane",
  research: "research pane",
  knowledge: "knowledge pane",
  infrastructure: "infra pane",
};

export function MgmtCardView({ card }: { card: MgmtCard }) {
  const kind = createMemo(() => card.type);
  const target = createMemo(() => cardPaneTarget(card));
  const openPane = () => {
    const view = target();
    if (view && view !== "logs") actions.openPane(view as Space);
  };

  return (
    <Panel
      flexDirection="column"
      accent={target() ? palette.accent : palette.border}
      padding={1}
      width="100%"
      overflow="hidden"
      onMouseDown={target() ? openPane : undefined}
    >
      <Show when={kind() === "wf-list"}>
        <CardTitle title="UMRS workflows" icon="▦" />
        <For each={wfListRows(card.payload).rows}>
          {(row) => (
            <box height={1}>
              <text
                children={t`${fg(palette.fg)(`${truncate(row[0], 30)} `)}${fg(palette.fgDim)(row[1] ? `[${row[1]}] ` : "")}${truncate(row[2], chatContentWidth(44))}`}
              />
            </box>
          )}
        </For>
        <box height={1}>
          <text fg={palette.fgDim}>{wfListRows(card.payload).total} workflow(s) total</text>
        </box>
      </Show>

      <Show when={kind() === "wf-run"}>
        {(() => {
          const { header, steps } = wfDetailSteps(card.payload);
          const status = header.find((r) => r.key === "status")?.value;
          const name = header.find((r) => r.key === "workflow")?.value ?? "execution";
          return (
            <box flexDirection="column">
              <box height={1}>
                <text
                  children={t`${fg(palette.accent)(`▶ workflow run — ${truncate(name, chatContentWidth(30))} `)}${fg(statusFg(status))(statusText(status))}`}
                />
              </box>
              <KvRows rows={header.filter((r) => r.key !== "status")} />
              <Show when={steps.length > 0}>
                <For each={steps}>
                  {(step) => (
                    <box height={1}>
                      <text
                        children={t`${fg(statusFg(step.status))(statusText(step.status))}${fg(palette.fg)(` ${truncate(step.name, chatContentWidth(26))} `)}${fg(palette.fgDim)(step.duration ?? "")}`}
                      />
                    </box>
                  )}
                </For>
              </Show>
            </box>
          );
        })()}
      </Show>

      <Show when={kind() === "knowledge-status"}>
        <CardTitle title="knowledge graph — this project" icon="◉" />
        <KvRows rows={knowledgeStatusRows(card.payload)} />
      </Show>

      <Show when={kind() === "knowledge-detail"}>
        <CardTitle title="knowledge inventory (TUI session)" icon="◉" />
        <KvRows rows={knowledgeInventoryRows(card.payload)} />
      </Show>

      <Show when={kind() === "knowledge-graph"}>
        {(() => {
          const rows = knowledgeGraphListRows(card.payload);
          return (
            <box flexDirection="column">
              <CardTitle title="registered knowledge graphs" icon="◉" />
              <For each={rows}>
                {(row) => (
                  <box height={1}>
                    <text
                      children={t`${fg(palette.fg)(`${truncate(row[0], 20)} `)}${fg(palette.fgDim)(row[1] ? `[${row[1]}] ` : "")}${fg(palette.fgDim)(`${truncate(row[2], 14)} `)}${truncate(row[3], chatContentWidth(46))}`}
                    />
                  </box>
                )}
              </For>
            </box>
          );
        })()}
      </Show>

      <Show when={kind() === "knowledge-graph-content"}>
        {(() => {
          const { header, nodes } = knowledgeGraphContentRows(card.payload);
          return (
            <box flexDirection="column">
              <CardTitle title="knowledge graph content" icon="◉" />
              <KvRows rows={header} />
              <For each={nodes}>
                {(row) => (
                  <box height={1}>
                    <text
                      children={t`${fg(palette.fg)(` ${truncate(row[0], chatContentWidth(24))} `)}${fg(palette.fgDim)(row[1] ? `[${row[1]}]` : "")}`}
                    />
                  </box>
                )}
              </For>
            </box>
          );
        })()}
      </Show>

      <Show when={kind() === "request-op"}>
        {(() => {
          const p = (card.payload ?? {}) as { tool?: string; decision?: string; at?: string };
          const status = REQUEST_OP_STATUS[p.decision ?? ""] ?? "gate rejected";
          return (
            <Panel
              flexDirection="row"
              tone="raised"
              paddingLeft={1}
              paddingRight={1}
              width="100%"
              overflow="hidden"
            >
              <text
                children={t`${fg(palette.accent)("◔ ")}${fg(palette.fg)(`${status} `)}${fg(palette.fgDim)(`· ${truncate(p.tool ?? "workflow gate", chatContentWidth(18))}`)}`}
              />
            </Panel>
          );
        })()}
      </Show>

      <Show when={kind() === "loop-list"}>
        {(() => {
          const { rows, active, runtime } = loopListRows(card.payload);
          return (
            <box flexDirection="column">
              <CardTitle title="research loops" icon="⥁" />
              <For each={rows}>
                {(row) => (
                  <box height={1}>
                    <text
                      children={t`${fg(statusFg(row[1]))(statusText(row[1]))}${fg(palette.fg)(` ${truncate(row[0], 22)} `)}${fg(palette.fgDim)(`${row[2]} · `)}${truncate(row[3], chatContentWidth(36))}`}
                    />
                  </box>
                )}
              </For>
              <box height={1}>
                <text fg={palette.fgDim}>
                  {rows.length} loop(s) · {active} active{runtime ? ` · runtime ${runtime}` : ""}
                </text>
              </box>
            </box>
          );
        })()}
      </Show>

      <Show when={kind() === "loop-status"}>
        {(() => {
          const rows = loopStatusRows(card.payload);
          const iterations = loopIterationRows(card.payload);
          return (
            <box flexDirection="column">
              <CardTitle title="loop detail" icon="⥁" />
              <KvRows rows={rows} />
              <Show when={iterations.length > 0}>
                <box height={1}>
                  <text fg={palette.fgDim}> recent iterations:</text>
                </box>
                <For each={iterations}>
                  {(row) => (
                    <box height={1}>
                      <text
                        children={t`${fg(palette.accent)(` ${row[0]} `)}${truncate(row[1], chatContentWidth(12))}`}
                      />
                    </box>
                  )}
                </For>
              </Show>
            </box>
          );
        })()}
      </Show>

      <Show when={kind() === "session-view"}>
        <CardTitle title="research session view" icon="◎" />
        <KvRows rows={sessionViewRows(card.payload)} />
      </Show>

      <Show when={kind() === "wf-show"}>
        <CardTitle title="workflow describe" icon="𝑖" />
        <KvRows rows={wfDescribeRows(describeWithSharing(card.payload, umrsWorkflowStore.catalog))} />
      </Show>

      <Show when={kind() === "wf-status"}>
        {(() => {
          const rows = wfStatusRows(card.payload);
          const status = rows.find((r) => r.key === "status")?.value;
          return (
            <box flexDirection="column">
              <box height={1}>
                <text
                  children={t`${fg(palette.accent)("◔ status ")}${fg(statusFg(status))(statusText(status))}`}
                />
              </box>
              <KvRows rows={rows.filter((r) => r.key !== "status")} />
            </box>
          );
        })()}
      </Show>

      <Show when={kind() === "wf-results"}>
        {(() => {
          const rows = wfResultsRows(card.payload);
          const status = rows.find((r) => r.key === "status")?.value;
          return (
            <box flexDirection="column">
              <box height={1}>
                <text
                  children={t`${fg(palette.accent)("▣ results ")}${fg(statusFg(status))(statusText(status))}`}
                />
              </box>
              <KvRows rows={rows.filter((r) => r.key !== "status")} />
            </box>
          );
        })()}
      </Show>

      <Show
        when={![
          "wf-list",
          "wf-run",
          "wf-show",
          "wf-status",
          "wf-results",
          "loop-list",
          "loop-status",
          "session-view",
          "knowledge-status",
          "knowledge-detail",
          "knowledge-graph",
          "knowledge-graph-content",
        ].includes(kind())}
      >
        <CardTitle title={kind()} icon="☰" />
        <box height={1}>
          <text fg={palette.fgDim}>{truncate(JSON.stringify(card.payload) ?? "", 96)}</text>
        </box>
      </Show>
      <Show when={target()}>
        <box height={1} marginTop={1}>
          <text fg={palette.accent2} onMouseDown={openPane}>
            ↵ open {PANE_VERB_LABEL[target()!] ?? target()}
          </text>
        </box>
      </Show>
    </Panel>
  );
}
