/**
 * Persistent JSONL companion bridge client for Bun TUI.
 *
 * Reconnect policy: fail all pending requests; never auto-replay mutating commands.
 * Read-only catalog refresh on reconnect is OK (caller decides).
 */

import { EventEmitter } from "node:events";
import { launchWorkspace, resolveBridgePython } from "../lib/resolve-python";
import { terminalSafeReviver } from "../lib/terminal-safe";
import {
  CANCEL_TURN_LINE,
  TURN_METHODS,
  approvalResponseLine,
  askUserResponseLine,
  bridgeArgv,
  bridgeReplyError,
  checkBridgeProtocol,
  routeBridgeLine,
} from "./wire";

// The wire moved to ./wire (ADR-103 D-3, 2026-10-31); re-exported so every
// existing import keeps working.
export {
  BRIDGE_PROTOCOL_VERSION,
  BridgeRequestError,
  TURN_METHODS,
  bridgeArgv,
  bridgeErrorHint,
  checkBridgeProtocol,
} from "./wire";

export type { ApprovalWireDecision } from "./wire";
import type { ApprovalWireDecision } from "./wire";

export type BridgeEventHandler = (event: Record<string, unknown>) => void;

/** `r4 --web` hands its flag to the console in this launch-only variable
 * (ADR-099 D-2, RS-1 fix W5). */
export const LAUNCH_WEB_ACCESS_ENV = "R4_LAUNCH_WEB_ACCESS";

let launchWebAccess: string | null | undefined;

/** The `r4 --web` flag (`MODE[:d1,d2]`), taken out of `env` the first time
 * and kept for every bridge (re)start: the shells the console and its
 * bridge start never inherit it. null when `r4 --web` was not given. */
export function takeLaunchWebAccess(env: Record<string, string | undefined> = process.env): string | null {
  if (launchWebAccess === undefined) {
    const spec = (env[LAUNCH_WEB_ACCESS_ENV] ?? "").trim();
    delete env[LAUNCH_WEB_ACCESS_ENV];
    launchWebAccess = spec || null;
  }
  return launchWebAccess;
}

/** Test hook: forget the taken flag. */
export function resetLaunchWebAccessForTest(): void {
  launchWebAccess = undefined;
}

/** `r4 --sandbox on|off` hands its flag to the console in this launch-only
 * variable (ADR-101 D-2; `R4_LAUNCH_SANDBOX`, popped like the web flag). */
export const LAUNCH_SANDBOX_ENV = "R4_LAUNCH_SANDBOX";

let launchSandbox: "on" | "off" | null | undefined;

/** `r4 --sandbox` takes exactly these (argparse choices), the only writer of
 * the variable; anything else is no preset (review Q-5, 2026-10-30). */
const SANDBOX_FLAG_WORDS = new Map<string, "on" | "off">([
  ["on", "on"],
  ["off", "off"],
]);

/** The `r4 --sandbox` flag, taken out of `env` the first time and kept for
 * every bridge (re)start; null when not given or not a flag word (the
 * bridge's own order then applies). */
export function takeLaunchSandbox(env: Record<string, string | undefined> = process.env): "on" | "off" | null {
  if (launchSandbox === undefined) {
    const spec = (env[LAUNCH_SANDBOX_ENV] ?? "").trim().toLowerCase();
    delete env[LAUNCH_SANDBOX_ENV];
    launchSandbox = SANDBOX_FLAG_WORDS.get(spec) ?? null;
  }
  return launchSandbox;
}

/** Test hook: forget the taken flag. */
export function resetLaunchSandboxForTest(): void {
  launchSandbox = undefined;
}


// Taken at load, before the console starts anything that could inherit them.
takeLaunchWebAccess();
takeLaunchSandbox();

interface ImportProbe {
  ok: boolean;
  error?: string;
  firstMissing?: string;
}

/** Boot modules the bridge server imports (top of the chain). The probe
 * reports the FIRST import that fails, so the UI can name the exact
 * missing module instead of a bare "no module named 'r4.companion.<x>'"
 * deep inside a stack frame. Cost: one fast python -c per cold start. */
const BRIDGE_BOOT_MODULES = [
  "r4.companion.ui.bridge_server",
  "r4.companion.control_schema",
  "r4.runtime",
  "r4.runtime.session_view",
  "r4.companion.session_runtime",
] as const;

export async function probeBridgeImports(python: string): Promise<ImportProbe> {
  const script =
    "import importlib,sys\n" +
    "mods=%s\n" +
    "first=None\n" +
    "try:\n" +
    "    for m in mods:\n" +
    "        importlib.import_module(m)\n" +
    "except ModuleNotFoundError as e:\n" +
    "    print(f'E:{e.name}')\n" +
    "    sys.exit(3)\n" +
    "except Exception as e:\n" +
    "    print(f'E:{type(e).__name__}:{e}')\n" +
    "    sys.exit(4)";
  const files = JSON.stringify([...BRIDGE_BOOT_MODULES]);
  const proc = Bun.spawnSync([python, "-c", script.replace("%s", files)], {
    stdout: "pipe",
    stderr: "pipe",
    env: { ...process.env },
  });
  if (proc.exitCode === 0) return { ok: true };
  const out = new TextDecoder().decode(proc.stdout).trim();
  const errText = new TextDecoder().decode(proc.stderr).trim().slice(-200);
  const missing = out.startsWith("E:") ? out.slice(2) : errText || out || "import failed";
  return {
    ok: false,
    error: missing,
    firstMissing: missing.split(":")[0] || missing,
  };
}

/** DEBUG spawn trace (R4_TUI_DEBUG=bridge). Keeps noise opt-in. */
export function logBridgeSpawn(python: string): void {
  if ((process.env.R4_TUI_DEBUG ?? "").split(",").includes("bridge")) {
    process.stderr.write(
      `[bridge] spawn ${python} -m r4.companion.ui.bridge_server (cwd ${launchWorkspace()})\n`,
    );
  }
}





interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
  method: string;
}

export { resolveBridgePython } from "../lib/resolve-python";

export class CompanionBridgeClient extends EventEmitter {
  private proc: ReturnType<typeof Bun.spawn> | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private buffer = "";
  private destroyed = false;
  private starting: Promise<void> | null = null;
  /**
   * Extra env for the bridge process (chat tabs, 2026-10-01): per-tab
   * project selection rides R4_PROJECT_ID so a /project switch in tab A
   * can never leak into tab B's bridge (selection is process-local now).
   */
  private readonly envExtra?: Record<string, string>;
  /**
   * Process generation counter. Each start() bumps it; reader loops capture
   * their generation and only the CURRENT one may report process exit.
   * Without this a reconnect raced: the OLD bridge's stdout reader could
   * finish (and call onProcessExit) AFTER the new process started — wiping
   * the new process reference and failing its initialize with a bogus
   * "bridge exited" (login → reconnect fallout, user report 2026-09-11).
   */
  private processGen = 0;
  readonly defaultTimeoutMs: number;

  /** The session a (re)started process initializes into ("" = a new one). */
  private readonly initialSessionId?: () => string;

  constructor(opts?: {
    defaultTimeoutMs?: number;
    envExtra?: Record<string, string>;
    initialSessionId?: () => string;
  }) {
    super();
    this.defaultTimeoutMs = opts?.defaultTimeoutMs ?? 120_000;
    this.envExtra = opts?.envExtra;
    this.initialSessionId = opts?.initialSessionId;
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
    if (this.starting) return this.starting;
    this.starting = this.start();
    try {
      await this.starting;
    } finally {
      this.starting = null;
    }
  }

  private async start(): Promise<void> {
    this.destroyed = false;
    this.buffer = "";
    const gen = ++this.processGen;
    let stderrTail = "";
    const python = resolveBridgePython();
    // DEBUG preflight: import the boot modules like the server will. The
    // historical failure mode lands here as a bare "no module named
    // 'r4.companion.<x>'" deep inside the UI — surface the EXACT first
    // failing module on one line so reports root-cause immediately.
    const preflight = await probeBridgeImports(python);
    if (!preflight.ok) {
      throw new Error(
        `Companion bridge preflight failed (${python}): ${preflight.error}\n` +
          `First missing import: ${preflight.firstMissing ?? "unknown"}. ` +
          `Check R4_TUI_PYTHON / which python runs \`r4\` (unbuilt binaries ` +
          `resolve differently than the repo venv).`,
      );
    }
    logBridgeSpawn(python);
    const workspace = launchWorkspace(); // a new bridge starts where the console was launched
    const proc = Bun.spawn(
      bridgeArgv(python, workspace, takeLaunchWebAccess(), takeLaunchSandbox()),
      {
        cwd: workspace,
        stdin: "pipe",
        stdout: "pipe",
        stderr: "pipe",
        env: { ...process.env, ...this.envExtra },
      },
    );
    this.proc = proc;

    void (async () => {
      const reader = proc.stdout.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          this.buffer += decoder.decode(value, { stream: true });
          let nl: number;
          while ((nl = this.buffer.indexOf("\n")) !== -1) {
            const line = this.buffer.slice(0, nl).trim();
            this.buffer = this.buffer.slice(nl + 1);
            if (line) this.handleLine(line);
          }
        }
      } catch {
        // process ended
      } finally {
        const hint = stderrTail.trim().slice(-400);
        this.reportProcessExit(
          gen,
          new Error(hint ? `bridge exited: ${hint}` : "bridge exited"),
        );
      }
    })();

    void (async () => {
      const reader = proc.stderr.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value);
          stderrTail = (stderrTail + chunk).slice(-2_000);
          this.emit("stderr", chunk);
        }
      } catch {
        /* ignore */
      }
    })();

    // Initialize session so slash/project RPCs have a session.
    try {
      const initResult = await this.initializeSession();
      checkBridgeProtocol(initResult, () =>
        this.emit(
          "stderr",
          "bridge: legacy server without protocol version — consider upgrading r4\n",
        ),
      );
      // Share the full snapshot (skills/endpoints/config) — the first chat turn
      // was the only place this ever landed, leaving inventory empty at launch.
      this.emit("init_state", initResult);
      // Round-3 task 7: if the bridge process's r4 source tree changed after
      // the process started (editable install: working tree import paths),
      // warn once — stale code is invisible without this.
      try {
        const source = (initResult as Record<string, unknown> | null)?.source as
          | { digest: string; source_dir: string }
          | null
          | undefined;
        if (source?.digest && source.source_dir) {
          // Lazy require to keep the hot path zero-cost when absent.
          const { sourceTreeChanged } = require("../lib/source-identity") as typeof import("../lib/source-identity");
          if (sourceTreeChanged(source)) {
            this.emit(
              "source_stale",
              "r4 source changed since this bridge started — restart `r4 console` for fresh code",
            );
          }
        }
      } catch {
        // source check must never block startup
      }
    } catch (err) {
      this.killProcess();
      const hint = stderrTail.trim().slice(-400);
      const base = err instanceof Error ? err.message : String(err);
      throw new Error(
        hint
          ? `Companion bridge failed (${python}): ${base} — ${hint}`
          : `Companion bridge failed (${python}): ${base}. Set R4_TUI_PYTHON to the interpreter that runs \`r4\` (r4 console does this automatically).`,
      );
    }
  }

  /** initialize into the tab's known session (w3-tui, 2026-10-21): a bare
   * `initialize {}` created an empty server session record on every tab
   * bridge start — even right before that tab resumed a session. An id the
   * bridge does not know (deleted, or a journal-only row load_session will
   * pull) falls back to a fresh session; the resume that follows reports
   * its own outcome, so nothing is claimed here. */
  private async initializeSession(): Promise<unknown> {
    const sessionId = this.initialSessionId?.() ?? "";
    if (!sessionId) return this.request("initialize", {}, 30_000);
    try {
      return await this.request("initialize", { session_id: sessionId }, 30_000);
    } catch (err) {
      if (!/session not found/i.test(err instanceof Error ? err.message : String(err))) throw err;
      this.emit("stderr", `bridge: session ${sessionId.slice(0, 8)} unknown to this bridge — initialized a new one\n`);
      return this.request("initialize", {}, 30_000);
    }
  }

  async request(method: string, params: Record<string, unknown> = {}, timeoutMs = this.defaultTimeoutMs): Promise<unknown> {
    await this.ensureStarted();
    const stdin = this.proc?.stdin;
    if (!stdin || typeof stdin === "number" || this.destroyed) {
      throw new Error("bridge unavailable");
    }
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolve, reject) => {
      const useTimeout = Number.isFinite(timeoutMs) && timeoutMs > 0;
      const timer = useTimeout
        ? setTimeout(() => {
            this.pending.delete(id);
            reject(new Error(`bridge request timed out: ${method}`));
          }, timeoutMs)
        : null;
      this.pending.set(id, { resolve, reject, timer, method });
      try {
        stdin.write(`${payload}\n`);
      } catch (err) {
        if (timer) clearTimeout(timer);
        this.pending.delete(id);
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  /** Reply to a server-initiated tool approval request. */
  respondApproval(id: string | number, decision: ApprovalWireDecision): void {
    const stdin = this.proc?.stdin;
    if (!stdin || typeof stdin === "number" || this.destroyed) return;
    const payload = approvalResponseLine(id, decision);
    try {
      stdin.write(`${payload}\n`);
    } catch {
      /* ignore */
    }
  }

  /** Reply to a server-initiated ask_user request (Companion clarifying question). */
  respondAskUser(id: string | number, answer: string | null): void {
    const stdin = this.proc?.stdin;
    if (!stdin || typeof stdin === "number" || this.destroyed) return;
    const payload = askUserResponseLine(id, answer);
    try {
      stdin.write(`${payload}\n`);
    } catch {
      /* ignore */
    }
  }

  /** Stop the in-flight companion turn (out-of-band; no response expected). */
  cancelTurn(): void {
    const stdin = this.proc?.stdin;
    if (!stdin || typeof stdin === "number" || this.destroyed) return;
    try {
      stdin.write(`${CANCEL_TURN_LINE}\n`);
    } catch {
      /* ignore */
    }
  }

  /**
   * Resolve in-flight send_message/confirm_plan immediately so the UI can stop waiting.
   * The bridge still winds down the turn in the background.
   */
  abortInFlightSend(): void {
    for (const [id, pending] of this.pending) {
      if (pending.method !== "send_message" && pending.method !== "confirm_plan") continue;
      if (pending.timer) clearTimeout(pending.timer);
      this.pending.delete(id);
      pending.resolve({ aborted: true, cancelled: true });
    }
  }

  /**
   * Soft reconnect: kill process, fail pending (no mutating replay), start fresh.
   * Caller may refresh read-only catalog afterward.
   */
  async reconnect(): Promise<void> {
    this.failPending(new Error("bridge reconnect — pending request cancelled (no auto-replay)"));
    await this.shutdown();
    await this.ensureStarted();
  }

  async shutdown(): Promise<void> {
    if (this.proc && !this.destroyed) {
      try {
        await this.request("shutdown", {}, 5_000);
      } catch {
        /* ignore */
      }
    }
    this.killProcess();
  }

  private killProcess(): void {
    this.destroyed = true;
    const proc = this.proc;
    if (proc) {
      // SIGTERM first, then SIGKILL on a 2 s grace — a bridge wedged in an
      // uninterruptible native wait (zmq poll, open-ended SDK call) defers
      // SIGTERM forever, and every reconnect left one more orphan in the
      // process table (user report 2026-10-05). proc.exited is bun's
      // completion promise; we never await it here — kill() then forget.
      try {
        proc.kill();
      } catch {
        /* ignore */
      }
      const killer = setTimeout(() => {
        try {
          proc.kill("SIGKILL");
        } catch {
          /* already gone */
        }
      }, 2_000);
      killer.unref?.();
      this.proc = null;
    }
  }

  private onProcessExit(error: Error): void {
    if (this.destroyed && this.pending.size === 0) return;
    this.destroyed = true;
    this.proc = null;
    this.failPending(error);
    this.emit("exit", error);
  }

  /**
   * Guarded exit report: only the current process generation's reader may
   * act. Stale readers (from a bridge process replaced by reconnect) must
   * not touch the new process's state — see processGen.
   */
  private reportProcessExit(gen: number, error: Error): void {
    if (gen !== this.processGen) return;
    this.onProcessExit(error);
  }

  /** Fail all pending — never replay mutating commands. */
  failPending(error: Error): void {
    for (const [, pending] of this.pending) {
      if (pending.timer) clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private handleLine(line: string): void {
    const response = this.parseJsonLine(line);
    if (!response) return;
    if (this.emitBridgeEvent(response)) return;
    this.dispatchResponse(response);
  }

  /** Parse one JSONL line, emitting protocol_error on invalid JSON. */
  private parseJsonLine(line: string): Record<string, unknown> | undefined {
    try {
      // Every string from the bridge is untrusted display text (model
      // replies, tool output): no control character survives the parse
      // (c2-tui 2026-10-21 — a reply's OSC 52 wrote the user's clipboard).
      return JSON.parse(line, terminalSafeReviver) as Record<string, unknown>;
    } catch (err) {
      this.emit(
        "protocol_error",
        new Error(`invalid bridge JSON: ${err instanceof Error ? err.message : String(err)}`),
      );
      return undefined;
    }
  }

  /** Route one-way bridge events; returns true when the line was an event. */
  private emitBridgeEvent(response: Record<string, unknown>): boolean {
    const routed = routeBridgeLine(response);
    if (routed.kind === "response") return false;
    this.emit(routed.channel, routed.payload);
    return true;
  }

  /** Resolve or reject the pending request matching response.id. */
  private dispatchResponse(response: Record<string, unknown>): void {
    const id = response.id as number | undefined;
    const pending = id === undefined ? undefined : this.pending.get(id);
    if (!pending) {
      this.emit("unmatched", response);
      return;
    }
    if (pending.timer) clearTimeout(pending.timer);
    this.pending.delete(id as number);
    if (response.ok) {
      pending.resolve(response.result);
    } else {
      pending.reject(bridgeReplyError(response));
    }
  }
}

let shared: CompanionBridgeClient | null = null;

/**
 * Tab-aware bridge provider (chat tabs, 2026-09-29). When registered, the
 * ACTIVE chat tab's bridge answers every getCompanionBridge() call — all
 * pre-tabs call sites keep working untouched (they always mean "the bridge
 * of the session the user is looking at"). Background tabs keep their own
 * bridges; their events route through tabs/registry.ts.
 */
let bridgeProvider: ((tabId?: string) => CompanionBridgeClient) | null = null;
export function registerCompanionBridgeProvider(
  provider: ((tabId?: string) => CompanionBridgeClient) | null,
): void {
  bridgeProvider = provider;
}

/**
 * The companion bridge for a tab. No argument means "the tab the user is
 * looking at" (the provider's active reading — today's every pre-tabs call
 * site). Pass `tabId` where a flow really means a specific tab (plan H-7
 * stage 5; tab-scoped pollers and handlers take their own `bridgeForTab`
 * directly, so no existing call site needs to change).
 */
export function getCompanionBridge(tabId?: string): CompanionBridgeClient {
  if (!tabId && shared) return shared;
  if (bridgeProvider) return bridgeProvider(tabId);
  if (!shared) shared = new CompanionBridgeClient();
  return shared;
}

/** Test helper — replace or clear the shared singleton. */
export function setCompanionBridgeForTests(client: CompanionBridgeClient | null): void {
  shared = client;
}

export async function shutdownCompanionBridge(): Promise<void> {
  if (shared) {
    await shared.shutdown();
    shared = null;
  }
  // Tab runtimes shut down through the registry (each tab owns its bridge).
  try {
    const { shutdownAllTabBridges } = await import("../tabs/registry");
    await shutdownAllTabBridges();
  } catch {
    /* registry absent in narrow test contexts */
  }
}
