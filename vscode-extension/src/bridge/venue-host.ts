/**
 * A session on the server (ADR-103 VP-3b part 2; the console's
 * `bridge/venue-bridge.ts` and remote source picker, ADR-046 A2).
 *
 * `/venue [status|start|stop [<id>]|sources]` over `session.venue_*` and
 * `source.*`; the `remote:` chip with its lease, polled while the runtime is
 * live; the start wait while the server provisions. All decisions and texts
 * come from the console's `lib/venue-runtime.ts`. No vscode import: the
 * picker and the confirm are hooks, the clock is injectable (tests).
 */

import {
  eligibleRemoteSources,
  formatVenueChip,
  formatVenueStatusLine,
  mapRemoteSourceRow,
  mapSnapshotVenueRuntime,
  mapVenueRuntime,
  parseVenueError,
  questionIdFromDetail,
  remoteBadge,
  START_POLL_MAX_MS,
  START_POLL_MS,
  startTransition,
  type RemoteSourceRow,
  type StartTransition,
  type VenueRuntime,
} from "../../../packages/r4-tui-v3/src/lib/venue-runtime";
import { webAccessLabel, type WebChoice } from "../../../packages/r4-tui-v3/src/lib/web-access";

export const VENUE_USAGE = "usage: /venue [status|start|stop [<id>]|sources|approvals|questions]";
export const STATUS_POLL_MS = 30_000;
const LIVE_STATES = new Set(["provisioning", "running"]);

export type OutputLine = { kind: "info" | "error"; text: string };

/** One row of the source picker (the console's row: name · provider · badge). */
export interface SourcePick {
  name: string;
  detail: string;
  badge: string;
  needsPush: boolean;
}

export interface VenueClock {
  every(ms: number, fn: () => void): () => void;
  sleep(ms: number): Promise<void>;
  now(): number;
}

export const REAL_CLOCK: VenueClock = {
  every: (ms, fn) => {
    const timer = setInterval(fn, ms);
    timer.unref?.();
    return () => clearInterval(timer);
  },
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now: () => Date.now(),
};

/** What the venue needs from its host. */
export interface VenueDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  post(message: Record<string, unknown>): void;
  sessionId(): string;
  output(command: string, lines: OutputLine[]): void;
  /** The session's own web choice (null = not chosen: ask first). */
  webChoice(): WebChoice | null;
  /** Ask the web-access question with the server's caps; false = backed out. */
  askRemoteWeb(): Promise<boolean>;
  /** A start/stop answer's state (venue, policy chips) and an adopted copy. */
  adoptState(state: unknown, adopted: boolean): Promise<void>;
  pickSource?(ask: { title: string; rows: SourcePick[] }): Promise<string | null>;
  confirm?(message: string, action: string): Promise<boolean>;
  /** `/venue approvals` and `/venue questions` (the remote asks answer). */
  approvals?(): Promise<OutputLine>;
  questions?(named: string | null): Promise<OutputLine>;
  /** Every runtime this venue adopts (snapshot, poll, watch). */
  onRuntime?(runtime: VenueRuntime): void;
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

function toPick(row: RemoteSourceRow): SourcePick {
  const badge = remoteBadge(row);
  return { name: row.name, detail: `${row.provider ?? "openai-compat"} · ${row.url}`, badge: badge.glyph, needsPush: badge.needsPush };
}

export class VenueHost {
  private runtime: VenueRuntime | null = null;
  private venue = "";
  private stopPolling: (() => void) | null = null;

  constructor(
    private readonly deps: VenueDeps,
    private readonly clock: VenueClock = REAL_CLOCK,
  ) {}

  get current(): VenueRuntime | null {
    return this.runtime;
  }

  get isRemote(): boolean {
    return this.venue === "frontend";
  }

  /** A snapshot's `venue` and `venue_runtime` → the chip (and the poller). */
  adoptSnapshot(session: Record<string, unknown>): void {
    this.venue = str(session.venue);
    this.setRuntime(mapSnapshotVenueRuntime(session.venue_runtime));
  }

  setRuntime(runtime: VenueRuntime | null): void {
    this.runtime = runtime;
    const chip = formatVenueChip(runtime);
    this.deps.post({ type: "venueState", sessionId: this.deps.sessionId(), chip: chip?.label ?? "", chipState: chip?.state ?? "" });
    if (runtime && LIVE_STATES.has(runtime.state)) this.armPolling();
    else this.disarmPolling();
    if (runtime) this.deps.onRuntime?.(runtime);
  }

  private armPolling(): void {
    this.stopPolling ??= this.clock.every(STATUS_POLL_MS, () => void this.fetchStatus().catch(() => undefined));
  }

  private disarmPolling(): void {
    this.stopPolling?.();
    this.stopPolling = null;
  }

  dispose(): void {
    this.disarmPolling();
  }

  /** `/venue …`: the console's verbs (approvals/questions ride the remote turn). */
  async command(args: string): Promise<void> {
    const [verb = "", arg = ""] = args.trim().split(/\s+/);
    const run = VENUE_VERBS[verb.toLowerCase()];
    if (run) {
      await run(this, arg);
      return;
    }
    this.deps.output(`/venue ${args}`.trim(), [{ kind: "error", text: VENUE_USAGE }]);
  }

  /** session.venue_status; null with `error` when the read failed (not not_found). */
  async fetchStatus(sessionId?: string): Promise<{ runtime: VenueRuntime | null; error?: string }> {
    const own = !sessionId || sessionId === this.deps.sessionId();
    try {
      const result = record(await this.deps.request("session.venue_status", sessionId ? { session_id: sessionId } : {}, 20_000));
      const runtime = mapVenueRuntime(result.venue, { lease_remaining_sec: result.lease_remaining_sec });
      if (own) this.setRuntime(runtime);
      return { runtime };
    } catch (error) {
      if (parseVenueError(errText(error))?.code !== "not_found") return { runtime: null, error: errText(error) };
      if (own) this.setRuntime(null);
      return { runtime: null };
    }
  }

  async status(): Promise<void> {
    const { runtime, error } = await this.fetchStatus();
    if (error && this.isRemote) return this.deps.output("/venue", [{ kind: "error", text: `venue status failed: ${error}` }]);
    const text = runtime ? formatVenueStatusLine(runtime) : this.noRuntimeText();
    this.deps.output("/venue", [{ kind: "info", text }]);
  }

  private noRuntimeText(): string {
    return this.isRemote
      ? "remote runtime: none recorded for this session (start one with /venue start)"
      : "this session runs locally — /venue start runs it on the server instead";
  }

  /** `/venue start`: this session on the server — web access first, then a
   * pushed source (a row not pushed yet asks once and pushes), then start. */
  async start(): Promise<void> {
    if (!this.deps.webChoice() && !(await this.deps.askRemoteWeb())) return;
    const web = this.deps.webChoice();
    if (!web) return;
    const name = await this.pickSource("remote session — which pushed source should the sandbox use?", true);
    if (name) await this.startWith(name, web, true);
  }

  /** A new session on the server (the venue picker's Server row): the web
   * answer, a source, then the session is created with that answer and
   * started; a push-then-retry reuses the session it created. */
  async startNew(askWeb: () => Promise<WebChoice | null>, create: (web: WebChoice) => Promise<boolean>): Promise<void> {
    const web = await askWeb();
    if (!web) return;
    const name = await this.pickSource("new remote session — which pushed source should the sandbox use?", true);
    if (!name || !(await create(web))) return;
    await this.startWith(name, web, true);
  }

  /** The picker over eligible rows; null when nothing was chosen. */
  private async pickSource(title: string, starting: boolean): Promise<string | null> {
    const listed = await this.listSources();
    if (!listed) return null;
    if (starting && !listed.remoteAvailable) {
      this.deps.output("/venue start", [{ kind: "error", text: "server unreachable — the sealed-source list did not answer; try again later" }]);
      return null;
    }
    if (!listed.rows.length) {
      this.deps.output("/venue", [{ kind: "error", text: "no eligible source: a remote session needs a public source with a key (r4 source add)" }]);
      return null;
    }
    return (await this.deps.pickSource?.({ title, rows: listed.rows.map(toPick) })) ?? null;
  }

  private async listSources(): Promise<{ rows: RemoteSourceRow[]; remoteAvailable: boolean } | null> {
    try {
      const result = record(await this.deps.request("source.list_remote", {}, 20_000));
      const rows = Array.isArray(result.rows) ? (result.rows as Record<string, unknown>[]).map(mapRemoteSourceRow) : [];
      return { rows: eligibleRemoteSources(rows), remoteAvailable: result.remote_available === true };
    } catch (error) {
      this.deps.output("/venue", [{ kind: "error", text: `source list failed: ${errText(error)}` }]);
      return null;
    }
  }

  private async startWith(source: string, web: WebChoice, mayPush: boolean): Promise<void> {
    try {
      const result = record(
        await this.deps.request(
          "session.venue_start",
          { source_refs: [source], default_source: source, web_policy: web.mode, web_allow_domains: web.domains },
          300_000,
        ),
      );
      await this.deps.adoptState(result.state, result.adopted === true);
      const runtime = mapVenueRuntime(result.venue);
      this.setRuntime(runtime);
      const first = startTransition(runtime);
      this.notice(`${first.text} · source ${source} · web ${webAccessLabel(web)}`);
      if (first.kind === "pending") void this.awaitRunning(this.deps.sessionId());
    } catch (error) {
      await this.startRefused(source, web, errText(error), mayPush);
    }
  }

  /** A refusal in the server's own words; `source_not_pushed` offers the push and retries once. */
  private async startRefused(source: string, web: WebChoice, detail: string, mayPush: boolean): Promise<void> {
    const parsed = parseVenueError(detail);
    if (parsed?.code === "source_not_pushed" && mayPush && (await this.push(source, true))) {
      await this.startWith(source, web, false);
      return;
    }
    const text = parsed ? `server refused (${parsed.code}): ${parsed.message}` : detail;
    this.deps.output("/venue start", [{ kind: "error", text: `remote session start failed: ${text}` }]);
  }

  /** After `provisioning`: poll fast until the runtime leaves it (bounded);
   * abandoned silently when the session changes. */
  async awaitRunning(sessionId: string): Promise<StartTransition> {
    const deadline = this.clock.now() + START_POLL_MAX_MS;
    let last: StartTransition = { kind: "pending", text: "" };
    while (this.clock.now() < deadline) {
      await this.clock.sleep(START_POLL_MS);
      if (this.deps.sessionId() !== sessionId) return last;
      last = startTransition((await this.fetchStatus()).runtime);
      if (last.kind !== "pending") break;
    }
    return this.reportTransition(last);
  }

  private reportTransition(last: StartTransition): StartTransition {
    if (last.kind === "running") this.notice(last.text);
    else if (last.kind === "pending") this.notice("remote session still provisioning — check /venue");
    else this.deps.post({ type: "error", sessionId: this.deps.sessionId(), message: `remote session start: ${last.text}` });
    return last;
  }

  async approvals(): Promise<void> {
    if (this.deps.approvals) this.deps.output("/venue approvals", [await this.deps.approvals()]);
  }

  async questions(): Promise<void> {
    if (this.deps.questions) this.deps.output("/venue questions", [await this.deps.questions(questionIdFromDetail(this.runtime?.detail))]);
  }

  /** `/venue stop [<id>]` (a Sessions row passes its id). */
  async stop(sessionId?: string): Promise<boolean> {
    const own = !sessionId || sessionId === this.deps.sessionId();
    try {
      const result = record(await this.deps.request("session.venue_stop", sessionId ? { session_id: sessionId } : {}, 150_000));
      if (own) {
        await this.deps.adoptState(result.state, result.adopted === true);
        this.setRuntime(mapVenueRuntime(result.venue));
      }
      this.notice(`remote session stopped${sessionId ? ` (${sessionId.slice(0, 8)})` : ""}`);
      return true;
    } catch (error) {
      this.deps.post({ type: "error", sessionId: this.deps.sessionId(), message: `remote session stop failed: ${errText(error)}` });
      return false;
    }
  }

  /** `/venue sources`: push a row not pushed yet, unpush a pushed one (asks). */
  async sources(): Promise<void> {
    const listed = await this.listSources();
    if (!listed) return;
    const rows = listed.rows.map(toPick);
    const name = await this.deps.pickSource?.({ title: "pushed sources — pick one to push or unpush", rows });
    const row = rows.find((r) => r.name === name);
    if (!row) return;
    if (row.needsPush) await this.push(row.name, true);
    else await this.unpush(row);
  }

  /** Push a source's key to the platform (sealed; the sandbox never sees it). */
  private async push(name: string, ask: boolean): Promise<boolean> {
    const message = `Push the key of '${name}' to the platform? It is sealed: the sandbox never sees it.`;
    if (ask && this.deps.confirm && !(await this.deps.confirm(message, "Push"))) return false;
    try {
      const result = record(await this.deps.request("source.push", { name }, 60_000));
      const replaced = result.replaced ? " · replaced" : "";
      this.notice(`pushed '${name}' → ${str(result.allow_host)} (${str(result.key_fingerprint)})${replaced}`);
      return true;
    } catch (error) {
      this.deps.post({ type: "error", sessionId: this.deps.sessionId(), message: `push '${name}' failed: ${errText(error)}` });
      return false;
    }
  }

  private async unpush(row: SourcePick): Promise<void> {
    const message = `Unpush '${row.name}' (${row.badge})? Remote sessions using it lose their lease.`;
    if (this.deps.confirm && !(await this.deps.confirm(message, "Unpush"))) return;
    try {
      const result = record(await this.deps.request("source.unpush", { name: row.name }, 60_000));
      this.notice(`unpushed '${row.name}' (leases revoked: ${Number(result.leases_revoked ?? 0)})`);
    } catch (error) {
      this.deps.post({ type: "error", sessionId: this.deps.sessionId(), message: `unpush '${row.name}' failed: ${errText(error)}` });
    }
  }

  private notice(text: string): void {
    this.deps.post({ type: "notice", sessionId: this.deps.sessionId(), text });
  }
}

const VENUE_VERBS: Record<string, (venue: VenueHost, arg: string) => Promise<unknown>> = {
  "": (venue) => venue.status(),
  status: (venue) => venue.status(),
  start: (venue) => venue.start(),
  stop: (venue, arg) => venue.stop(arg || undefined),
  sources: (venue) => venue.sources(),
  approvals: (venue) => venue.approvals(),
  questions: (venue) => venue.questions(),
};
