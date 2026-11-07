/**
 * Data source backed by the `r4` Python SDK.
 *
 * - Backend inventory (endpoints, models, jobs, skills) still comes from the
 *   `r4` CLI because those endpoints are fast, stable, and JSON-friendly.
 * - Chat is routed directly through the R4 Python client (`r4.client.Client`) so
 *   it reuses active-source authentication, model routing, retries, and the
 *   gateway `/gw/llm` path instead of the brittle `r4 chat` subcommand.
 */

import type {
  ChatHistoryItem,
  DataSource,
  KnowledgeAdapter,
  WorkspaceSnapshot,
  BackendSnapshot,
  ChatStreamChunk,
  ActionResult,
  ProjectKnowledgePollResult,
} from "./types";
import { companionUnavailableReply, generateLocalReply } from "../lib/chat-reply";
import { mapEndpoint, mapJob, mapModel, mapSkill } from "./mappers";
import type { UiSkill } from "../types";
import {
  type ProjectListEntry,
  mapArtifactsToKnowledgeObjects,
  mapResearchPlanFile,
  mapResearchPlanToTasks,
  mapSessionRow,
  pickActiveProject,
} from "./knowledge-mappers";
import { readResearchPlanFile } from "../lib/r4-project-path";
import { resolvePython, resolveR4Command } from "../lib/resolve-python";
import { parseTerminalSafe, terminalSafe } from "../lib/terminal-safe";
import type { UiEndpoint, UiJob, UiModel } from "../types";

export { resolvePython, resolveR4Command } from "../lib/resolve-python";

interface StreamLineEvent {
  delta?: string;
  errored?: boolean;
}

interface DrainedStream {
  buffer: string;
  errored: boolean;
  sawDelta: boolean;
}

interface StreamOutcome {
  sawDelta: boolean;
  errored: boolean;
}

interface CliOutput {
  stdout: string;
  stderr: string;
  exitCode: number;
}

/** Exit status of a finished CLI child. A child killed by a signal (the
 * AbortSignal timeout sends SIGTERM) has exitCode null: that is a failure
 * (124, as timeout(1) reports), never exit 0 (TUI audit r2, 2026-10-17). */
export function cliExitCode(proc: { exitCode: number | null; signalCode?: string | number | null }): number {
  if (typeof proc.exitCode === "number") return proc.exitCode;
  return proc.signalCode ? 124 : 1;
}

function killedNote(args: string[], timeoutMs: number): string {
  return `r4 ${args.slice(0, 2).join(" ")} timed out after ${Math.round(timeoutMs / 1000)}s and was stopped — the request may still be in progress`;
}

/** Spawn env for PROJECT-SCOPED adapter subprocesses (knowledge client,
 * project verify, catalog list): the active TAB's project rides along as
 * R4_PROJECT_ID (per-tab selection lives only in the tab's bridge env, so a
 * bare Client() here resolved the global/profile project — audit r2 #54).
 * The global backend inventory probes never get it: a tab switch would swap
 * the job list and the poll diff would toast every job as "created". */
export async function adapterEnv(extra: Record<string, string> = {}): Promise<Record<string, string | undefined>> {
  const { workspace, activeTab } = await import("../stores");
  const projectId = activeTab().project?.id;
  return { ...process.env, ...(projectId ? { R4_PROJECT_ID: projectId } : {}), ...extra };
}

async function runR4(args: string[], timeoutMs = 10000, opts: { tabProject?: boolean } = {}): Promise<CliOutput> {
  const proc = Bun.spawn([...resolveR4Command(), ...args], {
    stdout: "pipe",
    stderr: "pipe",
    env: opts.tabProject ? await adapterEnv() : process.env,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const stdout = await new Response(proc.stdout).text();
  const stderr = await new Response(proc.stderr).text();
  await proc.exited;
  const exitCode = cliExitCode(proc);
  // Everything the CLI prints may reach the screen (c3-tui D-3); JSON
  // payloads are parsed with parseTerminalSafe (escaped controls).
  const out = { stdout: terminalSafe(stdout), stderr: terminalSafe(stderr), exitCode };
  if (!proc.signalCode) return out;
  return { ...out, stderr: [out.stderr.trim(), killedNote(args, timeoutMs)].filter(Boolean).join("\n") };
}

/** `endpoint up` submit budget: the CLI's own submit timeout
 * (R4_PROVISION_SUBMIT_TIMEOUT_SEC, default 300 s) plus a margin, so the TUI
 * never kills a submit the CLI is still legitimately waiting on. */
function provisionBudgetMs(): number {
  const sec = Number(process.env["R4_PROVISION_SUBMIT_TIMEOUT_SEC"]);
  return ((Number.isFinite(sec) && sec > 0 ? sec : 300) + 30) * 1000;
}

/** Knowledge-client subprocess budget (tests shorten it). */
let knowledgeTimeoutMs = 30000;

/** Test hook: shorten the knowledge-client timeout; no argument restores 30 s. */
export function _setKnowledgeTimeoutForTests(ms = 30000): void {
  knowledgeTimeoutMs = ms;
}

const FAILED_PROBE: CliOutput = { stdout: "", stderr: "", exitCode: 1 };

/** Rows of one JSON list payload; non-JSON output ("No jobs found.") → []. */
function probeRows(raw: string): Array<Record<string, unknown>> {
  try {
    return listRowsOf(parseTerminalSafe(raw));
  } catch {
    return [];
  }
}

function listRowsOf(p: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(p)) return p as Array<Record<string, unknown>>;
  if (!p || typeof p !== "object") return [];
  const obj = p as Record<string, unknown>;
  // Try common wrapper keys first (e.g. {"endpoints": [...]}), then any array.
  for (const key of ["endpoints", "models", "jobs", "skills", "results"]) {
    if (Array.isArray(obj[key])) return obj[key] as Array<Record<string, unknown>>;
  }
  const first = Object.values(obj).find((v) => Array.isArray(v));
  return (first as Array<Record<string, unknown>> | undefined) ?? [];
}

/** `<label>: <the CLI's own error line>`. r4 prints `Error (…): <what>`
 *  first and an indented multi-line hint after it — the LAST line was the
 *  hint's tail ("endpoints:        r4 health", slash-a#4, 2026-10-21). A
 *  traceback still yields its exception line (the last one). */
function probeProblem(label: string, out: CliOutput): string {
  const lines = (out.stderr.trim() || out.stdout.trim()).split("\n").map((l) => l.trim()).filter(Boolean);
  const errorLine = lines.find((l) => /^(Error\b|✗)/.test(l));
  const line = (errorLine ?? lines.at(-1) ?? "").replace(/^Error(?: \([^)]*\))?(?: \[[^\]]*\])?:\s*/, "");
  return `${label}: ${line || `exit ${out.exitCode}`}`;
}

/** A failed probe that never reached the network: no credential, or one the
 *  gateway refused — "backend unreachable" sent users hunting a dead server. */
const SIGNED_OUT = /no credential|authentication failed|not authenticated|unauthori[sz]ed|\b401\b|rejected/i;

/** The error when every gateway probe failed, worded by its cause. */
function backendDownError(problem: string): Error {
  if (SIGNED_OUT.test(problem)) return new Error(`not signed in — ${problem} — /connect (or /login <token>) to sign in`);
  return new Error(`backend unreachable — ${problem}`);
}

interface EndpointSourceError {
  source?: string;
  error?: string;
  hint?: string;
}

/** Multi-source `endpoint list --json` reports a failed source in `errors`
 * with exit 0 — read it, never let it look like "no endpoints". */
function endpointSourceErrors(raw: string): EndpointSourceError[] {
  try {
    const parsed = parseTerminalSafe(raw) as { errors?: unknown };
    return Array.isArray(parsed?.errors) ? (parsed.errors as EndpointSourceError[]) : [];
  } catch {
    return [];
  }
}

function sourceErrorText(err: EndpointSourceError): string {
  const head = `endpoints (${err.source ?? "?"}): ${err.error ?? "failed"}`;
  return err.hint ? `${head} — ${err.hint}` : head;
}

/**
 * Local CLI skill inventory (`r4 skill list --json`) — stateless rows (no
 * active flags). Fetched alongside the bridge registry when the /skills
 * overlay opens so the union is present from frame one instead of landing 8s
 * later via the background poller (the visible mid-click list growth).
 */
export async function listCliSkills(): Promise<UiSkill[]> {
  try {
    const out = await runR4(["skill", "list", "--json"], 10_000);
    if (out.exitCode !== 0) return [];
    const parsed = parseTerminalSafe(out.stdout || "[]") as unknown;
    const rows = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === "object" && Array.isArray((parsed as { skills?: unknown }).skills)
        ? ((parsed as { skills: unknown[] }).skills as unknown[])
        : [];
    return (rows as Array<Record<string, unknown>>).map(mapSkill);
  } catch {
    return [];
  }
}

function extractArray<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    for (const key of ["endpoints", "models", "jobs", "data", "items", "results"]) {
      const val = obj[key];
      if (Array.isArray(val)) return val as T[];
    }
  }
  return [];
}

/** True when the Python r4 module is importable (even if `r4` is not on PATH). */
export function probeR4Cli(): boolean {
  try {
    const proc = Bun.spawnSync([...resolveR4Command(), "--version"], { timeout: 5000 });
    return proc.exitCode === 0;
  } catch {
    return false;
  }
}

const CHAT_SCRIPT = `
import json, sys
from r4.client import Client

payload = json.load(sys.stdin)
messages = payload.get("messages", [])
model = payload.get("model")
temperature = payload.get("temperature", 0.7)
max_tokens = payload.get("max_tokens", 512)

try:
    client = Client()
    kwargs = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if model:
        kwargs["model"] = model
    response = client.chat(**kwargs)
    sys.stdout.write(response.content)
except Exception as exc:
    print(f"R4 client chat failed: {exc}", file=sys.stderr)
    sys.exit(1)
`;

const STREAM_SCRIPT = `
import json, sys
from r4.client import Client

payload = json.load(sys.stdin)
messages = payload.get("messages", [])
model = payload.get("model")
temperature = payload.get("temperature", 0.7)
max_tokens = payload.get("max_tokens", 512)

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

try:
    client = Client()
    kwargs = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if model:
        kwargs["model"] = model
    saw = False
    for piece in client.chat_stream(**kwargs):
        if piece:
            saw = True
            emit({"delta": piece})
    if not saw:
        emit({"error": "empty stream"})
        sys.exit(1)
    emit({"done": True})
except Exception as exc:
    emit({"error": str(exc)})
    sys.exit(1)
`;

export class R4CliDataSource implements DataSource {
  readonly name = "r4-cli";
  private lastEndpoints: UiEndpoint[] = [];
  private lastModels: UiModel[] = [];
  private lastJobs: UiJob[] = [];
  private lastSkills: UiSkill[] = [];
  private lastWorkingModel: string | null = null;

  async loadWorkspace(): Promise<WorkspaceSnapshot> {
    let project = null;
    try {
      const out = await runR4(["project", "list", "--json"], 5000);
      if (out.exitCode === 0 && out.stdout.trim()) {
        const entries = parseTerminalSafe(out.stdout) as ProjectListEntry[];
        project = pickActiveProject(entries);
      }
    } catch {
      // No projects or command unavailable.
    }
    return {
      project,
      goals: [],
      tasks: [],
      knowledge: [],
      agents: [],
      notifications: [],
      events: [],
      connection: { gateway: "connecting", umrs: "connecting", hpc: "connecting" },
    };
  }

  /**
   * Read-only snapshot: the project's knowledge from the server
   * (`/k2/artifacts`, K1.3), the local research plan file, and the
   * server-owned research sessions. Does not run `understand` / `readiness`
   * (those are pipeline commands).
   */
  async pollProjectKnowledge(projectId: string, _projectName = projectId): Promise<ProjectKnowledgePollResult> {
    const empty: ProjectKnowledgePollResult = {
      knowledge: [],
      tasks: [],
      ingestion: null,
      coverage: null,
      readiness: null,
      plan: null,
      loop: null,
      sessions: null,
    };

    let plan = null;
    let tasks: ProjectKnowledgePollResult["tasks"] = [];
    const planFile = readResearchPlanFile(projectId);
    if (planFile) {
      plan = mapResearchPlanFile(planFile);
      tasks = mapResearchPlanToTasks(plan);
    }

    // K1.3: the pane lists what the server lists (`r4 knowledge ls`), with
    // who added each item — not the local artifact_manifest.json, which a
    // durable import never wrote. Research sessions are server-owned too
    // (UMRS /research/sessions; audit r2 #53).
    const [listed, sessions] = await Promise.all([
      this._pollArtifacts(projectId),
      this._pollSessions(projectId),
    ]);
    const { knowledge, error: knowledgeError } = listed;

    if (knowledge.length === 0 && !plan) {
      return { ...empty, sessions, knowledgeError };
    }

    return {
      knowledgeError,
      knowledge,
      tasks,
      ingestion: null,
      coverage: null,
      readiness: null,
      plan,
      loop: null,
      sessions,
    };
  }

  /** The server's artifact rows (newest first), or why they could not be
   * read: an error is not an empty project (review of K1 part B). */
  private async _pollArtifacts(
    projectId: string,
  ): Promise<{ knowledge: ProjectKnowledgePollResult["knowledge"]; error: string | null }> {
    try {
      const data = await new R4CliKnowledgeAdapter().listArtifacts(projectId);
      if (data.error) return { knowledge: [], error: String(data.error) };
      return { knowledge: mapArtifactsToKnowledgeObjects(data.artifacts), error: null };
    } catch (err) {
      return { knowledge: [], error: err instanceof Error ? err.message : String(err) };
    }
  }

  /** null = unavailable (the client answers failures as `{error}`, never a
   * throw) — distinct from a real empty list, which only an array gives. */
  private async _pollSessions(projectId: string): Promise<import("../types").ResearchSession[] | null> {
    try {
      const data = await new R4CliKnowledgeAdapter().listSessions(projectId);
      if (data.error || !Array.isArray(data.sessions)) return null;
      return (data.sessions as Array<Record<string, unknown>>).map(mapSessionRow);
    } catch {
      return null;
    }
  }

  /** Backend inventory. Throws only when every GATEWAY probe failed (the
   * skill probe is local and always answers); a single failed probe keeps
   * that kind's previous rows and is named in `problems`, never wiped to []
   * (TUI audit r2 #45/#52/#56). */
  async pollBackend(): Promise<BackendSnapshot> {
    const [endpointsOut, modelsOut, jobsOut, skillsOut] = await Promise.all([
      runR4(["endpoint", "list", "--json", "--all"]).catch(() => FAILED_PROBE),
      runR4(["model", "list", "--json"]).catch(() => FAILED_PROBE),
      runR4(["job", "list", "--json", "--limit", "20"]).catch(() => FAILED_PROBE),
      runR4(["skill", "list", "--json"]).catch(() => FAILED_PROBE),
    ]);
    const remote = [endpointsOut, modelsOut, jobsOut];
    if (remote.every((o) => o.exitCode !== 0)) {
      throw backendDownError(probeProblem("endpoints", endpointsOut));
    }
    const problems: string[] = [];
    const endpoints = this.endpointRows(endpointsOut, problems);
    const models = this.keepOnFailure("models", modelsOut, mapModel, this.lastModels, problems);
    const jobs = this.keepOnFailure("jobs", jobsOut, mapJob, this.lastJobs, problems);
    // The skill probe is local (no gateway): its failure is not a backend problem.
    const skills = this.keepOnFailure("skills", skillsOut, mapSkill, this.lastSkills, []);
    this.lastEndpoints = endpoints;
    this.lastModels = models;
    this.lastJobs = jobs;
    this.lastSkills = skills;
    return { endpoints, models, jobs, skills, problems };
  }

  private keepOnFailure<T>(
    label: string,
    out: CliOutput,
    map: (raw: Record<string, unknown>) => T,
    previous: T[],
    problems: string[],
  ): T[] {
    if (out.exitCode === 0) return probeRows(out.stdout).map((row) => map(row));
    problems.push(probeProblem(label, out));
    return previous;
  }

  /** Endpoint rows; a failed probe keeps the previous rows, and a source the
   * multi-source listing reports in `errors` keeps ITS previous rows. */
  private endpointRows(out: CliOutput, problems: string[]): UiEndpoint[] {
    if (out.exitCode !== 0) {
      problems.push(probeProblem("endpoints", out));
      return this.lastEndpoints;
    }
    const rows = probeRows(out.stdout).map((row) => mapEndpoint(row));
    const errors = endpointSourceErrors(out.stdout);
    problems.push(...errors.map(sourceErrorText));
    const failed = new Set(errors.map((e) => e.source).filter(Boolean));
    if (failed.size === 0) return rows;
    const kept = this.lastEndpoints.filter((ep) => ep.sourceName && failed.has(ep.sourceName));
    return [...rows, ...kept.filter((ep) => !rows.some((r) => r.id === ep.id))];
  }

  private readyModels(preferred?: string): string[] {
    const models = new Set<string>();
    // Order matters: user/current selection, then the last model that actually
    // responded, then any other ready endpoints.
    const ordered: string[] = [];
    const push = (m: string) => {
      if (m && !models.has(m)) {
        models.add(m);
        ordered.push(m);
      }
    };
    if (preferred) push(preferred);
    if (this.lastWorkingModel) push(this.lastWorkingModel);
    for (const ep of this.lastEndpoints) {
      if (ep.status === "ready" && ep.model) push(ep.model);
    }
    return ordered;
  }

  private async runClientChat(messages: ChatHistoryItem[], model: string, timeoutMs: number): Promise<string> {
    const payload = {
      messages,
      model: model === "auto" ? null : model,
      temperature: 0.7,
      max_tokens: 512,
    };

    const python = resolvePython();
    const proc = Bun.spawn([python, "-c", CHAT_SCRIPT], {
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      signal: AbortSignal.timeout(timeoutMs),
    });
    proc.stdin.write(JSON.stringify(payload));
    proc.stdin.end();

    const stdout = await new Response(proc.stdout).text();
    const stderr = await new Response(proc.stderr).text();
    await proc.exited;

    if (proc.exitCode === 0 && stdout.trim()) {
      return terminalSafe(stdout.trim());
    }
    if (stderr.trim()) {
      throw new Error(terminalSafe(stderr.trim()));
    }
    throw new Error("Empty response from R4 client");
  }

  async chat(message: string, history?: ChatHistoryItem[], model?: string): Promise<string> {
    const messages: ChatHistoryItem[] = [...(history ?? []), { role: "user", content: message }];

    // Always try the R4 client first — it handles model routing,
    // active-source authentication, and gateway routing internally.
    // We don't need ready endpoints from the poll; the R4 client
    // will find the right model via the gateway.
    const candidates = this.readyModels(model);
    const modelsToTry = candidates.length > 0 ? candidates : [model ?? "auto"];

    for (let i = 0; i < modelsToTry.length; i++) {
      const timeoutMs = i === 0 ? 45000 : 15000;
      try {
        const reply = await this.runClientChat(messages, modelsToTry[i], timeoutMs);
        this.lastWorkingModel = modelsToTry[i];
        return reply;
      } catch {
        // Try the next candidate.
      }
    }

    // If every backend attempt fails, still give the user a coherent reply.
    return generateLocalReply(message, 0);
  }

  /** First ready model, else the requested model name, else "auto". */
  private resolveStreamModel(model?: string): string {
    const candidates = this.readyModels(model);
    if (candidates.length > 0) return candidates[0];
    return model ?? "auto";
  }

  private chatStreamPayload(messages: ChatHistoryItem[], chosen: string): Record<string, unknown> {
    return {
      messages,
      model: chosen === "auto" ? null : chosen,
      temperature: 0.7,
      max_tokens: 512,
    };
  }

  async *chatStream(
    message: string,
    history?: ChatHistoryItem[],
    model?: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamChunk> {
    const messages: ChatHistoryItem[] = [...(history ?? []), { role: "user", content: message }];
    const chosen = this.resolveStreamModel(model);

    // Attribution up front so the UI can label the reply immediately.
    yield { model: chosen };

    const { sawDelta, errored } = yield* this.streamChatDeltas(
      this.chatStreamPayload(messages, chosen),
      chosen,
      signal,
    );

    if (signal?.aborted) {
      yield { done: true };
      return;
    }
    if (!sawDelta || errored) {
      yield* this.fallbackChunk(errored);
    }
    yield { done: true };
  }

  private *fallbackChunk(errored: boolean): Generator<ChatStreamChunk> {
    yield {
      delta: companionUnavailableReply(errored ? "r4 chat stream error" : "empty r4 chat stream"),
      model: "offline",
      fallback: true,
    };
  }

  /** Parse one NDJSON stream line; null means "skip this line". */
  private parseStreamLine(line: string): StreamLineEvent | null {
    let obj: ChatStreamChunk & { error?: string };
    try {
      obj = parseTerminalSafe(line);
    } catch {
      return null;
    }
    if (obj.error) return { errored: true };
    if (typeof obj.delta === "string" && obj.delta.length > 0) return { delta: obj.delta };
    return null;
  }

  /** Drain complete NDJSON lines from the buffer; yields deltas, returns the rest. */
  private *drainStreamLines(buffer: string, chosen: string): Generator<ChatStreamChunk, DrainedStream> {
    let errored = false;
    let sawDelta = false;
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      const event = this.parseStreamLine(line);
      if (event?.errored) {
        errored = true;
        break;
      }
      if (event?.delta) {
        sawDelta = true;
        this.lastWorkingModel = chosen === "auto" ? this.lastWorkingModel : chosen;
        yield { delta: event.delta };
      }
    }
    return { buffer, errored, sawDelta };
  }

  /** Spawn the streaming chat process and yield deltas; reports outcome for fallback handling. */
  private async *streamChatDeltas(
    payload: Record<string, unknown>,
    chosen: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamChunk, StreamOutcome> {
    const python = resolvePython();
    const decoder = new TextDecoder();
    let buffer = "";
    let sawDelta = false;
    let errored = false;

    try {
      const proc = Bun.spawn([python, "-c", STREAM_SCRIPT], {
        stdin: "pipe",
        stdout: "pipe",
        stderr: "pipe",
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
        signal,
      });
      proc.stdin.write(JSON.stringify(payload));
      proc.stdin.end();

      for await (const bytes of proc.stdout as unknown as AsyncIterable<Uint8Array>) {
        if (signal?.aborted) break;
        buffer += decoder.decode(bytes, { stream: true });
        const drained = yield* this.drainStreamLines(buffer, chosen);
        buffer = drained.buffer;
        if (drained.sawDelta) sawDelta = true;
        if (drained.errored) {
          errored = true;
          break;
        }
      }
      await proc.exited;
    } catch {
      // Spawn/stream error — caller falls back to a canned reply.
    }
    return { sawDelta, errored };
  }

  private async runAction(args: string[], okMessage: string, timeoutMs = 30000): Promise<ActionResult> {
    try {
      const out = await runR4(args, timeoutMs);
      if (out.exitCode === 0) {
        return { ok: true, message: out.stdout.trim() || okMessage };
      }
      return { ok: false, message: out.stderr.trim() || out.stdout.trim() || `command failed (exit ${out.exitCode})` };
    } catch (err) {
      return { ok: false, message: err instanceof Error ? err.message : String(err) };
    }
  }

  async provisionEndpoint(model: string, tier?: string): Promise<ActionResult> {
    const args = ["endpoint", "up", "--model", model];
    if (tier) args.push("--tier", tier);
    return this.runAction(args, `Requested endpoint for ${model}`, provisionBudgetMs());
  }

  async stopEndpoint(id: string): Promise<ActionResult> {
    return this.runAction(["endpoint", "down", id], `Stopped endpoint ${id}`);
  }

  async cancelJob(id: string): Promise<ActionResult> {
    return this.runAction(["job", "cancel", id], `Cancelled job ${id}`);
  }

  async retryJob(id: string): Promise<ActionResult> {
    return this.runAction(["job", "retry", id], `Retried job ${id}`);
  }

  async getJobLogs(id: string, lines = 100): Promise<string> {
    // There is no `r4 job logs` subcommand (CLI: list/describe/cancel/retry).
    // Surface job describe as the inspect payload; note the lines hint for callers.
    void lines;
    try {
      const out = await runR4(["job", "describe", id], 20000);
      const body = out.stdout.trim() || out.stderr.trim();
      if (out.exitCode !== 0 && !body) {
        return `Could not describe job ${id} (exit ${out.exitCode}).`;
      }
      const header =
        `Job details for ${id} (r4 job describe — no dedicated log stream in CLI).\n` +
        `---\n`;
      return header + (body || `No details for job ${id}`);
    } catch (err) {
      return err instanceof Error ? err.message : String(err);
    }
  }

  async verifyKnowledge(projectId: string, question: string): Promise<Record<string, unknown>> {
    // Prefer local `r4 project verify` (project knowledge store) over KnowledgeClient HTTP.
    try {
      const out = await runR4(["project", "verify", projectId, question, "--json"], 10000, { tabProject: true });
      if (out.exitCode === 0 && out.stdout.trim()) {
        try {
          return parseTerminalSafe(out.stdout.trim()) as Record<string, unknown>;
        } catch {
          /* fall through */
        }
      }
      if (out.stderr.trim() || out.stdout.trim()) {
        const errText = (out.stderr || out.stdout).trim();
        // Still try KnowledgeClient below; only surface CLI error if that also fails.
        const fallback = await new R4CliKnowledgeAdapter().verify(projectId, question);
        if (!fallback.error) return fallback;
        return { error: errText.slice(0, 400) };
      }
    } catch {
      /* fall through to KnowledgeClient */
    }
    return new R4CliKnowledgeAdapter().verify(projectId, question);
  }

  async readiness(projectId: string): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().readiness(projectId);
  }

  // ── ADR-031 server-spine reads ───────────────────────────────────────

  async catalogSearch(query: string, opts: { limit?: number; modality?: string; state?: string; tag?: string; prefix?: string } = {}): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().catalogSearch(query, opts);
  }

  async scientificCatalogs(projectId?: string): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().scientificCatalogs(projectId);
  }

  async catalogRead(s3Key: string, opts: { offset?: number; length?: number; source?: string } = {}): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().catalogRead(s3Key, opts);
  }

  async catalogStats(): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().catalogStats();
  }

  async knowledgeStatus(projectId: string): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().knowledgeStatus(projectId);
  }

  async knowledgeFindings(projectId: string): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().findings(projectId);
  }

  async listGraphs(projectId?: string, opts: { status?: string; query?: string; limit?: number } = {}): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().graphs(projectId, opts);
  }

  async graphContent(graphId: string, opts: { nodeLimit?: number; edgeLimit?: number } = {}): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().graphContent(graphId, opts);
  }

  async graphProposals(graphId: string, opts: { status?: string } = {}): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().graphProposals(graphId, opts);
  }

  async decideGraphProposal(proposalId: string, approved: boolean): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().graphDecide(proposalId, approved);
  }

  async knowledgeQuery(projectId: string, query: string, opts: { scope?: string; limit?: number } = {}): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().knowledgeQuery(projectId, query, opts);
  }

  async reconcileKnowledge(projectId: string, sample = 50): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().reconcile(projectId, sample);
  }

  async knowledgeLineage(artifactId: string, projectId: string, depth = 3): Promise<Record<string, unknown>> {
    return new R4CliKnowledgeAdapter().lineage(artifactId, projectId, depth);
  }
}

const KNOWLEDGE_SCRIPT = `
import json, sys

payload = json.load(sys.stdin)
action = payload.pop("action")

try:
    from r4.client import Client as _Rc
    rc = _Rc()
except Exception as exc:
    sys.stdout.write(json.dumps({"error": f"r4 client init failed: {exc}"}))
    sys.exit(0)

knowledge = rc.knowledge
catalog = rc.data_catalog

try:
    if action == "ingest":
        result = knowledge.ingest(**payload)
    elif action == "extract":
        result = knowledge.extract(**payload)
    elif action == "link":
        result = knowledge.link(**payload)
    elif action == "verify":
        result = knowledge.verify(**payload)
    elif action == "coverage":
        result = knowledge.coverage(**payload)
    elif action == "memory":
        result = knowledge.memory(**payload)
    elif action == "readiness":
        result = knowledge.readiness(**payload)
    elif action == "plan":
        result = knowledge.plan(**payload)
    elif action == "start_loop":
        result = knowledge.start_loop(**payload)
    elif action == "stop_loop":
        result = knowledge.stop_loop(**payload)
    elif action == "get_loop":
        result = knowledge.get_loop(**payload)
    elif action == "list_loops":
        result = knowledge.list_loops(**payload)
    elif action == "list_sessions":
        result = rc.list_research_sessions(**payload)
    elif action == "list_artifacts":
        result = knowledge.list_artifacts(**payload)
    elif action == "create_session":
        result = rc.create_research_session(**payload)
    elif action == "get_session":
        result = rc.get_research_session(**payload)
    elif action == "correct":
        result = knowledge.correct(**payload)
    elif action == "catalog_search":
        result = catalog.search(**payload)
    elif action == "catalog_read":
        s3_key = payload.pop("s3_key")
        result = catalog.read(s3_key, **payload)
    elif action == "catalog_stats":
        result = catalog.stats(**payload)
    elif action == "knowledge_status":
        result = knowledge.status(payload["project_id"])
    elif action == "findings":
        result = knowledge.findings(payload["project_id"])
    elif action == "graphs":
        result = {"graphs": knowledge.graphs(**payload)}
    elif action == "graph_info":
        result = knowledge.graph_info(payload["graph_id"])
    elif action == "graph_content":
        graph_id = payload.pop("graph_id")
        result = knowledge.graph_content(graph_id, **payload)
    elif action == "graph_proposals":
        graph_id = payload.pop("graph_id")
        result = {"proposals": knowledge.graph_proposals(graph_id, **payload)}
    elif action == "graph_decide":
        proposal_id = payload.pop("proposal_id")
        result = knowledge.graph_decide(proposal_id, **payload)
    elif action == "knowledge_query":
        query = payload.pop("query")
        result = knowledge.query(query, **payload)
    elif action == "reconcile":
        project_id = payload.pop("project_id")
        result = knowledge.reconcile_project(project_id, **payload)
    elif action == "lineage":
        artifact_id = payload.pop("artifact_id")
        result = knowledge.lineage(artifact_id, **payload)
    else:
        result = {"error": f"Unknown action: {action}"}
    sys.stdout.write(json.dumps(result, default=str))
except Exception as exc:
    sys.stdout.write(json.dumps({"error": str(exc)}))
`;

/** Convert camelCase params to snake_case for KnowledgeClient Python APIs. */
export function camelParamsToSnake(params: Record<string, unknown>): Record<string, unknown> {
  const snakeParams: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params)) {
    const snake = key.replace(/[A-Z]/g, (ch) => `_${ch.toLowerCase()}`);
    snakeParams[snake] = value;
  }
  return snakeParams;
}

export class R4CliKnowledgeAdapter implements KnowledgeAdapter {
  readonly name = "r4-cli-knowledge";

  async ingest(projectId: string, files: string[]): Promise<Record<string, unknown>> {
    return this.runKnowledge("ingest", { projectId, files });
  }

  async extract(projectId: string, sourceId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("extract", { projectId, sourceId });
  }

  async link(projectId: string, sourceId: string, targetId: string, relationshipType: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("link", { projectId, sourceId, targetId, relationshipType });
  }

  async verify(projectId: string, question: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("verify", { projectId, question });
  }

  async coverage(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("coverage", { projectId });
  }

  async memory(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("memory", { projectId });
  }

  async readiness(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("readiness", { projectId });
  }

  async plan(projectId: string, researchGoal: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("plan", { projectId, researchGoal });
  }

  async startLoop(projectId: string, researchGoal: string, maxIterations: number): Promise<Record<string, unknown>> {
    return this.runKnowledge("start_loop", { projectId, researchGoal, maxIterations });
  }

  async stopLoop(projectId: string, loopId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("stop_loop", { projectId, loopId });
  }

  async getLoop(projectId: string, loopId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("get_loop", { projectId, loopId });
  }

  async listLoops(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("list_loops", { projectId });
  }

  async listSessions(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("list_sessions", { project_id: projectId });
  }

  /** K1.3: `GET /k2/artifacts` (KnowledgeClient.list_artifacts). */
  async listArtifacts(projectId: string, pageSize = 100): Promise<Record<string, unknown>> {
    return this.runKnowledge("list_artifacts", { projectId, pageSize });
  }

  async createSession(projectId: string, researchGoal: string, title?: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("create_session", {
      project_id: projectId,
      research_goal: researchGoal,
      title: title || "",
    });
  }

  async getSession(sessionId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("get_session", { session_id: sessionId });
  }

  async correct(projectId: string, nodeId: string, correction: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("correct", { projectId, nodeId, correction });
  }

  // ── ADR-031 server-spine surfaces ─────────────────────────────────────

  async catalogSearch(query: string, opts: { limit?: number; modality?: string; state?: string; tag?: string; prefix?: string } = {}): Promise<Record<string, unknown>> {
    return this.runKnowledge("catalog_search", { query, ...opts });
  }

  async catalogRead(s3Key: string, opts: { offset?: number; length?: number; source?: string } = {}): Promise<Record<string, unknown>> {
    return this.runKnowledge("catalog_read", { s3Key, ...opts });
  }

  async catalogStats(): Promise<Record<string, unknown>> {
    return this.runKnowledge("catalog_stats", {});
  }

  /** D-39 Scientific Catalogs via the `r4 catalog` CLI (durable registry). */
  async scientificCatalogs(projectId?: string): Promise<Record<string, unknown>> {
    const args = ["catalog", "list", "--json"];
    if (projectId) args.push("--project", projectId);
    const out = await runR4(args, 10000, { tabProject: true });
    if (out.exitCode !== 0) {
      return {
        catalogs: [],
        error: out.stderr.trim() || `r4 catalog list failed (exit ${out.exitCode})`,
      };
    }
    try {
      return parseTerminalSafe(out.stdout || "{}");
    } catch {
      return { catalogs: [], error: "catalog list returned non-JSON" };
    }
  }

  async knowledgeStatus(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("knowledge_status", { projectId });
  }

  async findings(projectId: string): Promise<Record<string, unknown>> {
    return this.runKnowledge("findings", { projectId });
  }

  async graphs(projectId?: string, opts: { status?: string; query?: string; limit?: number } = {}): Promise<Record<string, unknown>> {
    return this.runKnowledge("graphs", { projectId, status: opts.status ?? "active", ...opts });
  }

  async graphContent(graphId: string, opts: { nodeLimit?: number; edgeLimit?: number } = {}): Promise<Record<string, unknown>> {
    return this.runKnowledge("graph_content", { graphId, ...opts });
  }

  async graphProposals(graphId: string, opts: { status?: string } = {}): Promise<Record<string, unknown>> {
    return this.runKnowledge("graph_proposals", { graphId, ...opts });
  }

  async graphDecide(proposalId: string, approved: boolean): Promise<Record<string, unknown>> {
    return this.runKnowledge("graph_decide", { proposalId, approved });
  }

  async knowledgeQuery(projectId: string, query: string, opts: { scope?: string; limit?: number } = {}): Promise<Record<string, unknown>> {
    return this.runKnowledge("knowledge_query", { projectId, query, ...opts });
  }

  async reconcile(projectId: string, sample = 50): Promise<Record<string, unknown>> {
    return this.runKnowledge("reconcile", { projectId, sample });
  }

  async lineage(artifactId: string, projectId: string, depth = 3): Promise<Record<string, unknown>> {
    return this.runKnowledge("lineage", { artifactId, projectId, depth });
  }

  private async runKnowledge(action: string, params: Record<string, unknown>): Promise<Record<string, unknown>> {
    // KnowledgeClient Python APIs expect snake_case (project_id), not camelCase.
    const snakeParams = camelParamsToSnake(params);
    const python = resolvePython();
    const proc = Bun.spawn([python, "-c", KNOWLEDGE_SCRIPT], {
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
      env: await adapterEnv({ PYTHONUNBUFFERED: "1" }),
      signal: AbortSignal.timeout(knowledgeTimeoutMs),
    });
    proc.stdin.write(JSON.stringify({ action, ...snakeParams }));
    proc.stdin.end();

    const stdout = await new Response(proc.stdout).text();
    const stderr = await new Response(proc.stderr).text();
    await proc.exited;

    if (proc.signalCode) {
      return { error: `knowledge ${action} timed out after ${Math.round(knowledgeTimeoutMs / 1000)}s and was stopped` };
    }
    if (stdout.trim()) {
      try {
        return parseTerminalSafe(stdout.trim());
      } catch {
        return { error: "Failed to parse response", raw: terminalSafe(stdout.trim()) };
      }
    }
    if (stderr.trim()) {
      return { error: terminalSafe(stderr.trim()) };
    }
    return { error: "Empty response" };
  }
}
