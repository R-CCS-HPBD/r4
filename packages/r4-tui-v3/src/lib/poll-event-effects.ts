/**
 * Thin side-effects for backend poll diff events (notify + mascot).
 */

import { actions } from "../stores";
import type { ConsoleEvent } from "../types";

function notifyForEvent(event: ConsoleEvent): void {
  const detail = event.detail ?? "";
  const action = event.action;

  if (action === "endpoint-ready") {
    actions.notify("success", "Endpoint ready", detail || "Endpoint is serving");
    return;
  }
  if (action === "endpoint-failed" || action === "endpoint-degraded") {
    actions.notify("error", "Endpoint issue", detail || action);
    return;
  }
  if (action === "endpoint-created") {
    actions.notify("info", "Endpoint created", detail);
    return;
  }
  if (action === "endpoint-removed") {
    actions.notify("warning", "Endpoint removed", detail);
    return;
  }
  if (action === "job-failed") {
    actions.notify("error", "Job failed", detail);
    return;
  }
  if (action === "job-completed") {
    actions.notify("success", "Job completed", detail);
    return;
  }
  if (action === "job-cancelled") {
    actions.notify("warning", "Job cancelled", detail);
    return;
  }
  if (action === "job-created" || action === "job-running" || action === "job-queued") {
    actions.notify("info", "Job update", `${action.replace("job-", "")}: ${detail}`);
    return;
  }
  if (action === "model-registered") {
    actions.notify("info", "Model registered", detail);
  }
}

function mascotForEvents(events: ConsoleEvent[]): void {
  const hasError = events.some(
    (e) => e.action.includes("failed") || e.action === "endpoint-degraded" || e.action === "poll-failed",
  );
  const hasSuccess = events.some(
    (e) => e.action === "endpoint-ready" || e.action === "job-completed",
  );
  if (hasError) {
    actions.setMascotState("error");
    setTimeout(() => actions.setMascotState("idle"), 2000);
    return;
  }
  if (hasSuccess) {
    actions.setMascotState("success");
    setTimeout(() => actions.setMascotState("idle"), 2000);
    return;
  }
  if (events.length > 0) {
    actions.setMascotState("running");
    setTimeout(() => actions.setMascotState("idle"), 1500);
  }
}

/** Apply user-visible reactions to poll diff events (after they are logged). */
export function applyPollEventSideEffects(events: ConsoleEvent[]): void {
  if (events.length === 0) return;
  for (const event of events) {
    notifyForEvent(event);
  }
  mascotForEvents(events);
}
