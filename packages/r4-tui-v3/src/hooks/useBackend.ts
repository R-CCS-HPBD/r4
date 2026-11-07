/**
 * Backend polling hook.
 */

import { createResource, createSignal, onCleanup } from "solid-js";
import type { DataSource } from "../adapters";
import type { BackendSnapshot } from "../adapters/types";
import { demoToolSelection } from "../adapters/fixtures";
import { actions, workspace, activeTab } from "../stores";
import type { ConsoleEvent, UiEndpoint, UiJob, UiModel, UiSkill } from "../types";
import { ulid } from "ulid";
import { applyPollEventSideEffects } from "../lib/poll-event-effects";
import { endpointLabel } from "../lib/endpoint-label";

function timestamp() {
  return new Date().toISOString();
}

/** A configured public source has no host:port, only a label (wave 82). */
function endpointEventDetail(ep: UiEndpoint): string {
  return ep.isPublic ? endpointLabel(ep) : `${ep.model} @ ${ep.host}:${ep.port}`;
}

function diffEndpoints(prev: UiEndpoint[], next: UiEndpoint[]): ConsoleEvent[] {
  const events: ConsoleEvent[] = [];
  const prevMap = new Map(prev.map((e) => [e.id, e]));
  for (const ep of next) {
    const old = prevMap.get(ep.id);
    if (!old) {
      events.push({ id: ulid(), timestamp: timestamp(), type: "job", actor: "umrs", action: "endpoint-created", detail: endpointEventDetail(ep) });
    } else if (old.status !== ep.status) {
      events.push({ id: ulid(), timestamp: timestamp(), type: "job", actor: "umrs", action: `endpoint-${ep.status}`, detail: `${endpointLabel(ep)} (${ep.id})` });
    }
  }
  for (const old of prev) {
    if (!next.find((e) => e.id === old.id)) {
      events.push({ id: ulid(), timestamp: timestamp(), type: "job", actor: "umrs", action: "endpoint-removed", detail: `${endpointLabel(old)} (${old.id})` });
    }
  }
  return events;
}

function diffJobs(prev: UiJob[], next: UiJob[]): ConsoleEvent[] {
  const events: ConsoleEvent[] = [];
  const prevMap = new Map(prev.map((j) => [j.id, j]));
  for (const job of next) {
    const old = prevMap.get(job.id);
    if (!old) {
      events.push({ id: ulid(), timestamp: timestamp(), type: "job", actor: "umrs", action: "job-created", detail: `${job.type} (${job.id})` });
    } else if (old.status !== job.status) {
      events.push({ id: ulid(), timestamp: timestamp(), type: "job", actor: "umrs", action: `job-${job.status}`, detail: `${job.type} (${job.id})` });
    }
  }
  return events;
}

function diffModels(prev: UiModel[], next: UiModel[]): ConsoleEvent[] {
  const events: ConsoleEvent[] = [];
  const prevMap = new Map(prev.map((m) => [m.id, m]));
  for (const model of next) {
    const old = prevMap.get(model.id);
    if (!old) {
      events.push({ id: ulid(), timestamp: timestamp(), type: "system", actor: "umrs", action: "model-registered", detail: `${model.name}` });
    }
  }
  return events;
}

function emitPollDiff(
  previous: { endpoints: UiEndpoint[]; jobs: UiJob[]; models: UiModel[] },
  result: BackendSnapshot,
): void {
  const events = [
    ...diffEndpoints(previous.endpoints, result.endpoints),
    ...diffJobs(previous.jobs, result.jobs),
    ...diffModels(previous.models, result.models),
  ];
  for (const event of events) actions.addEvent(event);
  applyPollEventSideEffects(events);
  if (events.length === 0) actions.setMascotState("idle");
}

/** Real "load" sample (in-flight jobs + endpoints not yet ready) and the
 * first ready model when none is chosen. */
function noteLiveLoad(result: BackendSnapshot): void {
  const activeJobs = result.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const busyEndpoints = result.endpoints.filter((e) => e.status !== "ready" && e.status !== "stopped").length;
  actions.pushLoadSample(activeJobs + busyEndpoints);
  if (!activeTab().currentModel) {
    const ready = result.endpoints.find((ep) => ep.status === "ready");
    if (ready?.model) actions.setCurrentModel(ready.model);
  }
}

/** Store one answered live poll. A poll with per-probe `problems` (a failed
 * gateway probe, a multi-source listing's `errors`) is DEGRADED on the
 * UMRS/HPC chips and says why once per distinct problem set (audit r2 #45/
 * #52/#56). The gateway answered (an all-probes outage throws instead), so
 * its chip stays connected: gateway-gated work such as the identity-circle
 * refresh keeps running while one secondary source is stale. Returns the
 * problem-set key the caller keeps for that dedupe. */
function applyLiveSnapshot(result: BackendSnapshot, lastProblemsKey: string): string {
  const problems = result.problems ?? [];
  const problemText = problems.join("; ");
  actions.setBackendState({
    endpoints: result.endpoints,
    models: result.models,
    jobs: result.jobs,
    skills: result.skills ?? [],
    loading: false,
    error: problemText || null,
  });
  noteLiveLoad(result);
  const state = problems.length > 0 ? "degraded" : "connected";
  actions.setConnection({ gateway: "connected", umrs: state, hpc: state });
  if (problemText && problemText !== lastProblemsKey) {
    actions.notify("warning", "Backend partly unreachable", problemText);
  }
  return problemText;
}

/** First answered poll after an outage: "reconnected" only for a clean
 * poll — a partial one gets the "partly unreachable" warning instead. */
function announceRecovery(result: BackendSnapshot): void {
  if ((result.problems ?? []).length > 0) return;
  actions.notify("success", "Backend reconnected", "Live data is flowing again.");
}

let activePollerStop: (() => void) | null = null;

/** Stop the active backend poller (tests and hot reload). */
export function stopBackendPoller(): void {
  activePollerStop?.();
  activePollerStop = null;
}

export function createBackendPoller(source: DataSource, intervalMs = 5000) {
  stopBackendPoller();
  const [tick, setTick] = createSignal(0);
  const [errorCount, setErrorCount] = createSignal(0);
  let previous: { endpoints: UiEndpoint[]; jobs: UiJob[]; models: UiModel[]; skills: UiSkill[] } | null = null;
  let degraded = false;
  let problemsKey = "";
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const [snapshot] = createResource(tick, async () => {
    // Offline/demo: keep fixtures + DEMO banner; channels show connected for local UX.
    if (workspace.demoMode || source.name === "offline") {
      try {
        const result = await source.pollBackend();
        actions.setBackendState({
          endpoints: result.endpoints,
          models: result.models,
          jobs: result.jobs,
          skills: result.skills ?? [],
          loading: false,
          error: "DEMO MODE",
        });
        // ADR-037: seed the labeled-demo selection record so the chip suffix
        // renders in DEMO MODE (mark: fabricated; banner carries the honesty).
        actions.applyToolSelection(demoToolSelection.payload);
        actions.setConnection({ gateway: "connected", umrs: "connected", hpc: "connected" });
        if (!activeTab().currentModel) {
          const ready = result.endpoints.find((ep) => ep.status === "ready");
          if (ready?.model) actions.setCurrentModel(ready.model);
        }
        return result;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        actions.setBackendState({
          loading: false,
          error: `DEMO MODE — ${message}`,
        });
        return { endpoints: [], models: [], jobs: [], skills: [] };
      }
    }

    actions.setBackendState({ loading: previous === null, error: null });
    try {
      const result = await source.pollBackend();
      setErrorCount(0);
      if (degraded) {
        degraded = false;
        announceRecovery(result);
      }
      if (previous) emitPollDiff(previous, result);
      previous = { endpoints: result.endpoints, jobs: result.jobs, models: result.models, skills: result.skills ?? [] };
      problemsKey = applyLiveSnapshot(result, problemsKey);
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setErrorCount((c) => c + 1);
      // An outage ends the partial-problem episode: the same problem set on
      // recovery is announced again.
      problemsKey = "";
      if (!degraded) {
        degraded = true;
        actions.notify("error", "Backend unreachable", `Retrying with backoff: ${message}`);
      }
      actions.setBackendState({ loading: false, error: message });
      actions.setConnection({ gateway: "degraded", umrs: "degraded", hpc: "degraded" });
      actions.setMascotState("error");
      actions.addEvent({ id: ulid(), timestamp: timestamp(), type: "system", actor: "console", action: "poll-failed", detail: message });
      return { endpoints: [], models: [], jobs: [], skills: [] };
    }
  });

  const schedule = () => {
    if (stopped) return;
    // Exponential backoff on repeated errors, capped at 8x the base interval.
    const factor = Math.min(2 ** errorCount(), 8);
    const delay = intervalMs * factor;
    timer = setTimeout(() => {
      setTick((t) => t + 1);
      schedule();
    }, delay);
  };
  schedule();
  const stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
  };
  activePollerStop = stop;
  onCleanup(stop);

  return { snapshot, errorCount };
}

export async function loadWorkspace(source: DataSource) {
  actions.setBackendState({ loading: true, error: null });
  try {
    const snap = await source.loadWorkspace();
    if (snap.project) actions.setProject(snap.project);
    if (snap.goals) actions.setGoals(snap.goals);
    if (snap.tasks) actions.setTasks(snap.tasks);
    if (snap.knowledge) actions.setKnowledge(snap.knowledge);
    if (snap.agents) actions.setAgents(snap.agents);
    if (snap.connection) actions.setConnection(snap.connection);
    actions.setBackendState({ loading: false, error: null });
  } catch (err) {
    actions.setBackendState({ loading: false, error: err instanceof Error ? err.message : String(err) });
  }
}
