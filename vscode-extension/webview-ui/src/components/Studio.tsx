/**
 * Studio tab — research projects + UMRS workflow graph.
 *
 * Replaces the "just a listing" experience with an interactive graph:
 * projects on the left rail, workflow catalog with run controls below,
 * and a live DAG of watched executions (step-level nodes, gate approvals).
 */

import { useEffect, useMemo, useState, type ReactElement } from 'react';
import {
  approveGateStep,
  cancelExecution,
  createProject,
  describeWorkflow,
  refreshStudio,
  removeWatch,
  runWorkflow,
  switchProject,
} from '@/lib/actions';
import KnowledgePanel from './KnowledgePanel';
import { useApp, useAppDispatch } from '@/state/store';
import type {
  ExecutionSnapshot,
  WorkflowDescribe,
  WorkflowEntry,
  WorkflowStepSnapshot,
} from '@/types/protocol';
import GraphCanvas, { type GraphNode } from './GraphCanvas';

/** Terminal execution states — gates stop being actionable there. */
const TERMINAL = new Set([
  'completed',
  'succeeded',
  'done',
  'failed',
  'error',
  'cancelled',
  'terminated',
  'stopped',
]);

function isGateWaiting(step: WorkflowStepSnapshot | undefined): boolean {
  if (!step) return false;
  const status = (step.status ?? '').toLowerCase();
  return status.includes('waiting') || status.includes('gate');
}

/** One declared step (string or object) → display name with 1-based fallback. */
function stepDisplayName(step: string | Record<string, unknown>, index: number): string {
  if (typeof step === 'string') return step;
  return String(step.name ?? step.id ?? step.tool ?? `step ${index + 1}`);
}

/** Normalize declared steps (strings or objects) to display names. */
export function describeStepNames(describe: WorkflowDescribe): string[] {
  return (describe.steps ?? []).map(stepDisplayName);
}

/** Selected workflow rendered as a "planned" lane before it runs. */
function buildPlannedNodes(workflowName: string, steps: string[]): GraphNode[] {
  const nodes: GraphNode[] = [
    {
      id: `plan:${workflowName}`,
      kind: 'execution',
      label: workflowName,
      sublabel: 'planned',
      status: 'planned',
      row: 0,
    },
  ];
  steps.forEach((name, index) => {
    nodes.push({
      id: `plan-step:${workflowName}:${index}`,
      kind: 'step',
      label: name,
      status: 'planned',
      parentId: `plan:${workflowName}`,
      row: 0,
      order: index,
    });
  });
  return nodes;
}

/** Build execution+step nodes (one lane per execution) from watched snapshots. */
function buildGraphNodes(
  executions: ExecutionSnapshot[],
  selectedExecutionId: string | undefined,
  selectedStepName: string | undefined,
  planned: { workflowName: string; steps: string[] } | null,
): GraphNode[] {
  const plannedNodes = planned
    ? buildPlannedNodes(planned.workflowName, planned.steps)
    : [];
  const rowOffset = planned ? 1 : 0;
  const nodes: GraphNode[] = [...plannedNodes];
  executions.forEach((execution, index) => {
    nodes.push({
      id: `exec:${execution.execution_id}`,
      kind: 'execution',
      label: execution.workflow_name || execution.execution_id.slice(0, 8),
      sublabel: execution.status ?? 'queued',
      status: (execution.status ?? 'pending').toLowerCase(),
      row: index + rowOffset,
      selected:
        selectedExecutionId === execution.execution_id && !selectedStepName,
      payload: execution,
    });
    (execution.steps ?? []).forEach((step, stepIndex) => {
      const status = (step.status ?? 'pending').toLowerCase();
      nodes.push({
        id: `step:${execution.execution_id}:${step.name ?? stepIndex}`,
        kind: 'step',
        label: step.name ?? `step ${stepIndex + 1}`,
        status,
        parentId: `exec:${execution.execution_id}`,
        row: index + rowOffset,
        order: stepIndex,
        selected:
          selectedExecutionId === execution.execution_id &&
          selectedStepName === (step.name ?? ''),
        gate: isGateWaiting(step),
        payload: step,
      });
    });
  });
  return nodes;
}

/** Map an `exec:` canvas id back to focus state (toggle-select). */
function resolveExecSelection(
  rest: string,
  currentExecutionId: string | undefined,
  currentStepName: string | undefined,
): { executionId?: string; stepName?: string } {
  const deselect = currentExecutionId === rest && !currentStepName;
  return { executionId: deselect ? undefined : rest, stepName: undefined };
}

/** Map a `step:` canvas id back to focus state. */
function resolveStepSelection(
  rest: string,
  executions: ExecutionSnapshot[],
): { executionId?: string; stepName?: string } {
  const splitAt = rest.indexOf(':');
  const executionId = rest.slice(0, splitAt);
  const stepLabel = rest.slice(splitAt + 1);
  const execution = executions.find((e) => e.execution_id === executionId);
  const step = execution?.steps?.find(
    (s, index) => String(s.name ?? index) === stepLabel,
  );
  return { executionId, stepName: step?.name ?? stepLabel };
}

/** Map a canvas node id back to a focus dispatch payload. */
function resolveGraphSelection(
  id: string,
  executions: ExecutionSnapshot[],
  currentExecutionId: string | undefined,
  currentStepName: string | undefined,
): { executionId?: string; stepName?: string } {
  if (id.startsWith('exec:')) {
    return resolveExecSelection(id.slice(5), currentExecutionId, currentStepName);
  }
  if (id.startsWith('step:')) {
    return resolveStepSelection(id.slice(5), executions);
  }
  return { executionId: undefined, stepName: undefined };
}

/** Selected execution + step inspector with cancel/gate actions. */
function StudioDetail(props: {
  execution: ExecutionSnapshot;
  step: WorkflowStepSnapshot | undefined;
  onClose: () => void;
}): ReactElement {
  const { execution, step, onClose } = props;
  const statusKey = (execution.status ?? '').toLowerCase();
  const active = !TERMINAL.has(statusKey);
  return (
    <div className="r4-studio-detail">
      <div className="r4-studio-detail-head">
        <strong>{execution.workflow_name ?? execution.execution_id}</strong>
        <span className={`r4-studio-status r4-studio-status-${statusKey}`}>
          {execution.status ?? 'queued'}
        </span>
        <button type="button" className="r4-btn" title="Dismiss from watchlist" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="r4-studio-detail-body">
        <div>
          <span className="r4-studio-key">execution</span>
          <code>{execution.execution_id}</code>
        </div>
        {execution.error && <div className="r4-studio-error">error: {execution.error}</div>}
      </div>
      {active && (
        <button
          type="button"
          className="r4-btn"
          onClick={() => cancelExecution(execution.execution_id)}
          title="Cancel this execution"
        >
          Cancel execution
        </button>
      )}
      {step && (
        <div className="r4-studio-detail-step">
          <div>
            <span className="r4-studio-key">step</span>
            <code>{step.name}</code>
            <span className={`r4-studio-status r4-studio-status-${(step.status ?? '').toLowerCase()}`}>
              {step.status ?? 'pending'}
            </span>
          </div>
          {isGateWaiting(step) && (
            <div className="r4-studio-gate">
              <button
                type="button"
                className="r4-btn r4-btn-primary"
                onClick={() => approveGateStep(execution.execution_id, step.name ?? '', true)}
              >
                Approve gate
              </button>
              <button
                type="button"
                className="r4-btn"
                onClick={() => approveGateStep(execution.execution_id, step.name ?? '', false)}
              >
                Reject
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Left rail: research project list (switch + create). */
function ProjectRail(): ReactElement {
  const { studio } = useApp();
  const [newProjectId, setNewProjectId] = useState('');

  const onCreateProject = () => {
    const projectId = newProjectId.trim();
    if (!projectId) return;
    createProject(projectId);
    setNewProjectId('');
  };

  const onSwitch = (projectId: string, isActive: boolean) => {
    if (!isActive) switchProject(projectId);
  };

  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>Research projects</span>
        <button
          type="button"
          className="r4-btn"
          title="Reload projects"
          onClick={refreshStudio}
        >
          ⟳
        </button>
      </div>
      {studio.projects.length === 0 && studio.loadedOnce && (
        <div className="r4-studio-empty">No projects yet</div>
      )}
      <ul className="r4-studio-list" role="listbox" aria-label="Projects">
        {studio.projects.map((project) => {
          const active = studio.currentProjectId === project.project_id;
          return (
            <li
              key={project.project_id}
              role="option"
              aria-selected={active}
              className={`r4-studio-list-item${active ? ' r4-studio-list-item-active' : ''}`}
              onClick={() => onSwitch(project.project_id, active)}
              title={project.gateway_url ?? project.project_id}
            >
              <span className="r4-studio-list-title">{project.name || project.project_id}</span>
              {active && <span className="r4-studio-pill">current</span>}
            </li>
          );
        })}
      </ul>
      <div className="r4-studio-create">
        <input
          className="r4-input"
          placeholder="new project id"
          value={newProjectId}
          onChange={(event) => setNewProjectId(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onCreateProject();
          }}
        />
        <button
          type="button"
          className="r4-btn"
          onClick={onCreateProject}
          disabled={!newProjectId.trim()}
        >
          Create
        </button>
      </div>
    </section>
  );
}

/** Describe cache entry → human error string (undefined when entry is fine). */
function describeErrorOf(
  entry: WorkflowDescribe | { error: string } | undefined,
): string | undefined {
  if (!entry || !('error' in entry) || typeof entry.error !== 'string') return undefined;
  return entry.error;
}

/** Run-parameters JSON textarea → object | 'error' | undefined (blank = none). */
function parseWorkflowParams(raw: string): Record<string, unknown> | undefined | 'error' {
  try {
    const parsed: unknown = JSON.parse(raw || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return 'error';
  }
  return undefined;
}

/** Run form: params JSON + input files + wait toggle + run button. */
function WorkflowRunForm(props: {
  runWorkflowName: string;
  projectId: string | undefined;
}): ReactElement {
  const { runWorkflowName, projectId } = props;
  const [runParams, setRunParams] = useState('{}');
  const [paramError, setParamError] = useState('');
  const [runInputFiles, setRunInputFiles] = useState('');
  const [runWait, setRunWait] = useState(false);

  const onRun = () => {
    setParamError('');
    const parameters = parseWorkflowParams(runParams);
    if (parameters === 'error') {
      setParamError('Parameters must be valid JSON.');
      return;
    }
    if (!runWorkflowName) return;
    runWorkflow(runWorkflowName, {
      parameters,
      projectId,
      inputFiles: runInputFiles
        .split(/[,\n]/)
        .map((entry) => entry.trim())
        .filter(Boolean),
      wait: runWait,
    });
    // The execution lands on the watch via refreshStudio — the planned lane
    // clears when the live execution appears.
  };

  return (
    <>
      <textarea
        className="r4-input r4-studio-params"
        rows={2}
        spellCheck={false}
        placeholder='run parameters (JSON, e.g. {"model":"sam3"})'
        value={runParams}
        onChange={(event) => setRunParams(event.target.value)}
      />
      <input
        className="r4-input"
        placeholder="input files (comma or newline separated, optional)"
        value={runInputFiles}
        onChange={(event) => setRunInputFiles(event.target.value)}
      />
      <label className="r4-studio-checkbox">
        <input
          type="checkbox"
          checked={runWait}
          onChange={(event) => setRunWait(event.target.checked)}
        />
        wait for completion
      </label>
      {paramError && <div className="r4-studio-error">{paramError}</div>}
      <button
        type="button"
        className="r4-btn r4-btn-primary"
        onClick={onRun}
        disabled={!runWorkflowName}
        title={runWorkflowName ? `Run ${runWorkflowName}` : 'Select a workflow to run'}
      >
        Run workflow
      </button>
    </>
  );
}

/** One workflow entry in the rail list (click to select/deselect). */
function WorkflowListItem(props: {
  workflow: WorkflowEntry;
  selected: boolean;
  onSelect: (workflowName: string) => void;
}): ReactElement {
  const { workflow, selected, onSelect } = props;
  const name = workflow.workflow_name;
  return (
    <li
      role="option"
      aria-selected={selected}
      className={`r4-studio-list-item${selected ? ' r4-studio-list-item-active' : ''}`}
      onClick={() => onSelect(name)}
      title={workflow.description ?? name}
    >
      <span className="r4-studio-list-title">{workflow.name || workflow.workflow_name}</span>
      {selected && <span className="r4-studio-pill">selected</span>}
    </li>
  );
}

/** Left rail: UMRS workflow catalog + run controls (console v3 parity). */
function WorkflowRail(): ReactElement {
  const { studio } = useApp();
  const dispatch = useAppDispatch();
  // Selection is store-backed: it drives the planned lane on the Executions graph.
  const runWorkflowName = studio.selectedWorkflowName ?? '';

  const onSelectWorkflow = (workflowName: string): void => {
    const next = runWorkflowName === workflowName ? '' : workflowName;
    dispatch({ type: 'ui/selectWorkflow', workflowName: next || undefined });
    // Fetch steps + schema for the preview lane (cached after first load).
    if (next && !studio.workflowDescribes[next]) describeWorkflow(next);
  };

  /** Selected entry — card (below the list) mirrors it for run params. */
  const describeError = describeErrorOf(
    runWorkflowName ? studio.workflowDescribes[runWorkflowName] : undefined,
  );

  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>UMRS workflows</span>
      </div>
      {studio.workflows.length === 0 && studio.loadedOnce && (
        <div className="r4-studio-empty">No workflows registered</div>
      )}
      <ul className="r4-studio-list" role="listbox" aria-label="Workflows">
        {studio.workflows.map((workflow) => (
          <WorkflowListItem
            key={workflow.workflow_name}
            workflow={workflow}
            selected={runWorkflowName === workflow.workflow_name}
            onSelect={onSelectWorkflow}
          />
        ))}
      </ul>
      {describeError && (
        <div className="r4-studio-error" title="Workflow describe failed">
          describe: {describeError}
        </div>
      )}
      <WorkflowRunForm
        runWorkflowName={runWorkflowName}
        projectId={studio.currentProjectId ?? undefined}
      />
    </section>
  );
}

/** Selected workflow's describe → planned-lane spec (null when unavailable). */
function plannedLaneSpec(studio: ReturnType<typeof useApp>['studio']): {
  workflowName: string;
  steps: string[];
} | null {
  const name = studio.selectedWorkflowName;
  if (!name) return null;
  const entry = studio.workflowDescribes[name];
  if (!entry || 'error' in entry) return null;
  const steps = describeStepNames(entry);
  if (steps.length === 0) return null;
  return { workflowName: name, steps };
}

type PlannedLane = { workflowName: string; steps: string[] };

/** Describe details inside the planned-lane card (metadata + required params). */
function PlannedDescribeDetails(props: {
  planned: PlannedLane;
  describe: WorkflowDescribe;
}): ReactElement {
  const { planned, describe } = props;
  const required = Array.isArray(describe.parameter_schema?.required)
    ? (describe.parameter_schema!.required as unknown[])
    : [];
  return (
    <>
      {describe.description && (
        <div>
          <span className="r4-studio-key">description</span>
          <span>{describe.description}</span>
        </div>
      )}
      <div>
        <span className="r4-studio-key">steps</span>
        <span>{planned.steps.length} declared (not running yet)</span>
      </div>
      {required.length > 0 && (
        <div>
          <span className="r4-studio-key">required params</span>
          <span>{(required as string[]).join(', ')}</span>
        </div>
      )}
    </>
  );
}

/** Planned-lane preview card (declared steps before the workflow runs). */
function PlannedLaneCard(props: {
  planned: PlannedLane;
  describe: WorkflowDescribe | { error: string } | undefined;
  onClear: () => void;
}): ReactElement {
  const { planned, describe, onClear } = props;
  const describeOk = describe && !('error' in describe);
  return (
    <div className="r4-studio-detail r4-studio-planned">
      <div className="r4-studio-detail-head">
        <strong>{planned.workflowName}</strong>
        <span className="r4-studio-status r4-studio-status-planned">planned</span>
        <button
          type="button"
          className="r4-btn"
          title="Clear the workflow selection"
          onClick={onClear}
        >
          ✕
        </button>
      </div>
      <div className="r4-studio-detail-body">
        {describeOk && <PlannedDescribeDetails planned={planned} describe={describe} />}
        <div className="r4-studio-hint">
          Set parameters on the left and press “Run workflow” — the planned lane becomes a
          live execution.
        </div>
      </div>
    </div>
  );
}

/** Executions sub-panel: graph canvas + planned/selected detail cards. */
function ExecutionsPanel(props: {
  executions: ExecutionSnapshot[];
  planned: PlannedLane | null;
  describe: WorkflowDescribe | { error: string } | undefined;
  graphNodes: GraphNode[];
  onGraphSelect: (id: string) => void;
  selected: ExecutionSnapshot | undefined;
  selectedStep: WorkflowStepSnapshot | undefined;
  onClearPlanned: () => void;
  onCloseSelected: () => void;
}): ReactElement {
  const {
    executions,
    planned,
    describe,
    graphNodes,
    onGraphSelect,
    selected,
    selectedStep,
    onClearPlanned,
    onCloseSelected,
  } = props;
  const showEmptyHint = executions.length === 0 && !planned;
  return (
    <>
      <div className="r4-studio-graph-wrap">
        {showEmptyHint ? (
          <div className="r4-studio-graph-empty">
            <p>No executions being watched.</p>
            <p className="r4-studio-hint">
              Run a workflow from the left rail — or just select one: its declared steps
              appear here as a planned lane before anything runs.
            </p>
          </div>
        ) : (
          <GraphCanvas nodes={graphNodes} onSelect={onGraphSelect} />
        )}
      </div>

      {planned && (
        <PlannedLaneCard planned={planned} describe={describe} onClear={onClearPlanned} />
      )}

      {selected && (
        <StudioDetail execution={selected} step={selectedStep} onClose={onCloseSelected} />
      )}
    </>
  );
}

export default function Studio() {
  const { studio, connected, studioPanel } = useApp();
  const dispatch = useAppDispatch();
  // Sub-panel is store-backed: the chat surface deep-links into
  // "Knowledge & Loops" after knowledge builds from a chat turn.
  const setStudioPanel = (panel: 'executions' | 'knowledge') =>
    dispatch({ type: 'ui/studioPanel', panel });

  // Load the studio snapshot whenever the tab is opened.
  useEffect(() => {
    refreshStudio();
  }, []);

  const executions = studio.executions ?? [];
  const planned = useMemo(() => plannedLaneSpec(studio), [studio]);
  const selectedDescribe = studio.selectedWorkflowName
    ? studio.workflowDescribes[studio.selectedWorkflowName]
    : undefined;
  const selected: ExecutionSnapshot | undefined = useMemo(
    () => executions.find((execution) => execution.execution_id === studio.selectedExecutionId),
    [executions, studio.selectedExecutionId],
  );
  const selectedStep: WorkflowStepSnapshot | undefined = useMemo(
    () =>
      selected?.steps?.find(
        (step) => (step.name ?? '') === studio.selectedStepName && studio.selectedStepName,
      ),
    [selected, studio.selectedStepName],
  );

  const graphNodes: GraphNode[] = useMemo(
    () =>
      buildGraphNodes(
        executions,
        studio.selectedExecutionId,
        studio.selectedStepName,
        planned,
      ),
    [executions, studio.selectedExecutionId, studio.selectedStepName, planned],
  );

  const handleGraphSelect = (id: string) => {
    if (id.startsWith('plan')) return; // planned lane is a preview, no focus
    const selection = resolveGraphSelection(
      id,
      executions,
      studio.selectedExecutionId,
      studio.selectedStepName,
    );
    dispatch({ type: 'ui/focusExecution', ...selection });
  };

  return (
    <div className="r4-studio" data-connected={connected}>
      <div className="r4-studio-rail">
        <ProjectRail />
        <WorkflowRail />
      </div>

      <div className="r4-studio-main">
        <div className="r4-studio-tabs">
          <button
            type="button"
            className={`r4-btn${studioPanel === 'executions' ? ' r4-btn-primary' : ''}`}
            onClick={() => setStudioPanel('executions')}
          >
            Executions
          </button>
          <button
            type="button"
            className={`r4-btn${studioPanel === 'knowledge' ? ' r4-btn-primary' : ''}`}
            onClick={() => setStudioPanel('knowledge')}
          >
            Knowledge & Loops
          </button>
        </div>

        {studioPanel === 'knowledge' ? (
          <KnowledgePanel />
        ) : (
          <ExecutionsPanel
            executions={executions}
            planned={planned}
            describe={selectedDescribe}
            graphNodes={graphNodes}
            onGraphSelect={handleGraphSelect}
            selected={selected}
            selectedStep={selectedStep}
            onClearPlanned={() =>
              dispatch({ type: 'ui/selectWorkflow', workflowName: undefined })
            }
            onCloseSelected={() => {
              if (!selected) return;
              removeWatch(selected.execution_id);
              dispatch({ type: 'ui/focusExecution', executionId: undefined });
            }}
          />
        )}
      </div>
    </div>
  );
}
