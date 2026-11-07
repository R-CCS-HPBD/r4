/**
 * Human-readable labels for UMRS workflow_* companion tools.
 * Ported from tui-js workflow-activity.mjs.
 */

import { basename, parseToolArgs, type ToolArgs } from "./tool-args";

export function isWorkflowTool(name: string): boolean {
  return String(name || "").startsWith("workflow_");
}

type WorkflowPayload = Record<string, unknown>;

export function parseWorkflowPayload(
  message: unknown,
  fullResult: unknown,
): WorkflowPayload | null {
  const msg = message != null ? String(message).trim() : "";
  const full = fullResult != null ? String(fullResult).trim() : "";
  const candidates: string[] = [];
  if (full) candidates.push(full);
  if (msg && msg !== full) candidates.push(msg);
  for (const raw of candidates) {
    const text = raw.trim();
    if (!text.startsWith("{") && !text.startsWith("[")) continue;
    try {
      const parsed = JSON.parse(text) as unknown;
      if (parsed && typeof parsed === "object") return parsed as WorkflowPayload;
    } catch {
      // try next candidate
    }
  }
  return null;
}

export function isWorkflowError(payload: WorkflowPayload | null): boolean {
  if (!payload || typeof payload !== "object") return false;
  if (payload.error === "workflow_error") return true;
  if (typeof payload.code === "string" && payload.code.startsWith("workflow_")) return true;
  if (payload.error && !payload.execution_id && !payload.workflows) return true;
  return false;
}

function workflowNameFromArgs(args: ToolArgs): string {
  return String(
    (args as Record<string, unknown>).workflow_name ??
      (args as Record<string, unknown>).workflow_id ??
      (args as Record<string, unknown>).name ??
      "",
  );
}

function executionIdFromArgs(args: ToolArgs): string {
  return String(
    (args as Record<string, unknown>).execution_id ??
      (args as Record<string, unknown>).executionId ??
      "",
  );
}

/** Workflow progress as the TUI shows it: a 0..1 fraction (UMRS
 * `WorkflowStatus.progress`) as a whole percent, a larger number as a
 * percent as sent, a string verbatim, nothing for null/empty. */
export function formatWorkflowProgress(progress: unknown): string {
  if (progress == null || progress === "") return "";
  if (typeof progress === "number") {
    if (progress <= 1) return `${Math.round(progress * 100)}%`;
    return `${progress}%`;
  }
  return String(progress);
}

/** `sep` + the formatted progress, or "" when there is none to show. */
export function progressSuffix(progress: unknown, sep: string): string {
  const text = formatWorkflowProgress(progress);
  return text ? `${sep}${text}` : "";
}

export function extractWorkflowArtifacts(payload: WorkflowPayload | null): string[] {
  if (!payload || typeof payload !== "object") return [];
  const artifacts = payload.artifacts;
  if (!Array.isArray(artifacts)) return [];
  return artifacts.map((item) => String(item)).filter(Boolean);
}

function artifactCount(payload: WorkflowPayload | null): number {
  return extractWorkflowArtifacts(payload).length;
}

export function formatWorkflowInProgress(name: string, rawArgs: unknown): string {
  const args: ToolArgs = typeof rawArgs === "string" ? parseToolArgs(rawArgs) : ((rawArgs as ToolArgs) || {});
  const wfName = workflowNameFromArgs(args);
  const execId = executionIdFromArgs(args);

  switch (name) {
    case "workflow_list":
      return "Listing UMRS workflows…";
    case "workflow_describe":
      return wfName ? `Describing ${wfName}…` : "Describing workflow…";
    case "workflow_run":
    case "workflow_trigger":
      return wfName ? `Running ${wfName}…` : "Running workflow…";
    case "workflow_status":
      return execId ? `Checking ${execId}…` : "Checking workflow status…";
    case "workflow_wait":
      if (wfName && execId) return `Waiting on ${wfName} (${execId})…`;
      if (execId) return `Waiting on ${execId}…`;
      return "Waiting for workflow…";
    case "workflow_results":
      return execId ? `Fetching results for ${execId}…` : "Fetching workflow results…";
    case "workflow_cancel":
      return execId ? `Cancelling ${execId}…` : "Cancelling workflow…";
    case "workflow_upload": {
      const path = String(
        (args as Record<string, unknown>).workflow_path ??
          (args as Record<string, unknown>).path ??
          "",
      );
      return path ? `Uploading ${basename(path)}…` : "Uploading workflow…";
    }
    default:
      return `${name}…`;
  }
}

export function formatWorkflowDone(
  name: string,
  rawArgs: unknown,
  message: unknown,
  fullResult: unknown,
): string {
  const args: ToolArgs = typeof rawArgs === "string" ? parseToolArgs(rawArgs) : ((rawArgs as ToolArgs) || {});
  const payload = parseWorkflowPayload(message, fullResult);

  if (payload && isWorkflowError(payload)) {
    const detail = String(payload.message || payload.error || "workflow failed");
    const target = workflowNameFromArgs(args) || executionIdFromArgs(args) || "workflow";
    return `${target} failed: ${detail}`;
  }

  if (!payload) {
    const summary = String(message || "").trim();
    return summary || name;
  }

  const wfName = String(payload.workflow_name || payload.workflow_id || "") || workflowNameFromArgs(args);
  const execId = String(payload.execution_id || "") || executionIdFromArgs(args);
  const status = payload.status ? String(payload.status) : "";
  const progress = formatWorkflowProgress(payload.progress);

  switch (name) {
    case "workflow_list": {
      const count =
        (payload.count as number | undefined) ??
        (Array.isArray(payload.workflows) ? payload.workflows.length : null);
      if (count != null) return `${count} workflow${count === 1 ? "" : "s"} listed`;
      return "Workflows listed";
    }
    case "workflow_describe":
      return wfName ? `${wfName} schema loaded` : "Workflow schema loaded";
    case "workflow_run":
    case "workflow_trigger":
      if (wfName && execId) return `Started ${wfName} → ${execId}${status ? ` (${status})` : ""}`;
      if (execId) return `Started workflow → ${execId}${status ? ` (${status})` : ""}`;
      return wfName ? `Started ${wfName}` : "Workflow started";
    case "workflow_status": {
      const label = wfName || execId || "workflow";
      const parts = [label];
      if (status) parts.push(status);
      if (progress) parts.push(progress);
      return parts.join(" · ");
    }
    case "workflow_wait": {
      const label = wfName || execId || "workflow";
      const arts = artifactCount(payload);
      const parts = [label];
      if (status) parts.push(status);
      if (arts > 0) parts.push(`${arts} artifact${arts === 1 ? "" : "s"}`);
      return parts.join(" · ");
    }
    case "workflow_results": {
      const label = wfName || execId || "workflow";
      const arts = artifactCount(payload);
      const parts = [label];
      if (status) parts.push(status);
      if (arts > 0) parts.push(`${arts} artifact${arts === 1 ? "" : "s"}`);
      return parts.join(" · ");
    }
    case "workflow_cancel":
      return execId ? `Cancelled ${execId}${status ? ` (${status})` : ""}` : "Workflow cancelled";
    case "workflow_upload":
      if (wfName) return `Uploaded ${wfName}`;
      if (payload.workflow_name) return `Uploaded ${String(payload.workflow_name)}`;
      return "Workflow uploaded";
    default:
      return wfName || execId || String(message || name);
  }
}

export function formatWorkflowApprovalDescription(toolName: string, rawArgs: unknown): string {
  const args: ToolArgs = typeof rawArgs === "string" ? parseToolArgs(rawArgs) : ((rawArgs as ToolArgs) || {});
  const wfName = workflowNameFromArgs(args);
  const execId = executionIdFromArgs(args);
  const path = String(
    (args as Record<string, unknown>).workflow_path ??
      (args as Record<string, unknown>).path ??
      "",
  );

  switch (toolName) {
    case "workflow_run":
    case "workflow_trigger":
      return wfName ? `Run workflow: ${wfName}` : "Run UMRS workflow";
    case "workflow_cancel":
      return execId ? `Cancel workflow run: ${execId}` : "Cancel UMRS workflow run";
    case "workflow_upload":
      if (wfName) return `Upload workflow: ${wfName}`;
      if (path) return `Upload workflow bundle: ${basename(path)}`;
      return "Upload UMRS workflow bundle";
    default:
      return "";
  }
}
