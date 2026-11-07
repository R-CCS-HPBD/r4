/**
 * Right detail / context panel — selection inspector + space overviews.
 */

import { For, Show, createMemo } from "solid-js";
import { unwrap } from "solid-js/store";
import { workspace, actions, activeTab } from "../stores";
import { knowledgeStore } from "../stores/knowledge";
import { agentRuntime, agentActions } from "../stores/agent-runtime";
import { workflowStore } from "../stores/workflow";
import { loopStore } from "../stores/autonomous-loop";
import { getActiveProjectJobs } from "../commands/project-job-events";
import { palette } from "../themes";
import { truncate, wrapText } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import { endpointLabel, endpointStateText } from "../lib/endpoint-label";

/** Inspector row cap tracks the real pane width — was fixed 24/36/40. */
const ctxCap = () => panePanelWidth(1, 6);
import { askAboutSelectedTask } from "../lib/space-shortcuts";
import { isDemoVisuals } from "../lib/console-mode";
import {
  formatArtifactEntries,
  formatKnowledgeSummary,
  formatScienceSummary,
  formatTrajectoryLine,
  knowledgeHasSignal,
  scienceLoopHasSignal,
} from "../lib/companion-panels";
import { buildProjectGraph } from "../components/knowledge-graph-viewer";
import { t, bold, dim } from "@opentui/core";
import { Panel } from "../ui/Panel";
import type { UiEndpoint, UiJob, KnowledgeObject, ResearchTask, AgentRuntime, WorkflowNode, KnowledgeNode } from "../types";

const CONTEXT_HINTS = {
  settings: "Console status and shortcuts — F12 for the full help overlay.",
} as const;

function SettingsOverview() {
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Settings")}`} />
      <text fg={palette.fgDim}>Status and key reference.</text>
      <text fg={palette.fgDim}>Nothing to configure here yet.</text>
      <text fg={palette.fgDim} marginTop={1}>
        Ctrl+P — command palette
      </text>
    </box>
  );
}

function LoopOverview() {
  const liveRedirect = () => !isDemoVisuals();
  const steps = () => workflowStore.steps.length;
  const max = () => workflowStore.maxSteps;
  const history = () => loopStore.history.length;

  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Workflow builder")}`} />
      <text fg={palette.fgDim}>Local compositional demo (not RC1)</text>
      <Show
        when={liveRedirect()}
        fallback={
          <>
            <text fg={palette.fgDim}>{`Steps: ${steps()}/${max()}`}</text>
            <text fg={palette.fgDim}>{`Loop history: ${history()}`}</text>
            <Show when={workflowStore.assembled}>
              <text fg={palette.statusOk}>Assembled</text>
            </Show>
            <Show when={workflowStore.running}>
              <text fg={palette.statusInfo}>Running</text>
            </Show>
            <Show when={workflowStore.pendingGate}>
              <text fg={palette.statusWarn}>Gate awaiting approval</text>
            </Show>
            <text fg={palette.fgDim} marginTop={1}>
              Add a template or manual step to begin
            </text>
          </>
        }
      >
        <text fg={palette.statusWarn}>Live console — redirect only</text>
        <text fg={palette.fgDim}>RC1 runs from Research (F2), not this panel.</text>
        <text fg={palette.accent} marginTop={1}>
          Enter or F2 → Research · [A] then Run
        </text>
        <text fg={palette.fgDim}>/project run &lt;id&gt; --simulate --approve</text>
      </Show>
    </box>
  );
}

function KnowledgeOverview() {
  const ing = () => knowledgeStore.ingestion;
  const plan = () => knowledgeStore.plan;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Knowledge")}`} />
      <text fg={palette.fgDim}>import → init → plan · ↑↓ for details</text>
      <Show when={ing()}>
        <text fg={palette.fgDim}>
          {`Ingestion: ${ing()!.filesProcessed} files · ${ing()!.status}`}
        </text>
      </Show>
      <Show when={plan()?.researchGoal}>
        <text fg={palette.fgDim}>{truncate(plan()!.researchGoal, 48)}</text>
      </Show>
      <Show when={!ing() && workspace.knowledge.length === 0}>
        <text fg={palette.fgDim}>No KB rows yet — use Mission Control [A] Import</text>
      </Show>
      <Show when={workspace.knowledge.length === 0 && (workspace.tasks.length > 0 || workspace.goals.length > 0)}>
        <text fg={palette.fgDim}>↑↓ Project graph nodes for Context</text>
      </Show>
    </box>
  );
}

function InfrastructureOverview() {
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Infrastructure")}`} />
      <text fg={palette.fgDim}>
        {`Endpoints ${workspace.endpoints.length} · Jobs ${workspace.jobs.length} · Models ${workspace.models.length}`}
      </text>
      <text fg={palette.fgDim}>Tab lists · ↑↓ select · s/c/r/l</text>
      <text fg={palette.fgDim}>Chat model: /models</text>
    </box>
  );
}

function TasksOverview() {
  const pending = () => workspace.tasks.filter((t) => t.status === "pending").length;
  const running = () => workspace.tasks.filter((t) => t.status === "running").length;
  const done = () => workspace.tasks.filter((t) => t.status === "completed").length;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Tasks")}`} />
      <text fg={palette.fgDim}>From the research plan — monitor and hand off</text>
      <text fg={palette.fgDim}>{`${workspace.tasks.length} total · ${pending()} pending · ${running()} running · ${done()} done`}</text>
      <text fg={palette.fgDim}>↑↓ select · Enter ask R4 · r Research · k Knowledge</text>
    </box>
  );
}

/** Research empty state — next actions, not a Mission Control clone. */
function ResearchOverview() {
  const project = () => activeTab().project;
  const pendingCount = () => agentActions.getPendingApprovals().length;
  const jobs = () => [...getActiveProjectJobs().entries()];
  const taskCount = () => workspace.tasks.length;

  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("What’s next")}`} />
      <Show
        when={project()}
        fallback={<text fg={palette.statusWarn}>No project — [A] → New</text>}
      >
        <text fg={palette.fg}>{truncate(project()!.id, 24)}</text>
      </Show>
      <Show when={jobs()[0]}>
        <text fg={palette.statusInfo}>
          {truncate(`Job: ${jobs()[0]![1]} (${jobs()[0]![0].slice(0, 8)}…)`, 26)}
        </text>
      </Show>
      <Show when={pendingCount() > 0}>
        <text fg={palette.statusWarn}>{`Approvals: ${pendingCount()}`}</text>
      </Show>
      <Show when={taskCount() > 0}>
        <text fg={palette.fgDim}>{`${taskCount()} plan tasks`}</text>
      </Show>
      <text fg={palette.fgDim} marginTop={1}>
        Tab cycles panes
      </text>
    </box>
  );
}

function CompanionSessionPanel() {
  // W1c: artifacts / science loop / knowledge — bridge-polled research rail.
  const artifacts = () => formatArtifactEntries(activeTab().turnArtifacts.slice(-6));
  const science = () => activeTab().scienceLoop;
  const kb = () => activeTab().knowledgeBase;

  return (
    <>
      <Show when={artifacts().length > 0}>
        <box flexDirection="column" gap={0} marginTop={1}>
          <text fg={palette.fgDim}>artifacts</text>
          <For each={artifacts()}>
            {(entry) => <text fg={palette.fgDim}>{`${entry.icon} ${entry.label}`}</text>}
          </For>
        </box>
      </Show>
      <Show when={scienceLoopHasSignal(science())}>
        <box flexDirection="column" gap={0} marginTop={1}>
          <text fg={palette.knowledge}>science</text>
          <text fg={palette.fgDim}>{formatScienceSummary(science()?.stats ?? {})}</text>
          <For each={(science()?.trajectory ?? []).slice(-3)}>
            {(iter) => <text fg={palette.fgDim}>{formatTrajectoryLine(iter)}</text>}
          </For>
        </box>
      </Show>
      <Show when={knowledgeHasSignal(kb())}>
        <box flexDirection="column" gap={0} marginTop={1}>
          <text fg={palette.knowledge}>knowledge</text>
          <text fg={palette.fgDim}>{formatKnowledgeSummary(kb()?.stats ?? {})}</text>
        </box>
      </Show>
    </>
  );
}

function DashboardOverview() {
  const project = () => activeTab().project;
  const readyEndpoints = () => workspace.endpoints.filter((ep) => ep.status === "ready").length;
  const runningJobs = () => workspace.jobs.filter((j) => j.status === "running").length;

  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Overview")}`} />
      <Show when={project()?.description}>
        <text fg={palette.fgDim}>{project()!.description.slice(0, 72)}</text>
      </Show>
      <text fg={palette.fgDim}>{`Status: ${project()?.status ?? "—"}`}</text>
      <text fg={palette.fgDim}>
        {`Ready endpoints: ${readyEndpoints()}/${workspace.endpoints.length}`}
      </text>
      <text fg={palette.fgDim}>{`Jobs running: ${runningJobs()}`}</text>
      <CompanionSessionPanel />
      <box flexDirection="column" gap={0} marginTop={1}>
        <text fg={palette.fgDim}>Jump to</text>
        <text fg={palette.knowledge} onMouseDown={() => actions.setSpace("knowledge")}>
          [F4] Knowledge
        </text>
        <text fg={palette.infrastructure} onMouseDown={() => actions.setSpace("infrastructure")}>
          [F5] Infrastructure
        </text>
      </box>
    </box>
  );
}

const INSPECTOR_TABS = ["details", "json"] as const;

type SelectedObject =
  | { kind: "endpoint"; data: UiEndpoint }
  | { kind: "job"; data: UiJob }
  | { kind: "task"; data: ResearchTask }
  | { kind: "knowledge"; data: KnowledgeObject }
  | { kind: "goal"; data: { id: string; statement: string; status: string } }
  | { kind: "hypothesis"; data: { id: string; statement: string; confidence: number } }
  | { kind: "workflow"; data: WorkflowNode }
  | { kind: "agent"; data: AgentRuntime }
  | { kind: "unknown"; id: string };

/** Match catalog rows by id, filename, or id slug (text-README-…). */
function knowledgeMatches(k: { id: string; name: string }, id: string): boolean {
  if (k.id === id || k.name === id) return true;
  const lower = id.toLowerCase();
  const name = k.name.toLowerCase();
  if (name && (lower.includes(name) || name.includes(lower))) return true;
  const stem = name.replace(/\.[^.]+$/, "");
  return stem.length >= 3 && lower.includes(stem);
}

/** wf:/agent: prefixed ids resolve against runtime stores, not workspace lists. */
function resolvePrefixedId(id: string): SelectedObject | null {
  if (id.startsWith("wf:")) {
    const nodeId = id.slice(3);
    const node = unwrap(agentRuntime.workflowGraph.nodes).find((n) => n.id === nodeId);
    if (node) return { kind: "workflow", data: node };
  }
  if (id.startsWith("agent:")) {
    const agentId = id.slice(6);
    const agent = agentActions.getAgentById(agentId);
    if (agent) return { kind: "agent", data: agent };
  }
  return null;
}

/** Direct workspace list lookups, first match wins (endpoint → … → hypothesis). */
function resolveDirectEntity(id: string): SelectedObject | null {
  const endpoint = unwrap(workspace.endpoints).find((e) => e.id === id);
  if (endpoint) return { kind: "endpoint", data: endpoint };
  const job = unwrap(workspace.jobs).find((j) => j.id === id);
  if (job) return { kind: "job", data: job };
  const task = unwrap(workspace.tasks).find((t) => t.id === id);
  if (task) return { kind: "task", data: task };
  const knowledge = unwrap(workspace.knowledge).find((k) => knowledgeMatches(k, id));
  if (knowledge) return { kind: "knowledge", data: knowledge };
  const goal = unwrap(workspace.goals).find((g) => g.id === id);
  if (goal) return { kind: "goal", data: { id: goal.id, statement: goal.statement, status: goal.status } };
  const hyp = unwrap(agentRuntime.missionControl.hypotheses).find((h) => h.id === id);
  if (hyp) return { kind: "hypothesis", data: hyp };
  return null;
}

/** Goal/hypothesis graph nodes carry optional status/confidence metadata. */
function graphMetaNodeSelection(node: KnowledgeNode): SelectedObject | null {
  if (node.type === "goal") {
    return { kind: "goal", data: { id: node.id, statement: node.label, status: node.status ?? "active" } };
  }
  if (node.type === "hypothesis") {
    return {
      kind: "hypothesis",
      data: { id: node.id, statement: node.label, confidence: node.confidence ?? 0 },
    };
  }
  return null;
}

/** Map a project-graph node to a synthetic selection; null when type unmatched. */
function graphNodeSelection(node: KnowledgeNode): SelectedObject | null {
  if (node.type === "knowledge") {
    return {
      kind: "knowledge",
      data: { id: node.id, name: node.label, status: "ready", documentCount: 1 },
    };
  }
  if (node.type === "task" || node.type === "experiment") {
    return {
      kind: "task",
      data: { id: node.id, goalId: "", title: node.label, status: "pending" },
    };
  }
  return graphMetaNodeSelection(node);
}

/** Exported for tests. */
export function resolveContextSelection(id: string): SelectedObject {
  const prefixed = resolvePrefixedId(id);
  if (prefixed) return prefixed;
  const direct = resolveDirectEntity(id);
  if (direct) return direct;
  const node = buildProjectGraph().nodes.find((n) => n.id === id || n.label === id);
  if (node) {
    const selected = graphNodeSelection(node);
    if (selected) return selected;
  }
  return { kind: "unknown", id };
}

function resolveSelected(id: string): SelectedObject {
  return resolveContextSelection(id);
}

/** A public source has no host:port (it is a URL); wave 82. */
function endpointHostText(ep: UiEndpoint): string {
  return ep.isPublic ? ep.url : `${ep.host}:${ep.port}`;
}

function EndpointDetails(props: { ep: UiEndpoint }) {
  const ep = props.ep;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Endpoint")} ${ep.id}`} />
      <text children={t`${dim("Model:")} ${endpointLabel(ep)}`} />
      <text children={t`${dim("Status:")} ${endpointStateText(ep)}`} />
      <text children={t`${dim("Host:")} ${endpointHostText(ep)}`} />
      <Show when={ep.status === "ready" || ep.status === "provisioning"}>
        <text fg={palette.statusWarn} onMouseDown={() => void actions.stopEndpoint(ep.id)}>
          [Stop endpoint]
        </text>
      </Show>
    </box>
  );
}

function JobDetails(props: { job: UiJob }) {
  const job = props.job;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Job")} ${job.id}`} />
      <text children={t`${dim("Type:")} ${job.type}`} />
      <text children={t`${dim("Status:")} ${job.status}`} />
      <box flexDirection="row" gap={2}>
        <Show when={job.status === "running" || job.status === "queued"}>
          <text fg={palette.statusWarn} onMouseDown={() => void actions.cancelJob(job.id)}>
            [Cancel]
          </text>
        </Show>
        <text fg={palette.fgDim} onMouseDown={() => void actions.showJobLogs(job.id)}>
          [Details]
        </text>
      </box>
    </box>
  );
}

function TaskDetails(props: { task: ResearchTask }) {
  const task = props.task;
  const goal = workspace.goals.find((g) => g.id === task.goalId);
  const titleLines = wrapText(task.title, 24, 6);
  const goalLines = goal ? wrapText(goal.statement, 24, 3) : [];
  return (
    <box flexDirection="column" gap={0} overflow="hidden">
      <text children={t`${bold("Task")}`} />
      <text fg={palette.fgDim}>Selection inspector · ↑↓ changes this</text>
      <For each={titleLines}>{(line) => <text fg={palette.fgBright}>{line}</text>}</For>
      <text children={t`${dim("Status:")} ${task.status}`} />
      <text fg={palette.fgDim}>{truncate(`Id: ${task.id}`, ctxCap())}</text>
      <Show when={goal}>
        <text fg={palette.fgDim} marginTop={1}>
          Goal
        </text>
        <For each={goalLines}>{(line) => <text fg={palette.fg}>{line}</text>}</For>
      </Show>
      <Show when={knowledgeStore.plan}>
        <text fg={palette.fgDim} marginTop={1}>
          {truncate(`Plan ${knowledgeStore.plan!.planId}`, ctxCap())}
        </text>
        <Show when={knowledgeStore.plan!.researchGoal}>
          <For each={wrapText(knowledgeStore.plan!.researchGoal, 24, 2)}>
            {(line) => <text fg={palette.fgDim}>{line}</text>}
          </For>
        </Show>
      </Show>
      <box flexDirection="column" gap={0} marginTop={1}>
        <text fg={palette.fgDim}>Actions for this task</text>
        <text
          fg={palette.accent}
          onMouseDown={() => {
            actions.selectObject(task.id);
            askAboutSelectedTask();
          }}
        >
          [Enter] ask R4 in Chat
        </text>
        <Show when={workspace.paneView !== "research"}>
          <text fg={palette.tasks} onMouseDown={() => actions.setSpace("research")}>
            [r] open Research
          </text>
        </Show>
        <text fg={palette.knowledge} onMouseDown={() => actions.setSpace("knowledge")}>
          [k] open Knowledge
        </text>
      </box>
    </box>
  );
}

function KnowledgeDetails(props: { k: KnowledgeObject }) {
  const k = props.k;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Catalog file")}`} />
      <text fg={palette.fgBright}>{truncate(k.name, ctxCap())}</text>
      <text children={t`${dim("Status:")} ${k.status}`} />
      <text children={t`${dim("Documents:")} ${k.documentCount}`} />
      <Show when={k.contributors?.length}>
        <text children={t`${dim("Added by:")} ${k.contributors!.join(", ")}`} />
      </Show>
      <text fg={palette.fgDim}>{truncate(k.id, ctxCap())}</text>
    </box>
  );
}

function GoalDetails(props: { g: { id: string; statement: string; status: string } }) {
  const g = props.g;
  const linked = workspace.tasks.filter((t) => t.goalId === g.id);
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Goal")}`} />
      <text fg={palette.fg}>{g.statement}</text>
      <text children={t`${dim("Status:")} ${g.status}`} />
      <Show when={linked.length > 0}>
        <text fg={palette.fgDim}>{`${linked.length} linked tasks`}</text>
      </Show>
    </box>
  );
}

function HypothesisDetails(props: { h: { id: string; statement: string; confidence: number } }) {
  const h = props.h;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Hypothesis")}`} />
      <text fg={palette.fg}>{truncate(h.statement, ctxCap())}</text>
      <text children={t`${dim("Confidence:")} ${(h.confidence * 100).toFixed(0)}%`} />
    </box>
  );
}

function WorkflowNodeDetails(props: { node: WorkflowNode }) {
  const node = props.node;
  const jobId = node.metadata ? node.metadata.jobId : undefined;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Workflow node")}`} />
      <text fg={palette.fg}>{truncate(node.label, ctxCap())}</text>
      <text children={t`${dim("Type:")} ${node.type}`} />
      <text children={t`${dim("Status:")} ${node.status}`} />
      <Show when={typeof jobId === "string"}>
        <text fg={palette.fgDim}>{truncate(`Job: ${String(jobId)}`, 40)}</text>
      </Show>
    </box>
  );
}

function AgentDetails(props: { a: AgentRuntime }) {
  const a = props.a;
  return (
    <box flexDirection="column" gap={1}>
      <text children={t`${bold("Agent")}`} />
      <text fg={palette.fg}>{`${a.role} · ${a.state}`}</text>
      <text fg={palette.fgDim}>{truncate(a.currentTask ?? a.goal, ctxCap())}</text>
      <Show when={a.progress !== undefined}>
        <text fg={palette.statusInfo}>{`Progress: ${Math.round(a.progress!)}%`}</text>
      </Show>
    </box>
  );
}

function UnknownDetails(props: { id: string }) {
  return (
    <box flexDirection="column" gap={1}>
      <text fg={palette.fgDim}>No details for this row</text>
      <text fg={palette.fg}>{truncate(props.id || "—", ctxCap())}</text>
    </box>
  );
}

/** Second dispatch half — research/meta kinds plus the fallback. */
function MetaDetails(props: { sel: SelectedObject }) {
  const sel = props.sel;
  if (sel.kind === "goal") return <GoalDetails g={sel.data} />;
  if (sel.kind === "hypothesis") return <HypothesisDetails h={sel.data} />;
  if (sel.kind === "workflow") return <WorkflowNodeDetails node={sel.data} />;
  if (sel.kind === "agent") return <AgentDetails a={sel.data} />;
  if (sel.kind === "unknown") return <UnknownDetails id={sel.id} />;
  return <text fg={palette.fgDim}>No details for this row</text>;
}

function DetailsTab(props: { selected: SelectedObject }) {
  const sel = props.selected;
  if (sel.kind === "endpoint") return <EndpointDetails ep={sel.data} />;
  if (sel.kind === "job") return <JobDetails job={sel.data} />;
  if (sel.kind === "task") return <TaskDetails task={sel.data} />;
  if (sel.kind === "knowledge") return <KnowledgeDetails k={sel.data} />;
  return <MetaDetails sel={sel} />;
}

function JsonTab(props: { selected: SelectedObject }) {
  const json = createMemo(() => {
    const sel = props.selected;
    if (sel.kind === "unknown") return JSON.stringify({ id: sel.id }, null, 2);
    return JSON.stringify(sel.data, null, 2);
  });
  const lines = createMemo(() => json().split("\n").slice(0, 16));
  return (
    <box flexDirection="column" gap={0}>
      <For each={lines()}>{(line) => <text fg={palette.fgDim}>{line}</text>}</For>
    </box>
  );
}

/** True when the selection kind is inspectable on the knowledge space. */
function isKnowledgeSpaceKind(kind: SelectedObject["kind"]): boolean {
  return (
    kind === "knowledge" ||
    kind === "task" ||
    kind === "goal" ||
    kind === "hypothesis"
  );
}

/** Knowledge-space selection: keep research kinds, else fall back to first object. */
function resolveKnowledgeSpaceSelection(
  resolved: SelectedObject | null,
): SelectedObject | null {
  if (resolved && isKnowledgeSpaceKind(resolved.kind)) return resolved;
  const fallback = workspace.knowledge[0];
  return fallback ? { kind: "knowledge" as const, data: fallback } : null;
}

export function ContextPanel() {
  const selectedId = () => workspace.selectedObjectId;
  const selected = createMemo(() => {
    const id = selectedId();
    void workspace.knowledge.length;
    void workspace.tasks.length;
    void workspace.goals.length;
    void workspace.knowledge[0]?.id;
    void workspace.knowledge[0]?.name;
    void workspace.endpoints.length;
    void agentRuntime.missionControl.hypotheses.length;
    const resolved = id ? resolveSelected(id) : null;
    if (workspace.paneView !== "knowledge") return resolved;
    return resolveKnowledgeSpaceSelection(resolved);
  });
  const tab = () => (workspace.contextPanelTab === "json" ? "json" : "details");

  return (
    <Panel
      width="100%"
      flexShrink={0}
      minHeight={12}
      accent={palette.accent2}
      title="Context"
      tone="raised"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <Show
        when={workspace.paneView === "settings" ? null : selected()}
        fallback={
          workspace.paneView === "dashboard" ? (
            <DashboardOverview />
          ) : workspace.paneView === "research" ? (
            <ResearchOverview />
          ) : workspace.paneView === "loop" ? (
            <LoopOverview />
          ) : workspace.paneView === "knowledge" ? (
            <KnowledgeOverview />
          ) : workspace.paneView === "infrastructure" ? (
            <InfrastructureOverview />
          ) : workspace.paneView === "tasks" ? (
            <TasksOverview />
          ) : workspace.paneView === "settings" ? (
            <SettingsOverview />
          ) : (
            <text fg={palette.fgDim}>{CONTEXT_HINTS.settings}</text>
          )
        }
      >
        {(sel) => (
          <>
            <box flexDirection="row" gap={1} height={1} marginBottom={1}>
              <For each={INSPECTOR_TABS}>
                {(name) => (
                  <text
                    fg={tab() === name ? palette.accent2 : palette.fgDim}
                    onMouseDown={() =>
                      actions.setContextPanelTab(name === "details" ? "overview" : "json")
                    }
                  >
                    {tab() === name ? `[${name}]` : ` ${name} `}
                  </text>
                )}
              </For>
            </box>
            <box flexDirection="column" gap={1} overflow="hidden">
              <Show when={sel()} keyed>
                {(s) => (tab() === "json" ? <JsonTab selected={s} /> : <DetailsTab selected={s} />)}
              </Show>
            </box>
          </>
        )}
      </Show>
    </Panel>
  );
}
