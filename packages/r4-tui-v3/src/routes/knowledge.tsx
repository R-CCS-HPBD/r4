/**
 * Knowledge route — project knowledge readiness dashboard (F4).
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For, Show, createEffect, createMemo, createSignal, on, onCleanup, onMount } from "solid-js";
import { workspace, actions, activeTab } from "../stores";
import { knowledgeStore } from "../stores/knowledge";
import { scienceActions, scienceStore } from "../stores/science";
import { palette, icons } from "../themes";
import { Constellation } from "../components";
import { KeyTrap, keyTrapInsertChar } from "../components/key-trap";
import { KnowledgeGraphViewer } from "../components/knowledge-graph-viewer";
import { ServerGraphPanel } from "../components/server-graph-panel";
import type { KeyEvent } from "@opentui/core";
import {
  KNOWLEDGE_EMPTY,
  knowledgeListUnavailable,
  COVERAGE_EMPTY,
  READINESS_EMPTY,
  PLAN_EMPTY,
  LOOP_POLL_EMPTY,
} from "../lib/empty-hints";
import { isDemoVisuals } from "../lib/console-mode";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import { clampIndex, handleListKey } from "../lib/selectable-list";
import { pipelineStages, pipelineNextAction } from "../lib/knowledge-pipeline";
import { formatQuotaLine } from "../lib/governance-quota";
import { fetchGovernanceOverview } from "../lib/session-changes";
import { Panel } from "../ui/Panel";
import { knowledgeRowLabel } from "../adapters/knowledge-mappers";

/** The empty-list line: the server's reason when its list could not be read
 * (an expired token is not an empty project), else how to add knowledge. */
function KnowledgeEmptyLine() {
  return (
    <Show when={knowledgeStore.listError} fallback={<Line fg={palette.fgDim}>{KNOWLEDGE_EMPTY}</Line>}>
      <Line fg={palette.statusErr}>{knowledgeListUnavailable(knowledgeStore.listError!)}</Line>
    </Show>
  );
}

function spineAvailable(): boolean {
  const platform = workspace.platform;
  return Boolean(platform?.listGraphs && platform?.graphContent);
}

/** The active project's graph (else the first one), its proposals and the
 * consistency/plane counts. `force` reloads loaded graph content (a project
 * switch); the mount keeps content another view already loaded. */
async function loadProjectSpine(force: boolean): Promise<void> {
  await scienceActions.refreshGraphs();
  const projectGraph =
    scienceStore.graphs.find((g) => g.projectId === activeTab().project?.id) ?? scienceStore.graphs[0];
  if (projectGraph && (force || scienceStore.graphContent === null)) {
    await scienceActions.loadGraphContent(projectGraph.graphId);
  }
  void scienceActions.refreshProposals(projectGraph?.graphId);
  void scienceActions.refreshReconcile();
  void scienceActions.refreshStats();
}

const VERIFY_INPUT_COLS = 56;
const VERIFY_DRAFT_MAX = 240;

function windowedDraft(text: string, cols: number): string {
  if (text.length <= cols) return text;
  return `…${text.slice(-(cols - 1))}`;
}

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

function KnowledgePanelFallback(props: { title: string; titleColor: string; message: string }) {
  return (
    <Panel
      flexDirection="column"
      width="33%"
      accent={props.titleColor}
      title={props.title}
      padding={1}
      overflow="hidden"
    >
      <Line fg={palette.fgDim}>{props.message}</Line>
    </Panel>
  );
}

type SelectRow = { id: string; label: string; kind: "knowledge" | "goal" | "task" };

type KnowledgeGraphData = {
  nodes: Array<{ id: string; label: string; type: "goal" | "task" | "knowledge" }>;
  edges: Array<{ from: string; to: string }>;
};

/** List-row glyph: knowledge icon, G for goals, T for tasks. */
function rowKindGlyph(kind: SelectRow["kind"]): string {
  if (kind === "knowledge") return icons.knowledge;
  return kind === "goal" ? "G" : "T";
}

/** Prompt box: verify inquiry entry + backend status lines. */
function VerifyPanel(props: {
  isVerify: () => boolean;
  query: () => string;
  onKeyDown: (event: KeyEvent) => void;
  onPaste: (text: string) => void;
}) {
  return (
    <Panel
      flexDirection="column"
      gap={0}
      overflow="hidden"
      accent={props.isVerify() ? palette.borderFocus : palette.accent}
      title="Verify"
      titleColor={props.isVerify() ? palette.accent2 : palette.accent}
      tone={props.isVerify() ? "raised" : "base"}
      padding={1}
    >
      <box
        height={1}
        width="100%"
        overflow="hidden"
        backgroundColor={props.isVerify() ? palette.msgUserBg : undefined}
        onMouseDown={() => actions.setKnowledgePane("verify")}
      >
        <text
          fg={props.isVerify() ? palette.fgBright : palette.fgDim}
          onMouseDown={() => actions.setKnowledgePane("verify")}
        >
          {props.isVerify()
            ? truncate(`› ${windowedDraft(props.query(), VERIFY_INPUT_COLS)}█`, VERIFY_INPUT_COLS + 4)
            : "›"}
        </text>
        <Show when={props.isVerify()}>
          <KeyTrap paneScoped focused captureText onKeyDown={props.onKeyDown} onPaste={props.onPaste} />
        </Show>
      </box>
      <Show when={knowledgeStore.loading}>
        <Line fg={palette.fgDim}>Querying backend…</Line>
      </Show>
      <Show when={knowledgeStore.error}>
        <Line fg={palette.statusErr}>{knowledgeStore.error!}</Line>
      </Show>
    </Panel>
  );
}

/** Answer card for the last verify inquiry. */
function VerificationResultPanel() {
  return (
    <Show when={knowledgeStore.verification}>
      <Panel
        flexDirection="column"
        accent={palette.knowledge}
        tone="raised"
        padding={1}
        overflow="hidden"
      >
        <Line fg={palette.knowledge}>
          {truncate(knowledgeStore.verification!.answer.replace(/\s+/g, " "), 80)}
        </Line>
        <Show when={knowledgeStore.verification!.evidenceIds.length > 0}>
          <Line fg={palette.fgDim}>
            {truncate(`evidence: ${knowledgeStore.verification!.evidenceIds.join(", ")}`, 80)}
          </Line>
        </Show>
        <Line fg={palette.fgDim}>
          {`confidence ${(knowledgeStore.verification!.confidence * 100).toFixed(0)}%`}
        </Line>
      </Panel>
    </Show>
  );
}

/** Project graph visual: canonical spine (server) → plan projection (local) → demo. */
function KnowledgeGraphPanel(props: {
  showLiveEmpty: () => boolean;
  graph: () => KnowledgeGraphData;
}) {
  return (
    <box flexDirection="column" gap={0} width="45%" height={18} overflow="hidden">
      <Show
        when={scienceStore.graphContent && !isDemoVisuals()}
        fallback={
          <Show
            when={!props.showLiveEmpty()}
            fallback={<KnowledgeEmptyLine />}
          >
            {isDemoVisuals() ? (
              <Constellation
                width={40}
                height={32}
                nodes={props.graph().nodes}
                edges={props.graph().edges}
              />
            ) : (
              <KnowledgeGraphViewer interactive={false} highlightFromWorkspace excludeTypes={["knowledge"]} />
            )}
          </Show>
        }
      >
        <ServerGraphPanel />
      </Show>
    </box>
  );
}

/** One row in the knowledge/goal/task select list. */
function KnowledgeListItem(props: {
  row: SelectRow;
  isSelected: () => boolean;
  onSelect: () => void;
}) {
  const select = () => {
    actions.setKnowledgePane("catalog");
    props.onSelect();
  };
  return (
    <box
      height={1}
      width="100%"
      overflow="hidden"
      backgroundColor={props.isSelected() ? palette.bgActive : undefined}
      onMouseDown={select}
    >
      <text fg={props.isSelected() ? palette.fgBright : palette.fg} onMouseDown={select}>
        {truncate(
          `${props.isSelected() ? "▸" : " "} ${rowKindGlyph(props.row.kind)} ${props.row.label}`,
          48,
        )}
      </text>
    </box>
  );
}

/** Right column: select list + hidden list KeyTrap. */
function KnowledgeListPanel(props: {
  rows: () => SelectRow[];
  selected: () => number;
  onSelect: (index: number) => void;
  onListKey: (event: KeyEvent) => void;
  isVerify: () => boolean;
}) {
  return (
    <box flexDirection="column" gap={0} width="55%" overflow="hidden">
      <Line fg={!props.isVerify() ? palette.accent2 : palette.accent}>Knowledge Bases</Line>
      <Show
        when={props.rows().length > 0}
        fallback={<KnowledgeEmptyLine />}
      >
        <For each={props.rows()}>
          {(row, idx) => (
            <KnowledgeListItem
              row={row}
              isSelected={() => idx() === props.selected()}
              onSelect={() => props.onSelect(idx())}
            />
          )}
        </For>
      </Show>
      <Show when={!props.isVerify()}>
        <box height={1} width={1} overflow="hidden">
          <KeyTrap paneScoped focused onKeyDown={props.onListKey} />
        </box>
      </Show>
    </box>
  );
}

/** Ingestion status card. */
function IngestionPanel() {
  return (
    <Show
      when={knowledgeStore.ingestion}
      fallback={
        <KnowledgePanelFallback
          title="Ingestion"
          titleColor={palette.accent}
          message="No ingestion in poll — /project import then wait for refresh"
        />
      }
    >
      <Panel
        flexDirection="column"
        width="33%"
        accent={palette.accent}
        title="Ingestion"
        padding={1}
        overflow="hidden"
      >
        <Line fg={palette.fgDim}>{`Files: ${knowledgeStore.ingestion!.filesProcessed}`}</Line>
        <Line fg={palette.fgDim}>{`Extracted: ${knowledgeStore.ingestion!.extractedCount}`}</Line>
        <Line fg={palette.fgDim}>{`Graph Nodes: ${knowledgeStore.ingestion!.graphNodes}`}</Line>
        <Line fg={palette.fgDim}>{`Status: ${knowledgeStore.ingestion!.status}`}</Line>
      </Panel>
    </Show>
  );
}

/** Coverage card (demo snapshot hides the gap hint text). */
function CoveragePanel() {
  return (
    <Show
      when={knowledgeStore.coverage}
      fallback={
        <KnowledgePanelFallback
          title="Coverage"
          titleColor={palette.statusOk}
          message={workspace.demoMode ? "Coverage unavailable in this demo snapshot" : COVERAGE_EMPTY}
        />
      }
    >
      <Panel
        flexDirection="column"
        width="33%"
        accent={palette.statusOk}
        title="Coverage"
        tone="raised"
        padding={1}
        overflow="hidden"
      >
        <Line fg={palette.fgDim}>
          {`Coverage: ${(knowledgeStore.coverage!.coveragePercentage * 100).toFixed(0)}%`}
        </Line>
        <Line fg={palette.fgDim}>
          {`Areas: ${knowledgeStore.coverage!.coveredAreas}/${knowledgeStore.coverage!.totalAreas}`}
        </Line>
        <Line fg={palette.statusWarn}>{`Gaps: ${knowledgeStore.coverage!.gaps.length}`}</Line>
      </Panel>
    </Show>
  );
}

/** Readiness headline (ready / can_run / approved flags). */
function readinessHeadline(): string {
  const r = knowledgeStore.readiness!;
  const canRun = r.canRun !== undefined ? ` · can_run=${r.canRun ? "yes" : "no"}` : "";
  const approved = r.userApproved !== undefined ? ` · approved=${r.userApproved ? "yes" : "no"}` : "";
  return `Ready: ${r.ready ? "Yes" : "No"}${canRun}${approved}`;
}

/** Readiness card. */
function ReadinessPanel() {
  return (
    <Show
      when={knowledgeStore.readiness}
      fallback={
        <KnowledgePanelFallback
          title="Readiness"
          titleColor={palette.statusInfo}
          message={workspace.demoMode ? "Readiness unavailable in this demo snapshot" : READINESS_EMPTY}
        />
      }
    >
      <Panel
        flexDirection="column"
        width="34%"
        accent={palette.statusInfo}
        title="Readiness"
        padding={1}
        overflow="hidden"
      >
        <Line fg={palette.fgDim}>{readinessHeadline()}</Line>
        <Line fg={palette.fgDim}>{`Level: ${knowledgeStore.readiness!.readinessLevel}`}</Line>
        <Line fg={palette.statusWarn}>
          {`Blockers: ${knowledgeStore.readiness!.blockers.length}`}
        </Line>
      </Panel>
    </Show>
  );
}

/** Ingestion / Coverage / Readiness row from the knowledge poll. */
function KnowledgeMetricsRow() {
  return (
    <Show
      when={knowledgeStore.ingestion || knowledgeStore.coverage || knowledgeStore.readiness}
      fallback={
        <box width="100%" height={1} padding={1} overflow="hidden">
          <Line fg={palette.fgDim}>
            {`${workspace.knowledge.length} items · r4 knowledge ls lists them with who added each`}
          </Line>
        </box>
      }
    >
      <box flexDirection="row" width="100%" height={8} gap={0} overflow="hidden">
        <IngestionPanel />
        <CoveragePanel />
        <ReadinessPanel />
      </box>
    </Show>
  );
}

/** Research Plan poll snapshot card. */
function ResearchPlanPollPanel() {
  return (
    <Show
      when={knowledgeStore.plan}
      fallback={
        <box width="33%" padding={1} overflow="hidden">
          <Line fg={palette.fgDim}>{PLAN_EMPTY}</Line>
        </box>
      }
    >
      <Panel flexDirection="column" width="33%" accent={palette.accent2} title="Research Plan" padding={1} overflow="hidden">
        <Line fg={palette.fgDim}>{`Plan ID: ${knowledgeStore.plan!.planId}`}</Line>
        <Line fg={palette.fgDim}>{truncate(`Goal: ${knowledgeStore.plan!.researchGoal}`, panePanelWidth(0.33))}</Line>
        <Line fg={palette.fgDim}>{`Objectives: ${knowledgeStore.plan!.objectives.length}`}</Line>
        <Line fg={palette.fgDim}>{`Hypotheses: ${knowledgeStore.plan!.hypotheses.length}`}</Line>
        <Line fg={palette.fgDim}>{`Experiments: ${knowledgeStore.plan!.experiments.length}`}</Line>
        <Line fg={palette.fgDim}>{`Status: ${knowledgeStore.plan!.status}`}</Line>
      </Panel>
    </Show>
  );
}

/** Research Loop poll snapshot card. */
function ResearchLoopPollPanel() {
  return (
    <Show
      when={knowledgeStore.loop}
      fallback={
        <box width="33%" padding={1} overflow="hidden">
          <Line fg={palette.fgDim}>{LOOP_POLL_EMPTY}</Line>
        </box>
      }
    >
      <Panel flexDirection="column" width="33%" accent={palette.tasks} title="Research Loop" tone="raised" padding={1} overflow="hidden">
        <Line fg={palette.fgDim}>{`Loop ID: ${knowledgeStore.loop!.loopId}`}</Line>
        <Line fg={palette.fgDim}>{`Status: ${knowledgeStore.loop!.status}`}</Line>
        <Line fg={palette.fgDim}>{`Phase: ${knowledgeStore.loop!.phase}`}</Line>
        <Line fg={palette.fgDim}>{`Iteration: ${knowledgeStore.loop!.currentIteration}/${knowledgeStore.loop!.maxIterations}`}</Line>
        <Line fg={palette.fgDim}>{`Hypotheses: ${knowledgeStore.loop!.totalHypothesesTested}`}</Line>
        <Line fg={palette.fgDim}>{`Experiments: ${knowledgeStore.loop!.totalExperimentsRun}`}</Line>
        <Line fg={palette.fgDim}>{`Findings: ${knowledgeStore.loop!.totalFindings}`}</Line>
      </Panel>
    </Show>
  );
}

/** Durable research sessions (ADR-025) card. */
function ResearchSessionsPanel() {
  return (
    <Panel flexDirection="column" width="34%" accent={palette.accent} title="Research Sessions" padding={1} overflow="hidden">
      <Show when={knowledgeStore.sessions == null}>
        <text fg={palette.fgDim}>(durable sessions not available on this backend)</text>
      </Show>
      <Show when={knowledgeStore.sessions != null && knowledgeStore.sessions.length === 0}>
        <text fg={palette.fgDim}>(no research sessions on this project)</text>
        <text fg={palette.fgDim}>create one with research_session_create in the Companion</text>
      </Show>
      <For each={(knowledgeStore.sessions ?? []).slice(0, 2)}>
        {(sess) => (
          <>
            <text fg={palette.accent}>{sess.title || "(untitled)"}</text>
            <text fg={palette.fgDim}>{sess.sessionId} · {sess.status} · {sess.authoritativeRuntime}</text>
            <text fg={palette.fgDim}>loops: {(sess.loops ?? []).length}</text>
          </>
        )}
      </For>
      <Show when={(knowledgeStore.sessions ?? []).length > 2}>
        <text fg={palette.fgDim}>… {(knowledgeStore.sessions ?? []).length - 2} more</text>
      </Show>
    </Panel>
  );
}

/** Bottom row: plan / loop / sessions poll cards. */
function PlanLoopSessionsRow() {
  return (
    <box flexDirection="row" width="100%" height={12} gap={0} overflow="hidden">
      <ResearchPlanPollPanel />
      <ResearchLoopPollPanel />
      <ResearchSessionsPanel />
    </box>
  );
}

/** P3: project-scoped build strip — what the pipeline knows, stage by stage.
 * Honest derivation from client-visible artifacts only (see lib/knowledge-pipeline). */
function KnowledgeBuildStrip() {
  const stages = createMemo(() =>
    pipelineStages({
      hasPlan: Boolean(knowledgeStore.plan),
      // The server list (K1.3) is the ingest evidence: the poll no longer
      // reads a local ingestion manifest, so ingest never showed done.
      hasIngestion: Boolean(knowledgeStore.ingestion) || workspace.knowledge.length > 0,
      hasExtraction: Boolean(knowledgeStore.extraction),
      hasCoverage: Boolean(knowledgeStore.coverage),
      hasGraph: Boolean(scienceStore.graphContent) || scienceStore.graphs.length > 0,
      runningJobTypes: workspace.jobs
        .filter((j) => j.status === "running" || j.status === "queued")
        .map((j) => j.type),
    }),
  );
  const glyph = (state: "done" | "running" | "pending") =>
    state === "done" ? "✓" : state === "running" ? "◐" : "·";
  const next = createMemo(() => pipelineNextAction(stages()));
  // One <text> node: sibling <text> flex items get per-item Yoga boxes and the
  // stage names vanish on narrow panes (verified live in PTY 2026-09-15).
  const line = createMemo(() => {
    const body = stages()
      .map((stage) => `${glyph(stage.state)} ${stage.name}`)
      .join(" → ");
    const tail =
      next() == null
        ? " · ready"
        : stages().every((s) => s.state === "pending")
          ? ` · idle — /project ingest ${activeTab().project?.id ?? "<id>"} to start`
          : ` · next: ${next()}`;
    return `build ${activeTab().project?.id ?? "?"}: ${body}${tail}`;
  });
  // D3: quota gauges ride the build strip (governance fetch is lazy).
  const quota = createMemo(() => formatQuotaLine(workspace.governance));
  return (
    <box width="100%" overflow="hidden" flexDirection="column">
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>{line()}</text>
      </box>
      <Show when={quota()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{quota()}</text>
        </box>
      </Show>
    </box>
  );
}

export function KnowledgeRoute() {
  const [query, setQuery] = createSignal("");
  const [selected, setSelected] = createSignal(0);

  const isVerify = () => workspace.knowledgePane === "verify";

  const selectRows = createMemo(() => {
    if (workspace.knowledge.length > 0) {
      return workspace.knowledge.map((k) => ({
        id: k.id,
        label: knowledgeRowLabel(k),
        kind: "knowledge" as const,
      }));
    }
    return [
      ...workspace.goals.map((g) => ({
        id: g.id,
        label: g.statement,
        kind: "goal" as const,
      })),
      ...workspace.tasks.map((t) => ({
        id: t.id,
        label: t.title,
        kind: "task" as const,
      })),
    ];
  });

  const syncSelection = (index: number) => {
    const rows = selectRows();
    const i = clampIndex(index, rows.length);
    setSelected(i);
    const row = rows[i];
    if (row) actions.selectObject(row.id);
  };

  onMount(() => {
    const rows = selectRows();
    if (rows.length === 0) return;
    const idx = rows.findIndex((r) => r.id === workspace.selectedObjectId);
    syncSelection(idx >= 0 ? idx : 0);
  });

  // Canonical spine load (ADR-031): registry → default graph content →
  // consistency projection + plane counts. Server truth only; failures land
  // in scienceStore.error and the graph panel stays on the local projection.
  onMount(() => {
    if (!workspace.governance) void fetchGovernanceOverview();
    if (spineAvailable()) void loadProjectSpine(false);
  });
  // A project switch while the pane is open reloads THAT project's graph
  // (it used to wait for a remount; TUI audit r2 #55).
  createEffect(
    on(
      () => activeTab().project?.id,
      () => {
        if (spineAvailable()) void loadProjectSpine(true);
      },
      { defer: true },
    ),
  );

  createEffect(() => {
    const rows = selectRows();
    const id = workspace.selectedObjectId;
    const idx = rows.findIndex((r) => r.id === id);
    if (idx >= 0) setSelected(idx);
  });

  createEffect(() => {
    if (workspace.paneView !== "knowledge") return;
    const id = workspace.selectedObjectId;
    if (
      id &&
      (workspace.knowledge.some((k) => k.id === id) ||
        workspace.tasks.some((t) => t.id === id) ||
        workspace.goals.some((g) => g.id === id))
    ) {
      return;
    }
    const row = selectRows()[selected()];
    if (row) actions.selectObject(row.id);
  });

  createEffect(() => {
    if (!isVerify()) setQuery("");
  });

  onCleanup(() => {
    if (workspace.knowledgePane === "verify") actions.setKnowledgePane("catalog");
  });

  const setQueryBoth = (value: string) => {
    const clipped = value.slice(0, VERIFY_DRAFT_MAX);
    setQuery(clipped);
    actions.setVerifyDraft(clipped);
  };

  const submitQuery = () => {
    const q = query().trim();
    if (!q) return;
    void actions.verifyKnowledge(q);
  };

  const handleVerifyKey = (event: KeyEvent) => {
    if (event.name === "escape") {
      event.preventDefault();
      actions.setKnowledgePane("catalog");
      setQueryBoth("");
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      submitQuery();
      return;
    }
    if (event.name === "backspace" || event.name === "delete") {
      event.preventDefault();
      setQueryBoth(query().slice(0, -1));
      return;
    }
    if (!event.ctrl && !event.meta) {
      const ch = keyTrapInsertChar(event);
      if (!ch) return;
      event.preventDefault();
      setQueryBoth(query() + ch);
    }
  };

  const handleListKeyDown = (event: KeyEvent) => {
    if (event.name === "escape") {
      event.preventDefault();
      return;
    }
    if (
      handleListKey(event.name, {
        selected: selected(),
        length: selectRows().length,
        ctrl: Boolean(event.ctrl),
        onMove: (i) => {
          event.preventDefault();
          syncSelection(i);
        },
        onEnter: (i) => {
          event.preventDefault();
          syncSelection(i);
          // Stay on F4. Enter selects + shows Context; for a catalog file,
          // prefill Verify with the filename so Enter again asks about it.
          const row = selectRows()[i];
          if (row && row.kind === "knowledge") {
            const name = workspace.knowledge.find((k) => k.id === row.id)?.name ?? row.label;
            actions.setKnowledgePane("verify");
            setQueryBoth(name);
          }
        },
      })
    ) {
      /* handled */
    }
  };

  const hasWorkspaceData = createMemo(
    () => workspace.goals.length > 0 || workspace.tasks.length > 0 || workspace.knowledge.length > 0,
  );
  const hasStoreData = createMemo(
    () =>
      !!(
        knowledgeStore.ingestion ||
        knowledgeStore.coverage ||
        knowledgeStore.readiness ||
        knowledgeStore.plan ||
        knowledgeStore.loop
      ),
  );
  const showLiveEmpty = createMemo(
    () => !workspace.demoMode && !hasWorkspaceData() && !hasStoreData(),
  );

  const graph = createMemo(() => {
    const nodes = [
      ...workspace.goals.map((g) => ({ id: g.id, label: g.statement.slice(0, 24), type: "goal" as const })),
      ...workspace.tasks.map((t) => ({ id: t.id, label: t.title.slice(0, 24), type: "task" as const })),
      ...workspace.knowledge.map((k) => ({ id: k.id, label: k.name.slice(0, 24), type: "knowledge" as const })),
    ];
    const edges = [
      ...workspace.tasks.map((t) => ({ from: t.goalId, to: t.id })),
      ...workspace.knowledge.map((k) => ({ from: k.id, to: workspace.goals[0]?.id ?? k.id })),
    ];
    return { nodes, edges };
  });

  return (
    <box flexDirection="column" width="100%" height="100%" gap={1} overflow="hidden">
      <KnowledgeBuildStrip />
      <VerifyPanel
        isVerify={isVerify}
        query={query}
        onKeyDown={handleVerifyKey}
        onPaste={(text) => setQueryBoth(query() + text.replace(/\r?\n/g, " "))}
      />

      <VerificationResultPanel />

      <box flexDirection="row" gap={2} width="100%" height={18} overflow="hidden">
        <KnowledgeGraphPanel showLiveEmpty={showLiveEmpty} graph={graph} />
        <KnowledgeListPanel
          rows={selectRows}
          selected={selected}
          onSelect={syncSelection}
          onListKey={handleListKeyDown}
          isVerify={isVerify}
        />
      </box>

      <Show
        when={!showLiveEmpty()}
        fallback={
          <box width="100%" height={8} padding={1} overflow="hidden">
            <KnowledgeEmptyLine />
          </box>
        }
      >
        <KnowledgeMetricsRow />

        {/* Bottom: Research Plan & Loop & Sessions */}
        <PlanLoopSessionsRow />
      </Show>
    </box>
  );
}
