/**
 * A server session's approvals and questions (ADR-103 VP-3b part 2; the
 * console's `bridge/venue-bridge.ts`, ADR-046 A2, ADR-099 D-8).
 *
 * The remote worker's tool approvals are durable rows (`session.venue_approvals`,
 * decided with `session.venue_decide`); its ask_user questions too
 * (`session.venue_questions`, answered with `session.venue_answer`). Cards carry
 * `remote:<id>`, so the host routes their answers here. Esc on a question
 * leaves it pending server-side; this window stops reopening it.
 */

import { remoteApprovalOptions, type ApprovalOption } from "../../../packages/r4-tui-v3/src/lib/approval-options";
import { parseVenueError, questionIdFromDetail } from "../../../packages/r4-tui-v3/src/lib/venue-runtime";

export const REMOTE_PREFIX = "remote:";
const APPROVALS_EVERY_N_POLLS = 2;

export interface RemoteAsksDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  post(message: Record<string, unknown>): void;
  sessionId(): string;
}

interface Question {
  questionId: string;
  sessionId: string;
  question: string;
  options: string[];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function pending(rows: unknown, key: string): Record<string, unknown>[] {
  const list = Array.isArray(rows) ? (rows as Record<string, unknown>[]) : [];
  return list.filter((row) => (row.status === "pending" || row.status === undefined) && str(row[key]));
}

export function remoteId(id: string | number): string | null {
  const text = String(id);
  return text.startsWith(REMOTE_PREFIX) ? text.slice(REMOTE_PREFIX.length) : null;
}

export class RemoteAsks {
  private openApproval: string | null = null;
  private openQuestion: string | null = null;
  private readonly dismissed = new Set<string>();
  /** Decided here: never reopened while a poll still lists them pending. */
  private readonly decided = new Set<string>();
  private readonly answered = new Set<string>();
  private readonly questionSessions = new Map<string, string>();

  constructor(private readonly deps: RemoteAsksDeps) {}

  /** One watcher poll: approvals on `awaiting_approval` (and every other poll),
   * the question the worker waits on, and a question card it no longer waits on closes. */
  async poll(phase: string, polls: number, detail: string): Promise<void> {
    if (phase === "awaiting_approval" || polls % APPROVALS_EVERY_N_POLLS === 0) await this.surfaceApprovals().catch(() => undefined);
    await this.followQuestion(phase, detail);
  }

  /** The worker's phase and heartbeat (watcher, status poll, snapshot). */
  async followQuestion(phase: string, detail: string): Promise<void> {
    const named = questionIdFromDetail(detail);
    if (this.openQuestion && (phase !== "awaiting_user" || (named && named !== this.openQuestion))) this.closeQuestion();
    if (phase === "awaiting_user") await this.surfaceQuestion(named).catch(() => undefined);
  }

  /** The first pending approval as a card; "open" while one is already shown. */
  async surfaceApprovals(): Promise<number | "open"> {
    if (this.openApproval) return "open";
    const result = record(await this.deps.request("session.venue_approvals", {}, 15_000));
    const rows = pending(result.approvals, "approval_id").filter((row) => !this.decided.has(str(row.approval_id)));
    const first = rows[0];
    if (!first || this.openApproval) return rows.length;
    const action = record(first.action);
    const approvalId = str(first.approval_id);
    const fenced = action.fenced === true;
    this.openApproval = approvalId;
    this.deps.post({
      type: "approvalRequest",
      sessionId: this.deps.sessionId(),
      id: REMOTE_PREFIX + approvalId,
      tool: str(action.tool) || "tool",
      description: str(action.description) || `remote tool approval: ${str(action.tool) || "tool"}`,
      fenced,
      fenceReason: fenced ? str(action.fence_reason) || "a fence on this call's arguments" : undefined,
      options: remoteApprovalOptions(fenced),
    });
    return rows.length;
  }

  /** The question the worker names, else the newest; set-aside and answered ones are skipped. */
  async surfaceQuestion(named: string | null, sessionId?: string): Promise<number | "open"> {
    if (this.openQuestion || this.openApproval) return "open";
    const result = record(await this.deps.request("session.venue_questions", sessionId ? { session_id: sessionId } : {}, 15_000));
    const open = pending(result.questions, "question_id")
      .map(toQuestion)
      .filter((q) => !this.dismissed.has(q.questionId) && !this.answered.has(q.questionId));
    const shown = open.find((q) => q.questionId === named) ?? open[open.length - 1];
    if (!shown || this.openQuestion) return open.length;
    this.openQuestion = shown.questionId;
    if (shown.sessionId) this.questionSessions.set(shown.questionId, shown.sessionId);
    this.deps.post({ type: "askUserRequest", sessionId: this.deps.sessionId(), id: REMOTE_PREFIX + shown.questionId, question: shown.question, options: shown.options });
    return open.length;
  }

  private closeQuestion(): void {
    const id = this.openQuestion;
    this.openQuestion = null;
    if (!id) return;
    this.deps.post({ type: "askUserResolved", sessionId: this.deps.sessionId(), id: REMOTE_PREFIX + id });
    this.notice("remote question closed (answered elsewhere, expired, or the session stopped)");
  }

  /** A remote card's decision → `session.venue_decide` (once | always | reject). */
  async decide(approvalId: string, decision: string): Promise<void> {
    if (approvalId === this.openApproval) this.openApproval = null;
    this.decided.add(approvalId);
    const answer: ApprovalOption = decision === "once" || decision === "always" ? decision : "reject";
    this.deps.post({ type: "approvalResolved", sessionId: this.deps.sessionId(), id: REMOTE_PREFIX + approvalId, decision: answer });
    try {
      await this.deps.request("session.venue_decide", { approval_id: approvalId, decision: answer }, 15_000);
    } catch (error) {
      this.decided.delete(approvalId);
      this.deps.post({ type: "error", sessionId: this.deps.sessionId(), message: `remote approval failed: ${errText(error)}` });
    }
  }

  /** The owner's answer; null (Esc) sets the question aside, still pending server-side. */
  async answer(questionId: string, answer: string | null): Promise<void> {
    if (questionId === this.openQuestion) this.openQuestion = null;
    this.deps.post({ type: "askUserResolved", sessionId: this.deps.sessionId(), id: REMOTE_PREFIX + questionId });
    if (answer === null) return this.setAside(questionId);
    this.answered.add(questionId);
    const params: Record<string, unknown> = { question_id: questionId, answer };
    const sessionId = this.questionSessions.get(questionId);
    if (sessionId) params.session_id = sessionId;
    try {
      await this.deps.request("session.venue_answer", params, 15_000);
      this.questionSessions.delete(questionId);
    } catch (error) {
      this.answerFailed(questionId, answer, errText(error));
    }
  }

  /** A closed question (answered elsewhere, expired) gives the text back; anything else is an error. */
  private answerFailed(questionId: string, answer: string, detail: string): void {
    if (parseVenueError(detail)?.code === "question_closed") {
      this.notice("remote question already closed (answered elsewhere or expired)");
      this.deps.post({ type: "restoreDraft", sessionId: this.deps.sessionId(), text: answer });
      return;
    }
    this.answered.delete(questionId);
    this.deps.post({ type: "error", sessionId: this.deps.sessionId(), message: `remote answer failed: ${detail}` });
  }

  private setAside(questionId: string): void {
    this.dismissed.add(questionId);
    this.notice(
      `remote question left pending — answer it later with /venue questions or \`r4 session answer <session> ${questionId} <text>\`; ` +
        "the worker stops waiting after its timeout",
    );
  }

  /** `/venue approvals`: the console's three outcomes. */
  async approvalsText(): Promise<{ kind: "info" | "error"; text: string }> {
    try {
      const count = await this.surfaceApprovals();
      if (count === "open") return { kind: "info", text: "an approval card is already open — decide it, then /venue approvals again" };
      return { kind: "info", text: count === 0 ? "no pending remote approvals" : `${count} pending remote approval(s) — first one shown` };
    } catch (error) {
      return { kind: "error", text: `venue approvals failed: ${errText(error)}` };
    }
  }

  /** `/venue questions`: shows the set-aside ones again too. */
  async questionsText(named: string | null): Promise<{ kind: "info" | "error"; text: string }> {
    this.dismissed.clear();
    try {
      const count = await this.surfaceQuestion(named);
      if (count === "open") return { kind: "info", text: "a card is already open — answer it, then /venue questions again" };
      if (count === 0) return { kind: "info", text: "no pending remote questions" };
      return { kind: "info", text: count > 1 ? `${count} pending remote questions — the one the worker waits on is shown` : "1 pending remote question — shown" };
    } catch (error) {
      return { kind: "error", text: `venue questions failed: ${errText(error)}` };
    }
  }

  /** The watch ended: no remote card outlives it (the next poll or /venue reopens). */
  clear(): void {
    if (this.openApproval) {
      this.deps.post({ type: "approvalResolved", sessionId: this.deps.sessionId(), id: REMOTE_PREFIX + this.openApproval, decision: "expired" });
    }
    this.openApproval = null;
    if (this.openQuestion) this.deps.post({ type: "askUserResolved", sessionId: this.deps.sessionId(), id: REMOTE_PREFIX + this.openQuestion });
    this.openQuestion = null;
  }

  private notice(text: string): void {
    this.deps.post({ type: "notice", sessionId: this.deps.sessionId(), text });
  }
}

function toQuestion(row: Record<string, unknown>): Question {
  const options = Array.isArray(row.options) ? row.options.map(str).filter((o) => o.trim()) : [];
  return { questionId: str(row.question_id), sessionId: str(row.session_id), question: str(row.question), options };
}
