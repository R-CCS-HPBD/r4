/**
 * The console's chat verbs the host answers itself (ADR-103 VP-2c):
 * `/rewind` and the message edit (the console's edit-and-resend: the prompt
 * returns to the composer and the next send first cuts the session history
 * at that turn, `session.truncate`), `/btw` (a side question: no tools, not
 * in the history, works mid-turn), `/bg` (background `!` commands),
 * `/explain <error.id>`, `/palette`. No vscode import (hooks; tests).
 */

import { EXPLAIN_USAGE, explainText, type ExplainAnswer } from "../../../packages/r4-tui-v3/src/lib/explain-text";

export type OutputLine = { kind: "info" | "error"; text: string };

/** The console's REWIND_KEEP: a refusal cuts nothing and sends nothing. */
const REWIND_KEEP = "nothing was cut or sent — your text stays in the composer; sending posts it as a new turn";
const BG_KEEP = 20;

export interface PromptRow {
  turnId: string;
  text: string;
}

export interface ChatVerbsDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  post(message: Record<string, unknown>): void;
  sessionId(): string;
  output(command: string, lines: OutputLine[]): void;
  isRemote(): boolean;
  /** `/rewind`: the user's prompts, newest first (null = backed out). */
  pickPrompt?(rows: PromptRow[]): Promise<string | null>;
  openPalette?(): void | Thenable<unknown>;
  now?(): number;
}

interface BgTask {
  id: number;
  label: string;
  status: "running" | "done" | "failed" | "cancelled";
  startedAt: number;
  endedAt?: number;
  detail?: string;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Why the history cannot be cut at this prompt, or null (the console's rewindRefusal). */
export function rewindRefusal(remote: boolean, text: string, turnId: string): string | null {
  if (remote) {
    return `cannot rewind a remote session: its context lives on the server — fork the session to run it locally; ${REWIND_KEEP}`;
  }
  if (text.startsWith("/")) return `cannot rewind to a slash command: it is not a chat turn; ${REWIND_KEEP}`;
  if (!turnId) return `cannot rewind: this turn has no session id — ${REWIND_KEEP}`;
  return null;
}

/** The user's chat prompts in a snapshot, newest first (slash lines are not turns). */
export function promptRows(messages: unknown): PromptRow[] {
  const rows = Array.isArray(messages) ? messages.map(record) : [];
  return rows
    .filter((m) => m.role === "user" && !str(m.content).startsWith("/"))
    .map((m) => ({ turnId: str(record(m.metadata).turn_id), text: str(m.content) }))
    .filter((row) => row.turnId)
    .reverse();
}

export class ChatVerbs {
  private rewind: PromptRow | null = null;
  private readonly tasks: BgTask[] = [];
  private nextTask = 1;

  constructor(private readonly deps: ChatVerbsDeps) {}

  private now(): number {
    return this.deps.now?.() ?? Date.now();
  }

  /** A session change drops a pending rewind (it named the other session's turn). */
  reset(): void {
    this.rewind = null;
  }

  get pendingRewind(): PromptRow | null {
    return this.rewind;
  }

  /** `/rewind [cancel]`: pick an earlier prompt (bare), or keep the history. */
  async rewindCommand(args: string): Promise<void> {
    if (args.trim().toLowerCase() === "cancel") {
      const had = this.rewind !== null;
      this.rewind = null;
      this.deps.post({ type: "rewindPending", sessionId: this.deps.sessionId(), turnId: "" });
      this.deps.output("/rewind cancel", [{ kind: "info", text: had ? "rewind cancelled — the history stays as it is" : "no rewind pending" }]);
      return;
    }
    const rows = await this.prompts();
    if (!rows) return;
    if (!rows.length) return this.deps.output("/rewind", [{ kind: "info", text: "nothing to rewind to: no chat prompt in this session yet" }]);
    const turnId = await this.deps.pickPrompt?.(rows);
    const row = rows.find((r) => r.turnId === turnId);
    if (row) this.begin(row.turnId, row.text);
  }

  private async prompts(): Promise<PromptRow[] | null> {
    try {
      return promptRows(record(await this.deps.request("get_state", {}, 15_000)).messages);
    } catch (error) {
      this.deps.output("/rewind", [{ kind: "error", text: `rewind failed: ${errText(error)}` }]);
      return null;
    }
  }

  /** The prompt returns to the composer; the next send replaces the conversation from it. */
  begin(turnId: string, text: string): void {
    const sessionId = this.deps.sessionId();
    const refusal = rewindRefusal(this.deps.isRemote(), text, turnId);
    this.deps.post({ type: "restoreDraft", sessionId, text, force: true });
    if (refusal) {
      this.rewind = null;
      this.deps.post({ type: "notice", sessionId, text: refusal });
      return;
    }
    this.rewind = { turnId, text };
    this.deps.post({ type: "rewindPending", sessionId, turnId });
    this.deps.post({
      type: "notice",
      sessionId,
      text: "rewound — the prompt is back in the composer; sending replaces the conversation from there (/rewind cancel keeps it)",
    });
  }

  /** Before a send: cut the history at the pending rewind. False = do not send
   * (the cut failed; the text goes back to the composer). */
  async cutBeforeSend(text: string): Promise<boolean> {
    const pending = this.rewind;
    if (!pending) return true;
    this.rewind = null;
    const sessionId = this.deps.sessionId();
    try {
      await this.deps.request("session.truncate", { turn_id: pending.turnId }, 30_000);
      this.deps.post({ type: "truncateFrom", sessionId, turnId: pending.turnId });
      return true;
    } catch (error) {
      this.deps.post({ type: "rewindPending", sessionId, turnId: "" });
      this.deps.post({ type: "error", sessionId, message: `rewind failed (nothing was cut or sent): ${errText(error)}` });
      this.deps.post({ type: "restoreDraft", sessionId, text, force: true });
      return false;
    }
  }

  /** `/btw <question>`: answered from this conversation, no tools, not recorded. */
  async btw(question: string): Promise<void> {
    const q = question.trim();
    const command = `/btw ${q}`.trim();
    if (!q) return this.deps.output(command, [{ kind: "error", text: "usage: /btw <question> — a side question; no tools, not added to the history" }]);
    try {
      const result = record(await this.deps.request("session.side_question", { question: q }, 120_000));
      this.deps.output(command, [{ kind: "info", text: str(result.answer) || "(no answer)" }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `btw failed: ${errText(error)}` }]);
    }
  }

  /** A `!cmd` started (the console's background work list). */
  shellStarted(label: string): number {
    const id = this.nextTask++;
    this.tasks.push({ id, label, status: "running", startedAt: this.now() });
    if (this.tasks.length > BG_KEEP) this.tasks.shift();
    return id;
  }

  shellEnded(id: number, status: "done" | "failed" | "cancelled", detail?: string): void {
    const task = this.tasks.find((t) => t.id === id);
    if (!task) return;
    Object.assign(task, { status, endedAt: this.now(), detail });
  }

  /** `/bg`: the console's lines (newest first). */
  bg(): void {
    if (!this.tasks.length) {
      this.deps.output("/bg", [{ kind: "info", text: "no background work (`!cmd` runs in the background; chat tabs run turns side by side)" }]);
      return;
    }
    const now = this.now();
    const lines = [...this.tasks].reverse().map((t) => {
      const secs = Math.round(((t.endedAt ?? now) - t.startedAt) / 1000);
      return `${t.status.padEnd(9)} shell ${secs}s  ${t.label}${t.detail ? `  — ${t.detail}` : ""}`;
    });
    this.deps.output("/bg", [{ kind: "info", text: `background work (${this.tasks.length}):\n${lines.join("\n")}` }]);
  }

  /** `/explain <error.id>`: the registry's cause and fix. */
  async explain(args: string): Promise<void> {
    const errorId = args.trim();
    const command = `/explain ${errorId}`.trim();
    if (!errorId) return this.deps.output(command, [{ kind: "error", text: EXPLAIN_USAGE }]);
    try {
      const out = record(await this.deps.request("explain", { error_id: errorId }, 15_000)) as ExplainAnswer;
      this.deps.output(command, [{ kind: "info", text: explainText(errorId, out) }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `explain failed: ${errText(error)}` }]);
    }
  }

  /** `/palette`: VS Code's own command palette, on R4's commands. */
  async palette(): Promise<void> {
    if (this.deps.openPalette) await this.deps.openPalette();
    else this.deps.output("/palette", [{ kind: "error", text: "the command palette is not available here" }]);
  }
}
