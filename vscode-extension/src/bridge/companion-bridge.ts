/**
 * The companion JSONL bridge, from the extension host (ADR-103 D-2, VP-1).
 *
 * Spawns the same process the console spawns
 * (`python -m r4.companion.ui.bridge_server --fs-root <workspace>`) and speaks
 * the same wire: the line routing, argv, protocol check, reply errors and
 * out-of-band lines come from the console's own `bridge/wire.ts`, and every
 * string is scrubbed by its `terminalSafeReviver`, so a fix there is a fix
 * here. Node port of the console's `CompanionBridgeClient`
 * (`packages/r4-tui-v3/src/bridge/jsonl-client.ts`): reconnect fails pending
 * requests and never replays them; a process generation guard keeps a stale
 * process's exit from touching its successor.
 *
 * Events: "event", "project_job_event", "team_event", "session_title",
 * "approval", "ask_user", "turn_stopped", "control_event" (bridge lines),
 * "init_state" (the initialize result), "stderr", "protocol_error",
 * "unmatched", "exit".
 */

import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import type { Readable, Writable } from "node:stream";

import { terminalSafeReviver } from "../../../packages/r4-tui-v3/src/lib/terminal-safe";
import {
  CANCEL_TURN_LINE,
  TURN_METHODS,
  approvalResponseLine,
  askUserResponseLine,
  bridgeArgv,
  bridgeReplyError,
  checkBridgeProtocol,
  routeBridgeLine,
  type ApprovalWireDecision,
} from "../../../packages/r4-tui-v3/src/bridge/wire";

export { BridgeRequestError, bridgeErrorHint } from "../../../packages/r4-tui-v3/src/bridge/wire";

/** The part of a child process the bridge uses (a fake in tests). */
export interface BridgeProcess {
  stdin: Writable;
  stdout: Readable;
  stderr: Readable;
  kill(signal?: NodeJS.Signals): boolean;
  once(event: "exit", listener: (code: number | null, signal: NodeJS.Signals | null) => void): unknown;
  once(event: "error", listener: (err: Error) => void): unknown;
}

export type BridgeSpawner = (
  argv: string[],
  options: { cwd: string; env: Record<string, string | undefined> },
) => BridgeProcess;

const nodeSpawner: BridgeSpawner = (argv, options) =>
  spawn(argv[0], argv.slice(1), { cwd: options.cwd, env: options.env, stdio: ["pipe", "pipe", "pipe"] });

export interface CompanionBridgeOptions {
  python: string;
  workspace: string;
  spawner?: BridgeSpawner;
  /** Extra env for the process (per-tab `R4_PROJECT_ID`, as the console does). */
  env?: Record<string, string>;
  webAccess?: string | null;
  sandbox?: "on" | "off" | null;
  defaultTimeoutMs?: number;
  initTimeoutMs?: number;
  /** SIGTERM → SIGKILL grace for a wedged bridge. */
  killGraceMs?: number;
  /** The session a (re)started process initializes into ("" = a new one). */
  initialSessionId?: () => string;
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
  method: string;
}

export class CompanionBridge extends EventEmitter {
  private proc: BridgeProcess | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private buffer = "";
  private stderrTail = "";
  private destroyed = false;
  private starting: Promise<void> | null = null;
  private processGen = 0;
  private readonly spawner: BridgeSpawner;
  readonly defaultTimeoutMs: number;
  /** The last initialize result (the session snapshot). */
  initResult: unknown = null;

  constructor(private readonly options: CompanionBridgeOptions) {
    super();
    this.spawner = options.spawner ?? nodeSpawner;
    this.defaultTimeoutMs = options.defaultTimeoutMs ?? 120_000;
  }

  get alive(): boolean {
    return this.proc !== null && !this.destroyed;
  }

  /** True while an agent turn (TURN_METHODS) is pending on this bridge. */
  get turnInFlight(): boolean {
    for (const pending of this.pending.values()) {
      if (TURN_METHODS.has(pending.method)) return true;
    }
    return false;
  }

  async ensureStarted(): Promise<void> {
    if (this.alive) return;
    if (!this.starting) {
      this.starting = this.start().finally(() => {
        this.starting = null;
      });
    }
    return this.starting;
  }

  private async start(): Promise<void> {
    this.destroyed = false;
    this.buffer = "";
    this.stderrTail = "";
    const gen = ++this.processGen;
    const { python, workspace } = this.options;
    const proc = this.spawner(
      bridgeArgv(python, workspace, this.options.webAccess ?? null, this.options.sandbox ?? null),
      { cwd: workspace, env: { ...process.env, ...this.options.env } },
    );
    this.proc = proc;
    this.wireProcess(proc, gen);
    try {
      const initResult = await this.initializeSession();
      checkBridgeProtocol(
        initResult,
        () => this.emit("stderr", "bridge: legacy server without protocol version — consider upgrading r4\n"),
        "VS Code",
      );
      this.initResult = initResult;
      this.emit("init_state", initResult);
    } catch (err) {
      this.killProcess();
      throw this.startError(err);
    }
  }

  private startError(err: unknown): Error {
    const base = err instanceof Error ? err.message : String(err);
    const hint = this.stderrTail.trim().slice(-400);
    return new Error(
      hint
        ? `Companion bridge failed (${this.options.python}): ${base} — ${hint}`
        : `Companion bridge failed (${this.options.python}): ${base}. Set r4.pythonPath to the interpreter that runs \`r4\`.`,
    );
  }

  private wireProcess(proc: BridgeProcess, gen: number): void {
    proc.stdout.setEncoding("utf8");
    proc.stdout.on("data", (chunk: string) => this.onStdout(chunk));
    proc.stderr.setEncoding("utf8");
    proc.stderr.on("data", (chunk: string) => {
      this.stderrTail = (this.stderrTail + chunk).slice(-2_000);
      this.emit("stderr", chunk);
    });
    proc.stdin.on("error", () => {
      /* EPIPE after exit: the exit handler reports it */
    });
    proc.once("error", (err) => this.reportProcessExit(gen, err));
    proc.once("exit", () => {
      const hint = this.stderrTail.trim().slice(-400);
      this.reportProcessExit(gen, new Error(hint ? `bridge exited: ${hint}` : "bridge exited"));
    });
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    let nl: number;
    while ((nl = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, nl).trim();
      this.buffer = this.buffer.slice(nl + 1);
      if (line) this.handleLine(line);
    }
  }

  /** initialize into the known session; an id the bridge does not know
   * falls back to a fresh session (the console's w3-tui rule). */
  private async initializeSession(): Promise<unknown> {
    const initTimeout = this.options.initTimeoutMs ?? 30_000;
    const sessionId = this.options.initialSessionId?.() ?? "";
    if (!sessionId) return this.request("initialize", {}, initTimeout);
    try {
      return await this.request("initialize", { session_id: sessionId }, initTimeout);
    } catch (err) {
      if (!/session not found/i.test(err instanceof Error ? err.message : String(err))) throw err;
      this.emit("stderr", `bridge: session ${sessionId.slice(0, 8)} unknown to this bridge — initialized a new one\n`);
      return this.request("initialize", {}, initTimeout);
    }
  }

  async request(method: string, params: Record<string, unknown> = {}, timeoutMs = this.defaultTimeoutMs): Promise<unknown> {
    if (method !== "initialize") await this.ensureStarted();
    const stdin = this.proc?.stdin;
    if (!stdin || this.destroyed) throw new Error("bridge unavailable");
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer =
        Number.isFinite(timeoutMs) && timeoutMs > 0
          ? setTimeout(() => {
              this.pending.delete(id);
              reject(new Error(`bridge request timed out: ${method}`));
            }, timeoutMs)
          : null;
      this.pending.set(id, { resolve, reject, timer, method });
      this.writeLine(JSON.stringify({ id, method, params }), (err) => {
        if (timer) clearTimeout(timer);
        this.pending.delete(id);
        reject(err);
      });
    });
  }

  private writeLine(line: string, onError?: (err: Error) => void): void {
    const stdin = this.proc?.stdin;
    if (!stdin || this.destroyed) return;
    try {
      stdin.write(`${line}\n`);
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  }

  /** Reply to a server-initiated tool approval request. */
  respondApproval(id: string | number, decision: ApprovalWireDecision): void {
    this.writeLine(approvalResponseLine(id, decision));
  }

  /** Reply to a server-initiated ask_user request. */
  respondAskUser(id: string | number, answer: string | null): void {
    this.writeLine(askUserResponseLine(id, answer));
  }

  /** Stop the in-flight turn (out-of-band; no reply expected). */
  cancelTurn(): void {
    this.writeLine(CANCEL_TURN_LINE);
  }

  /** Kill the process, fail pending (no replay), start fresh. */
  async reconnect(): Promise<void> {
    this.failPending(new Error("bridge reconnect — pending request cancelled (no auto-replay)"));
    await this.shutdown();
    await this.ensureStarted();
  }

  async shutdown(): Promise<void> {
    if (this.alive) {
      try {
        await this.request("shutdown", {}, 5_000);
      } catch {
        /* the kill below ends it either way */
      }
    }
    this.killProcess();
  }

  private killProcess(): void {
    this.destroyed = true;
    const proc = this.proc;
    this.proc = null;
    if (!proc) return;
    try {
      proc.kill("SIGTERM");
    } catch {
      /* already gone */
    }
    // A bridge wedged in a native wait ignores SIGTERM (console report 2026-10-05).
    const killer = setTimeout(() => {
      try {
        proc.kill("SIGKILL");
      } catch {
        /* already gone */
      }
    }, this.options.killGraceMs ?? 2_000);
    killer.unref?.();
  }

  private reportProcessExit(gen: number, error: Error): void {
    if (gen !== this.processGen) return;
    if (this.destroyed && this.pending.size === 0) return;
    this.destroyed = true;
    this.proc = null;
    this.failPending(error);
    this.emit("exit", error);
  }

  failPending(error: Error): void {
    for (const pending of this.pending.values()) {
      if (pending.timer) clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private handleLine(line: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(line, terminalSafeReviver) as Record<string, unknown>;
    } catch (err) {
      this.emit("protocol_error", new Error(`invalid bridge JSON: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    const routed = routeBridgeLine(message);
    if (routed.kind === "notification") {
      this.emit(routed.channel, routed.payload);
      return;
    }
    this.dispatchResponse(message);
  }

  private dispatchResponse(message: Record<string, unknown>): void {
    const id = message.id as number | undefined;
    const pending = id === undefined ? undefined : this.pending.get(id);
    if (!pending) {
      this.emit("unmatched", message);
      return;
    }
    if (pending.timer) clearTimeout(pending.timer);
    this.pending.delete(id as number);
    if (message.ok) pending.resolve(message.result);
    else pending.reject(bridgeReplyError(message));
  }
}
