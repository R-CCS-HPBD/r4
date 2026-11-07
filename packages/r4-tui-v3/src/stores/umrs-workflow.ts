/**
 * UMRS Workflow Store — registered server-executed pipelines (not Research Loop).
 *
 * Catalog and execution state for UMRS workflows via the Companion bridge.
 * Research Loop compositional DAG state lives in `workflow.ts`.
 */

import { createStore } from "solid-js/store";
import { getCompanionBridge } from "../bridge";
import { rpc, type RpcMethod, type RpcResult } from "../bridge/rpc";
import { isOfflineBackend } from "../lib/console-mode";
import { formatWorkflowProgress } from "../lib/workflow-activity";
import { projectWorkflowExecutionOntoGraph } from "../lib/workflow-graph-projection";

export interface UmrsWorkflowCatalogItem {
  workflow_name: string;
  name?: string;
  description?: string;
  tags?: string[];
  /** Ownership (2026-10-23): `shared` marks a row `owner` shared with the caller. */
  scope?: string;
  owner?: string;
  shared?: boolean;
  access?: string[];
}

export interface UmrsWorkflowExecution {
  execution_id: string;
  workflow_name?: string | null;
  status: string;
  /** 0..1 fraction on the wire (r4_contracts `WorkflowStatus.progress`);
   * show it through `executionRowDetail` / `formatWorkflowProgress`. */
  progress?: number | null;
  error?: string | null;
  artifacts?: unknown;
  outputs?: unknown;
  ok?: boolean;
  /** LOCAL scope stamp (2026-10-01 (15)) — the project this console triggered
   * the execution under; the wire payloads carry no project field, so this
   * is the only honest scoping signal (never serialized back out). */
  _scopeProjectId?: string;
  /** LOCAL (2026-10-24 review): the live poll got "not found" — UMRS no
   * longer knows this run (record purged, registry reset). The last true
   * status stays, but the run no longer counts as running (badge, Alt+W).
   * A later successful read replaces the record and drops the flag. */
  _stale?: boolean;
}

/** The detail line under an execution row: its error, else its progress
 * as a percent (the bridge sends a 0..1 float; it used to reach the row raw,
 * and the list overlay's `truncate` threw on the number). */
export function executionRowDetail(exec: UmrsWorkflowExecution): string | null {
  return exec.error ?? (formatWorkflowProgress(exec.progress) || null);
}

// The definition types are pure, in lib/workflow-def-types.ts (the card's row
// builders and the VS Code host use them, ADR-103 D-3).
import type { WorkflowDefinitionDetail } from "../lib/workflow-def-types";
export type { WorkflowDefFile, WorkflowDefinitionDetail, WorkflowDefStep } from "../lib/workflow-def-types";

export interface UmrsWorkflowStoreState {
  catalog: UmrsWorkflowCatalogItem[];
  activeExecutions: Record<string, UmrsWorkflowExecution>;
  /** 2026-10-01: definition packages per workflow name; errors per name so
   * a backend without the route degrades to an honest card, not a crash. */
  definitions: Record<string, WorkflowDefinitionDetail>;
  definitionErrors: Record<string, string>;
  lastError: string | null;
  loading: boolean;
}

const initialState: UmrsWorkflowStoreState = {
  catalog: [],
  activeExecutions: {},
  definitions: {},
  definitionErrors: {},
  lastError: null,
  loading: false,
};

const [umrsWorkflowStore, setUmrsWorkflowStore] = createStore<UmrsWorkflowStoreState>(initialState);

function requireLiveBackend(): void {
  if (isOfflineBackend()) {
    throw new Error("UMRS workflow commands require a live backend (not offline/demo mode)");
  }
}

async function bridgeRequest<M extends RpcMethod>(
  method: M,
  params: Record<string, unknown>,
  timeoutMs = 30_000,
): Promise<RpcResult<M>> {
  requireLiveBackend();
  const bridge = getCompanionBridge();
  await bridge.ensureStarted();
  return rpc(bridge, method, params, timeoutMs);
}

/** One-shot auto-open registry (execution_id → fired) — never re-fires even
 * across many status polls, so user shifting panes later is left alone. */
const _autoOpened = new Set<string>();

/**
 * Live execution poll (HPC-visibility leg, 2026-09-27): an execution the
 * store has heard about and that is not terminal is re-polled until it
 * stops moving — steps/status land in the graph and work pane live, instead
 * of freezing at the last manual Enter. One timer per execution; cleared on
 * a terminal status or a not-found answer (status of record stays the last
 * true one — never fabricated). TUI audit r2 (2026-10-17): the poll reads
 * workflow.detail (the only payload with step rows) and re-arms with capped
 * backoff after a transient failure. It polls during a chat turn too: the
 * bridge answers reads mid-turn (plan H-8).
 */
const _pollTimers = new Map<string, ReturnType<typeof setTimeout>>();
const _pollFailures = new Map<string, number>();
const _NON_TERMINAL = new Set([
  "queued",
  "submitted",
  "pending",
  "planned",
  "provisioning",
  "running",
  "waiting_approval",
]);
const EXECUTION_POLL_MS = 4_000;
const EXECUTION_POLL_MAX_MS = 60_000;

function _executionIsLive(status: string): boolean {
  return _NON_TERMINAL.has(status.toLowerCase());
}

/** True while an execution is still moving (the live poll's own test):
 * the status-bar workflow badge and `/workflow graph` read the same set. */
export function executionIsLive(status: string | undefined): boolean {
  return _executionIsLive(status ?? "");
}

function _isNotFound(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return /not[ _-]?found|unknown execution|\b404\b/i.test(message);
}

/** The poll's own not-found test (`/workflow graph <id>` tells an unknown
 * id from a read that failed for another reason with it). */
export function isExecutionNotFound(err: unknown): boolean {
  return _isNotFound(err);
}

function _armExecutionPoll(id: string): void {
  if (_pollTimers.has(id)) return;
  const failures = _pollFailures.get(id) ?? 0;
  const delay = Math.min(EXECUTION_POLL_MS * 2 ** failures, EXECUTION_POLL_MAX_MS);
  _pollTimers.set(id, setTimeout(() => void _pollExecutionTick(id), delay));
}

async function _pollExecutionTick(id: string): Promise<void> {
  _pollTimers.delete(id);
  const current = umrsWorkflowStore.activeExecutions[id];
  if (!current || !_executionIsLive(current.status)) return;
  try {
    await umrsWorkflowActions.refreshStatus(id); // success re-arms via trackExecution
  } catch (err) {
    if (_isNotFound(err)) {
      setUmrsWorkflowStore("activeExecutions", id, "_stale", true);
      return;
    }
    _pollFailures.set(id, (_pollFailures.get(id) ?? 0) + 1);
    _armExecutionPoll(id);
  }
}

function _ensureExecutionPoll(execution: UmrsWorkflowExecution): void {
  const id = execution.execution_id;
  if (!id || !_executionIsLive(execution.status)) return;
  _pollFailures.delete(id); // a tracked record is a successful read
  _armExecutionPoll(id);
}

/** Test hooks: inspect / drop / fire the pending execution polls. */
export function _clearExecutionPollsForTests(): void {
  for (const timer of _pollTimers.values()) clearTimeout(timer);
  _pollTimers.clear();
  _pollFailures.clear();
}

export function _hasExecutionPollForTests(id: string): boolean {
  return _pollTimers.has(id);
}

export async function _fireExecutionPollForTests(id: string): Promise<void> {
  const timer = _pollTimers.get(id);
  if (timer) clearTimeout(timer);
  await _pollExecutionTick(id);
}

/** The raw execution record (workflow.detail, wrapped as {execution: …}):
 * a superset of workflow.status read from the same UMRS route, and the only
 * payload that carries `steps` for the overlay and the graph projection. */
async function _fetchExecutionRecord(executionId: string): Promise<UmrsWorkflowExecution> {
  const { execution: record } = await bridgeRequest("workflow.detail", { execution_id: executionId });
  return {
    ...record,
    execution_id: record.execution_id || executionId,
    status: String(record.status ?? "unknown"),
  };
}

/** Definition fetches in flight (one `workflow.package` read per name). */
const _definitionInflight = new Set<string>();

/** Fetch a workflow's definition once per session: the overlay's DAG and
 * the badge's step total need it (UMRS adds a step row only when the step
 * starts, so the rows alone undercount). A recorded error is final — no
 * retry storm on a backend without the route. */
function _ensureDefinition(name: string | null | undefined): void {
  if (!name || _definitionInflight.has(name)) return;
  if (umrsWorkflowStore.definitions[name] || umrsWorkflowStore.definitionErrors[name]) return;
  _definitionInflight.add(name);
  void umrsWorkflowActions
    .fetchDefinition(name)
    .catch(() => undefined)
    .finally(() => _definitionInflight.delete(name));
}

function trackExecution(execution: UmrsWorkflowExecution): void {
  if (!execution.execution_id) return;
  // Scope-stamp survival (2026-10-01 (15)): status/results refreshes arrive
  // without the stamp — carry the previous one forward, never lose scope.
  const previous = umrsWorkflowStore.activeExecutions[execution.execution_id];
  if (execution._scopeProjectId === undefined && previous?._scopeProjectId !== undefined) {
    execution = { ...execution, _scopeProjectId: previous._scopeProjectId };
  }
  // A successful read: the store MERGES, so drop a not-found mark explicitly.
  setUmrsWorkflowStore("activeExecutions", execution.execution_id, { ...execution, _stale: undefined });
  _ensureExecutionPoll(execution);
  // The badge's step total (2026-10-24 review): a live run's definition is
  // read once when the run is first tracked, not only when its overlay opens.
  if (_executionIsLive(execution.status)) _ensureDefinition(execution.workflow_name);
  // T5 topic-13: live workflow-graph projection — this execution's own step
  // rows replace the graph so the pane follows the in-flight run visually.
  // Capability-detected: no rows land for workflows without step topology.
  try {
    projectWorkflowExecutionOntoGraph(execution);
    // Auto-navigate (once per execution id): agent-started runs should land
    // the graph in the pane the user watches today; never steals focus.
    void import("./index").then(({ workspace, actions, activeTab }) => {
      if (
        (execution.status === "running" || execution.status === "submitted") &&
        !_autoOpened.has(execution.execution_id) &&
        !activeTab().chatStreaming &&
        workspace.paneView === null
      ) {
        _autoOpened.add(execution.execution_id);
        actions.openPane("research");
      }
    });
  } catch {
    // never crash the DOM for graph projection
  }
}

export const umrsWorkflowActions = {
  /** Fetch `name`'s definition unless cached, failed before, or in flight. */
  ensureDefinition(name: string | null | undefined): void {
    _ensureDefinition(name);
  },

  /** Replace the catalog with rows already fetched (the `/workflow list`
   * card), so the show card and overlay label shares from fresh rows. */
  setCatalog(rows: UmrsWorkflowCatalogItem[] | undefined): void {
    if (Array.isArray(rows)) setUmrsWorkflowStore("catalog", rows);
  },

  async loadCatalog(): Promise<void> {
    if (isOfflineBackend()) return;
    setUmrsWorkflowStore({ loading: true, lastError: null });
    try {
      const result = await bridgeRequest("workflow.list", {});
      setUmrsWorkflowStore({
        catalog: result.workflows ?? [],
        loading: false,
      });
    } catch (err) {
      setUmrsWorkflowStore({
        loading: false,
        lastError: err instanceof Error ? err.message : String(err),
      });
    }
  },

  async run(
    workflowName: string,
    options: {
      parameters?: Record<string, unknown>;
      inputFiles?: string[];
      projectId?: string;
      wait?: boolean;
    } = {},
  ): Promise<UmrsWorkflowExecution> {
    setUmrsWorkflowStore("lastError", null);
    // 2026-10-01 (15): default the run's project to the ACTIVE one (caller
    // override still wins) and stamp the tracked execution so the work pane
    // can scope it — the wire payload carries no project field.
    const { activeTab } = await import("./index");
    const projectId =
      (options.projectId ?? activeTab().project?.id ?? activeTab().identityProjectId) || undefined;
    const result = await bridgeRequest("workflow.run", {
      workflow_name: workflowName,
      parameters: options.parameters ?? {},
      input_files: options.inputFiles,
      project_id: projectId,
      wait: Boolean(options.wait),
    });
    trackExecution({ ...result, _scopeProjectId: projectId });
    return result;
  },

  /**
   * An execution started outside this store (2026-10-02: `/workflow trigger`
   * typed in the composer runs through the companion's slash handler) joins
   * the work pane — status fetched, scope-stamped like `run`, then polled.
   * A failed fetch leaves it unlisted; the companion already printed its id.
   */
  async adoptExecution(executionId: string, projectId?: string): Promise<void> {
    try {
      const result = await bridgeRequest("workflow.status", {
        execution_id: executionId,
      });
      trackExecution({ ...result, _scopeProjectId: projectId });
    } catch {
      /* unlisted, never fabricated */
    }
  },

  /** Status + step rows of one execution (Enter on a work-pane row, the
   * list overlay's [s], the live poll) — read through workflow.detail so the
   * overlay and the graph get `steps`; workflow.status strips them. */
  async refreshStatus(executionId: string): Promise<UmrsWorkflowExecution> {
    setUmrsWorkflowStore("lastError", null);
    const result = await _fetchExecutionRecord(executionId);
    trackExecution(result);
    return result;
  },

  async cancel(executionId: string): Promise<UmrsWorkflowExecution> {
    setUmrsWorkflowStore("lastError", null);
    const result = await bridgeRequest("workflow.cancel", {
      execution_id: executionId,
    });
    trackExecution(result);
    return result;
  },

  async fetchResults(executionId: string): Promise<UmrsWorkflowExecution> {
    setUmrsWorkflowStore("lastError", null);
    const result = await bridgeRequest("workflow.results", {
      execution_id: executionId,
    });
    trackExecution(result);
    return result;
  },

  async fetchDetail(executionId: string): Promise<UmrsWorkflowExecution> {
    setUmrsWorkflowStore("lastError", null);
    // The bridge wraps the record as {execution: …}; read flat, the overlay's
    // [a] verb never found a waiting gate (TUI audit 2026-10-17).
    const result = await _fetchExecutionRecord(executionId);
    trackExecution(result);
    return result;
  },

  /**
   * Approve (or reject) a manual-approval gate step on a waiting execution.
   * The step must currently sit at status `waiting_approval` server-side —
   * discover it with waitingApprovalStepName(fetchDetail(id)).
   */
  async approveGate(
    executionId: string,
    stepName: string,
    approved = true,
  ): Promise<UmrsWorkflowExecution> {
    setUmrsWorkflowStore("lastError", null);
    const result = await bridgeRequest("workflow.approve_gate", {
      execution_id: executionId,
      step_name: stepName,
      approved,
    });
    trackExecution(result);
    return result;
  },

  /**
   * Fetch the workflow definition package (2026-10-01): step graph rows,
   * resources, and the registered code files. A backend without the route
   * stores an honest per-name error the card renders verbatim.
   */
  async fetchDefinition(workflowName: string): Promise<WorkflowDefinitionDetail | null> {
    try {
      const wire = await bridgeRequest("workflow.package", { workflow_name: workflowName }, 30_000);
      const detail: WorkflowDefinitionDetail = { ...wire, workflow_name: wire.workflow_name ?? workflowName };
      setUmrsWorkflowStore("definitions", workflowName, detail);
      setUmrsWorkflowStore("definitionErrors", workflowName, "");
      return detail;
    } catch (err) {
      setUmrsWorkflowStore(
        "definitionErrors",
        workflowName,
        err instanceof Error ? err.message : String(err),
      );
      return null;
    }
  },

  clearError(): void {
    setUmrsWorkflowStore("lastError", null);
  },
};

export { umrsWorkflowStore, setUmrsWorkflowStore };
