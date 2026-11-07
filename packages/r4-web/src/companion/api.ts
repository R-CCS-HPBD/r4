/**
 * Typed client for the `/gw/companion/*` surface (UMRS companion worker
 * venue, wave T3b m5). Every route is ownership-pinned server-side: a
 * registry row is reachable only by its owner user id.
 */

import { gatewayFetch } from "./gateway";
import { readEventStream } from "./sse";

const BASE = "/gw/companion";

/* ── wire shapes (mirror umrs.api.companion response models) ────────────── */

export interface WorkerRow {
  registry_key: string;
  owner_user_id: string;
  project_id: string;
  job_id: string;
  state: string;
  attempt_index: number;
  attempts_max: number;
}

export interface TurnAccepted {
  attempt_id: string;
  state: string;
}

export interface PendingApproval {
  approval_id?: string;
  tool_name?: string;
  args_summary?: string;
  [key: string]: unknown;
}

/** Events on the turn SSE stream. `done` is client-side ([DONE] sentinel). */
export type TurnStreamEvent =
  | { type: "in_flight"; attempt_id: string; pending?: boolean }
  | { type: "result"; committed_response_ref: string | null; content?: string | null }
  | { type: "attempt_failed"; attempt_id: string }
  | { type: "attempt_not_found"; attempt_id: string }
  | { type: "done" };

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/* ── helpers ────────────────────────────────────────────────────────────── */

async function parseOrThrow(resp: Response): Promise<unknown> {
  const text = await resp.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body surfaces as raw text */
  }
  if (!resp.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${resp.status}`;
    throw new ApiError(resp.status, detail);
  }
  return body;
}

/* ── workers ────────────────────────────────────────────────────────────── */

/** Fetch the caller's worker row; null when none is provisioned yet. */
export async function getWorker(registryKey: string): Promise<WorkerRow | null> {
  const resp = await gatewayFetch(`${BASE}/workers/${encodeURIComponent(registryKey)}`);
  if (resp.status === 404) return null;
  return (await parseOrThrow(resp)) as WorkerRow;
}

/* ── turns ──────────────────────────────────────────────────────────────── */

export interface BeginTurnInput {
  registryKey: string;
  message: string;
  sessionId?: string;
  /** Client idempotency key; a fresh UUID per user send. */
  requestToken?: string;
  parentAttemptId?: string;
  researchSessionId?: string;
}

export async function beginTurn(input: BeginTurnInput): Promise<TurnAccepted> {
  const resp = await gatewayFetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      registry_key: input.registryKey,
      message: input.message,
      session_id: input.sessionId,
      request_token: input.requestToken,
      parent_attempt_id: input.parentAttemptId,
      research_session_id: input.researchSessionId,
    }),
  });
  return (await parseOrThrow(resp)) as TurnAccepted;
}

/**
 * Stream one turn attempt's events (ADR-028 reattach semantics: the server
 * replays the same attempt buffer, never re-executes). Resolves when the
 * stream closes — the caller decides whether to reattach or stop.
 */
export async function streamTurnEvents(
  attemptId: string,
  onEvent: (ev: TurnStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await gatewayFetch(`${BASE}/turns/${encodeURIComponent(attemptId)}/events`, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  await readEventStream(resp, {
    onEvent: (data) => {
      if (data === "[DONE]") {
        onEvent({ type: "done" });
        return;
      }
      try {
        onEvent(JSON.parse(data) as TurnStreamEvent);
      } catch {
        /* keepalive/comment lines are ignorable */
      }
    },
  });
}

/* ── approvals (m5 scaffold — stable surface, list populates with T3c) ──── */

export async function listPendingApprovals(registryKey: string): Promise<PendingApproval[]> {
  const resp = await gatewayFetch(
    `${BASE}/approvals/pending?registry_key=${encodeURIComponent(registryKey)}`,
  );
  const body = (await parseOrThrow(resp)) as { pending?: PendingApproval[] };
  return body.pending ?? [];
}

export async function recordApprovalDecision(
  approvalId: string,
  decision: "once" | "always" | "no",
): Promise<void> {
  const resp = await gatewayFetch(`${BASE}/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  await parseOrThrow(resp);
}

/** Build the registry key for a (user, project) pair — supervisor convention. */
export function registryKeyFor(userId: string, projectId: string): string {
  return `${userId}@${projectId}`;
}
