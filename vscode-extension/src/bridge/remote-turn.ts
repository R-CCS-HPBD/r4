/**
 * A turn on the server (ADR-103 VP-3b part 2; the console's remote-turn
 * watcher in `adapters/companion-chat.ts`, ADR-046 A2 G-RS-22).
 *
 * `send_message` on a server session answers `{remote, queued}`: the sandbox
 * worker pops the queue and runs the turn. This watch polls
 * `session.venue_sync`, folds each answer with the console's pure
 * `evaluateRemoteSync`, and posts the webview's own events: the growing
 * partial as `assistantDelta`, a new round as a closed bubble, the worker's
 * tool activity as one notice, the journal's final answer. Stop ends the
 * watch only; the turn continues on the server.
 */

import {
  evaluateRemoteSync,
  mapVenueRuntime,
  type RemoteSyncAnswer,
  type RemoteSyncStep,
  type VenueRuntime,
} from "../../../packages/r4-tui-v3/src/lib/venue-runtime";
import type { RemoteAsks } from "./remote-asks";

export const REMOTE_POLL_MS = 1_500;

export type RemoteWatchEnd = "finished" | "failed" | "left" | "stopped";

export interface RemoteWatchDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  post(message: Record<string, unknown>): void;
  /** The host's current session (a change ends the watch). */
  sessionId(): string;
  setRuntime(runtime: VenueRuntime): void;
  sleep(ms: number): Promise<void>;
}

type SyncAnswer = RemoteSyncAnswer & { state?: RemoteSyncAnswer["state"] & { session?: { chat_queue?: unknown[] } } };

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** Journal size when the turn was queued (total_messages, else the window length). */
export function sendBaseTotal(result: Record<string, unknown>): number {
  const state = record(result.state);
  if (typeof state.total_messages === "number") return state.total_messages;
  return Array.isArray(state.messages) ? state.messages.length : 0;
}

export function isQueuedRemote(result: Record<string, unknown>): boolean {
  return Boolean(result.remote && result.queued);
}

export class RemoteTurnWatch {
  private shown = "";
  private polls = 0;
  private adopted = false;
  private activity: string[] = [];
  private stopped = false;

  constructor(
    private readonly deps: RemoteWatchDeps,
    private readonly asks: RemoteAsks,
    private readonly sessionId: string,
    private readonly baseTotal: number,
  ) {}

  /** Stop pressed: the watch ends; the turn does not. */
  stop(): void {
    this.stopped = true;
  }

  async run(): Promise<RemoteWatchEnd> {
    for (;;) {
      await this.deps.sleep(REMOTE_POLL_MS);
      const left = this.left();
      if (left) return this.leave(left);
      const sync = await this.sync();
      if (!sync) return "failed";
      const left2 = this.left();
      if (left2) return this.leave(left2);
      const step = this.step(sync);
      if (turnOver(step, sync)) return this.finish(step);
      await this.asks.poll(step.phase, this.polls, String(sync.runtime?.detail ?? ""));
    }
  }

  private left(): "stopped" | "left" | null {
    if (this.stopped) return "stopped";
    return this.deps.sessionId() === this.sessionId ? null : "left";
  }

  private leave(end: "stopped" | "left"): RemoteWatchEnd {
    const why = end === "stopped" ? "stopped watching" : "stopped watching — this view moved to another session;";
    this.post({ type: "notice", text: `${why} the remote turn continues on the server (/venue to check)` });
    return end;
  }

  private async sync(): Promise<SyncAnswer | null> {
    try {
      return (await this.deps.request("session.venue_sync", {}, 30_000)) as SyncAnswer;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.post({ type: "error", message: `remote sync failed: ${detail} — the turn may still finish on the server (/venue)` });
      return null;
    }
  }

  /** One answer: the chip, the partial text (a reset closes the bubble first). */
  private step(sync: SyncAnswer): RemoteSyncStep {
    this.polls += 1;
    this.adopted ||= sync.adopted === true;
    const step = evaluateRemoteSync({ ...sync, adopted: this.adopted }, this.baseTotal, this.shown);
    const runtime = mapVenueRuntime(
      { state: sync.venue_state },
      { runtime: sync.runtime, lease_remaining_sec: typeof sync.lease_remaining_sec === "number" ? sync.lease_remaining_sec : null },
    );
    // An empty activity answer keeps the last-known lines (never fabricated).
    if (runtime.toolActivity.length) this.activity = runtime.toolActivity;
    this.deps.setRuntime(runtime);
    if (step.reset && this.shown) this.post({ type: "assistantComplete", text: this.shown });
    if (step.delta) this.post({ type: "assistantDelta", text: step.delta });
    this.shown = step.shown;
    return step;
  }

  /** The tool summary, then the journal's answer (it replaces the streamed draft). */
  private finish(step: RemoteSyncStep): RemoteWatchEnd {
    if (this.activity.length) {
      const plural = this.activity.length === 1 ? "" : "s";
      const lines = this.activity.slice(0, 12).map((line) => `  ${line}`);
      this.post({ type: "notice", text: [`remote turn — ${this.activity.length} tool action${plural}`, ...lines].join("\n") });
    }
    if (step.phase === "error") {
      this.post({ type: "error", message: `remote turn failed: ${step.errorDetail || "the sandbox reported an error"}` });
      return "failed";
    }
    const text = step.finalText || this.shown;
    if (text) this.post({ type: "assistantComplete", text });
    else this.post({ type: "error", message: "the remote turn ended with an empty response" });
    return text ? "finished" : "failed";
  }

  private post(message: Record<string, unknown>): void {
    this.deps.post({ ...message, sessionId: this.sessionId });
  }
}

/** Done, unless the worker went idle with more queued (it pops the next one). */
function turnOver(step: RemoteSyncStep, sync: SyncAnswer): boolean {
  if (!step.done) return false;
  const queue = sync.state?.session?.chat_queue;
  return !(step.phase === "idle" && Array.isArray(queue) && queue.length > 0);
}
