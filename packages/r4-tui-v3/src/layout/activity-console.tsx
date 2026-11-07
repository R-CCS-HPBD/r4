/**
 * Bottom Activity Console — diagnostic tray for agent/runtime signals.
 */

import { For, Show, createMemo } from "solid-js";
import { workspace, actions, activeTab } from "../stores";
import { agentRuntime } from "../stores/agent-runtime";
import { palette } from "../themes";
import { t, dim } from "@opentui/core";
import type { ActivityTab } from "../types";
import { ACTIVITY_TAB_LABELS, ACTIVITY_TABS } from "../lib/activity-tabs";
import { toolActionIcon } from "../lib/tool-timeline";
import { localTimeHHMMSS } from "../lib/time";
import { TODO_GLYPH, TODO_PRIORITY_SHORT, todoCounts, todoSummaryLine } from "../lib/session-todos";
import {
  PLAN_STATUS_GLYPH,
  planStepDepths,
  workflowNodesFromCalls,
} from "../lib/session-plan";
import { Panel } from "../ui/Panel";

const tabs: { id: ActivityTab; label: string }[] = ACTIVITY_TABS.map((id) => ({
  id,
  label: ACTIVITY_TAB_LABELS[id],
}));

function EventsTab() {
  return (
    <box flexDirection="column" gap={1}>
      <For each={workspace.events.slice(0, 10)}>
        {(event) => (
          <text
            children={t`${dim(localTimeHHMMSS(event.timestamp))} ${event.actor} ${event.action}${
              event.detail ? ` — ${event.detail}` : ""
            }`}
          />
        )}
      </For>
      <Show when={workspace.events.length === 0}>
        <text fg={palette.fgDim}>No events yet.</text>
      </Show>
    </box>
  );
}

function NotificationsTab() {
  return (
    <box flexDirection="column" gap={1}>
      <For each={workspace.notifications.slice(0, 10)}>
        {(n) => (
          <text
            fg={n.severity === "error" ? palette.statusErr : n.severity === "warning" ? palette.statusWarn : palette.statusInfo}
          >
            {n.severity.toUpperCase()}: {n.title}
          </text>
        )}
      </For>
      <Show when={workspace.notifications.length === 0}>
        <text fg={palette.fgDim}>No notifications.</text>
      </Show>
    </box>
  );
}

function AgentLogsTab() {
  const agentEvents = createMemo(() => {
    const seen = new Set<string>();
    const merged = [
      ...agentRuntime.events.map((e) => ({
        ts: e.timestamp,
        line: `${e.eventType}${e.agentId ? ` · ${e.agentId}` : ""}`,
      })),
      ...workspace.events
        .filter((e) => e.type === "agent")
        .map((e) => ({
          ts: e.timestamp,
          line: `${e.actor} ${e.action}${e.detail ? ` — ${e.detail}` : ""}`,
        })),
      ...agentRuntime.timeline.map((e) => ({
        ts: e.timestamp,
        line: `${e.type}: ${e.title}`,
      })),
    ]
      .sort((a, b) => b.ts.localeCompare(a.ts))
      .filter((entry) => {
        const key = `${entry.ts}|${entry.line}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    return merged.slice(0, 12);
  });

  return (
    <box flexDirection="column" gap={1}>
      <For each={agentEvents()}>
        {(entry) => (
          <text children={t`${dim(localTimeHHMMSS(entry.ts))} ${entry.line}`} />
        )}
      </For>
      <Show when={agentEvents().length === 0}>
        <text fg={palette.fgDim}>No agent activity yet.</text>
      </Show>
    </box>
  );
}

function ToolCallsTab() {
  const toolEvents = createMemo(() =>
    workspace.events.filter((e) => e.type === "tool").slice(0, 12),
  );

  return (
    <box flexDirection="column" gap={1}>
      <For each={toolEvents()}>
        {(event) => (
          <text
            children={t`${dim(localTimeHHMMSS(event.timestamp))} ${toolActionIcon(event.action, event.detail)} ${
              event.detail || event.action
            }`}
          />
        )}
      </For>
      <Show when={toolEvents().length === 0}>
        <text fg={palette.fgDim}>No tool calls yet.</text>
      </Show>
    </box>
  );
}

/** Plan-step status fg by glyph (accessibility: color + symbol, not color-only). */
function planStepFg(status: string): string {
  if (status === "done") return palette.statusOk;
  if (status === "active") return palette.accent;
  if (status === "failed") return palette.statusErr;
  return palette.fgDim;
}

/**
 * Session plan graph — structured steps (task-graph mirror of todo_write)
 * rendered as a dependency-layered DAG, plus workflow executions launched
 * during the session. Ordered rows; a step's depth = longest dependency
 * chain to a root, drawn with └─ connectors under its dependency layer.
 */
function PlanGraphRows() {
  const steps = createMemo(() => activeTab().sessionPlan);
  const depths = createMemo(() => planStepDepths(steps()));
  const workflows = createMemo(() => workflowNodesFromCalls(activeTab().turnToolActivity));
  const doneCount = createMemo(() => steps().filter((s) => s.status === "done").length);
  return (
    <box flexDirection="column" gap={0}>
      <box height={1} overflow="hidden">
        <text fg={palette.fgDim}>
          {`plan: ${doneCount()}/${steps().length} steps done${workflows().length > 0 ? ` · ⤳ ${workflows().length} workflow${workflows().length === 1 ? "" : "s"}` : ""}`}
        </text>
      </box>
      <For each={steps()}>
        {(step) => {
          const depth = () => Math.min(depths().get(step.id) ?? 0, 6);
          const glyph = () => PLAN_STATUS_GLYPH[step.status] ?? "○";
          return (
            <box height={1} overflow="hidden">
              <text fg={planStepFg(step.status)}>
                {`${"  ".repeat(depth())}${depth() > 0 ? "└─ " : ""}${glyph()} ${step.title}${step.status === "active" && step.progress > 0 ? ` ${step.progress}%` : ""}`}
              </text>
            </box>
          );
        }}
      </For>
      <For each={workflows()}>
        {(node) => (
          <box height={1} overflow="hidden">
            <text fg={palette.knowledge}>
              {`⤳ ${node.label} `}
            </text>
            <text fg={palette.fgDim}>{`(${node.id.slice(0, 8)}… ${node.status})`}</text>
          </box>
        )}
      </For>
    </box>
  );
}

/** Session plan — structured graph when the bridge serves task-graph rows,
 * legacy todo-list mirror as fallback (pre-plan backends). */
function TodosTab() {
  const counts = createMemo(() => todoCounts(activeTab().sessionTodos));
  const hasPlan = createMemo(() => activeTab().sessionPlan.length > 0);
  return (
    <box flexDirection="column" gap={0}>
      <Show
        when={hasPlan() || activeTab().sessionTodos.length > 0}
        fallback={
          <text fg={palette.fgDim}>
            No session plan yet — appears when the agent plans its work (todowrite).
          </text>
        }
      >
        <Show when={hasPlan()} fallback={<LegacyTodoRows />}>
          <PlanGraphRows />
        </Show>
        <Show when={!hasPlan() && counts().cancelled > 0}>
          <box height={1} overflow="hidden">
            <text fg={palette.fgDim}>{`✗ ${counts().cancelled} cancelled`}</text>
          </box>
        </Show>
      </Show>
    </box>
  );
}

/** Legacy todo-list rows (fallback when the bridge predates get_session_plan). */
function LegacyTodoRows() {
  return (
    <>
      <box height={1} overflow="hidden">
        <text fg={palette.fgDim}>{todoSummaryLine(activeTab().sessionTodos)}</text>
      </box>
      <For each={activeTab().sessionTodos}>
        {(todo) => {
          const glyph = () => TODO_GLYPH[todo.status] ?? "□";
          const fg = () =>
            glyph() === "✓"
              ? palette.statusOk
              : glyph() === "▶"
                ? palette.accent
                : glyph() === "✗"
                  ? palette.statusErr
                  : palette.fg;
          const chip = () => TODO_PRIORITY_SHORT[todo.priority] ?? "";
          return (
            <box height={1} overflow="hidden">
              <text fg={fg()}>
                {`${glyph()} ${chip() ? `${chip()} ` : ""}${todo.content}`}
              </text>
              <Show when={todo.status !== "pending" && todo.status !== "completed"}>
                <text fg={palette.fgDim}>{`  [${todo.status.replace(/_/g, " ")}]`}</text>
              </Show>
            </box>
          );
        }}
      </For>
    </>
  );
}

function TerminalTab() {
  return (
    <box flexDirection="column" gap={1}>
      <For each={workspace.terminalLines.slice(0, 12)}>
        {(line) => <text fg={palette.fgDim}>{line}</text>}
      </For>
      <Show when={workspace.terminalLines.length === 0}>
        <text fg={palette.fgDim}>Backend command output appears here.</text>
      </Show>
    </box>
  );
}

export function ActivityConsole() {
  const active = () => workspace.activityTab;

  return (
    <Panel
      width="100%"
      height={12}
      accent={palette.accent}
      title="Activity"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      {/* Tabs wrap when the pane is narrow (45% of a small window) —
          without wrap the 5th tab fell off the edge with overflow=hidden and
          became invisible+unclickable even though the digit bindings still
          worked (report 2026-09-03). */}
      <box flexDirection="row" flexWrap="wrap" gap={1} marginBottom={1}>
        <For each={tabs}>
          {(tab, idx) => (
            <box
              height={1}
              overflow="hidden"
              onMouseDown={() => actions.setActivityTab(tab.id)}
            >
              <text fg={active() === tab.id ? palette.accent : palette.fgDim}>
                {active() === tab.id
                  ? `[${idx() + 1} ${tab.label}]`
                  : ` ${idx() + 1} ${tab.label} `}
              </text>
            </box>
          )}
        </For>
      </box>
      <box flexGrow={1} flexDirection="column" overflow="hidden">
        {active() === "agent-logs" && <AgentLogsTab />}
        {active() === "tool-calls" && <ToolCallsTab />}
        {active() === "events" && <EventsTab />}
        {active() === "terminal" && <TerminalTab />}
        {active() === "notifications" && <NotificationsTab />}
        {active() === "todos" && <TodosTab />}
      </box>
    </Panel>
  );
}
