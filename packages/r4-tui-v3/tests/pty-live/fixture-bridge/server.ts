/**
 * Fixture companion bridge for the pty-live offline journeys (plan H-9).
 *
 * Speaks the JSONL wire of `r4.companion.ui.bridge_server` (requests
 * `{id, method, params}` → `{id, ok, result}`; one-way lines `{event}`,
 * `{approval_request}`, control envelopes `{type, payload}`) from canned
 * replies: no Python, no r4, no network, no model. The TUI under test runs
 * in its normal CLI mode (not R4_TUI_BACKEND=offline), so the real chat
 * rail, approval/plan cards, tab bridges and remote watcher all run — only
 * the far end of the pipe is scripted.
 *
 * Launched by the `python` shim that fixture.ts writes (`-m
 * r4.companion.ui.bridge_server`). Every chat tab spawns its own copy, as
 * every tab spawns its own real bridge.
 */

import { appendFileSync } from "node:fs";
import { scriptFor, slashReply, type SlashLine, type TurnScript, type TurnStep } from "./turns";
import type {
  RpcResult,
  SandboxState,
  SnapshotMessage,
  SnapshotSession,
  VenueRuntime,
  WebAccessChoice,
} from "../../../src/bridge/rpc-types.gen";

type Json = Record<string, unknown>;

interface Message {
  role: "user" | "assistant";
  content: string;
  turn_id?: string;
}

/** session.metadata.plan_mode_state (session.py): a plan under revision
 * (keep_planning) stays pending but is not awaiting a card answer. */
interface PlanState {
  pending: string;
  revising: boolean;
}

/** A queued remote turn the fake worker answers over three venue_sync polls. */
interface RemoteTurn {
  polls: number;
  reply: string;
  userText: string;
}

/**
 * R4_PTY_FIXTURE_VENUE=frontend: the session is a frontend-venue session
 * with a running remote runtime (ADR-046 A2) — every send_message rides the
 * queue to the "worker", as bridge_server._send_remote does; rewind refuses.
 */
const FRONTEND = process.env.R4_PTY_FIXTURE_VENUE === "frontend";

/**
 * R4_PTY_FIXTURE_WEB=ask: the session carries `web_access` (ADR-099 D-2)
 * and opens without a choice, as a fresh real bridge does; the console asks.
 * Unset: the snapshot has no `web_access` key (a bridge before RS-1), so the
 * other journeys never meet the question.
 */
const WEB = process.env.R4_PTY_FIXTURE_WEB === "ask";

/**
 * R4_PTY_FIXTURE_SANDBOX=off: the session carries `sandbox` (ADR-101) and
 * opens with it off and a working bwrap, as a fresh real local bridge does;
 * session.sandbox_get / session.sandbox_set answer like sandbox_bridge.py.
 * Unset: no `sandbox` key (a bridge before ADR-101), so the other journeys
 * never meet the notice or the chip.
 */
const SANDBOX = process.env.R4_PTY_FIXTURE_SANDBOX === "off";
const SANDBOX_NOTICE_OFF =
  "Sandbox off: shell, Python and MCP servers run with your full permissions. " +
  "Turn it on with /sandbox on (this session) or r4 --sandbox on (at launch).";

/**
 * R4_PTY_FIXTURE_ASK=remote (with the frontend venue): the remote worker is
 * waiting on an ask_user question when the session opens (ADR-099 D-8);
 * session.venue_questions lists it and session.venue_answer closes it.
 */
const REMOTE_ASK = FRONTEND && process.env.R4_PTY_FIXTURE_ASK === "remote";
const remoteQuestion = {
  question_id: "q-fx-1",
  session_id: "",
  question: "Which fixture dataset should the remote worker use?",
  options: ["dataset A", "dataset B"],
  status: REMOTE_ASK ? "pending" : "answered",
  answer: "",
};
const UNCHOSEN: WebAccessChoice = { mode: "", domains: [], chosen_at: "", source: "" };

const PROTOCOL = 1;
const session = {
  id: `fx-${process.pid}`,
  messages: [] as Message[],
  turnSeq: 0,
  mode: "chat" as "chat" | "plan" | "autopilot",
  permission: "auto_safe" as SnapshotSession["permission_mode"],
  plan: null as PlanState | null,
  queue: [] as string[],
  remote: null as RemoteTurn | null,
  remoteSeq: 0,
  web: { ...UNCHOSEN } as WebAccessChoice,
  sandboxMode: "off" as "off" | "on",
  sandboxSource: "default_off",
};

/** sandbox_bridge.sandbox_state of the fixture's local session. */
function sandboxState(): SandboxState {
  const on = session.sandboxMode === "on";
  const tmp = `/tmp/r4-sandbox-fixture/s-${process.pid}/tmp`;
  return {
    mode: session.sandboxMode,
    source: session.sandboxSource,
    backend: "bwrap",
    available: true,
    reason: "",
    tmp_dir: on ? tmp : "",
    write_roots: on ? [process.cwd(), tmp] : [],
    network: on ? "isolated (web: off)" : "host",
    notice: on ? "" : SANDBOX_NOTICE_OFF,
    mcp_error: "",
    detail: ["Writes: only the workspace, a per-session temp directory and your extra_write_paths."],
  };
}

/** session.sandbox_set {mode, default?} (sandbox_bridge.session_sandbox_set). */
function sandboxSet(params: Json): RpcResult<"session.sandbox_set"> {
  const mode = String(params.mode ?? "");
  if (mode !== "on" && mode !== "off") throw new Error(`sandbox.refused: not on or off: ${mode}`);
  session.sandboxMode = mode;
  session.sandboxSource = "slash";
  return { sandbox: sandboxState(), restarted_mcp: [], still_running: [], mcp_error: "", state: snapshot() as never };
}

/** session.web_access_set / initialize's web_access (web_access_bridge.py). */
const WEB_MODES = new Set(["none", "allowlist", "public"]);

function webMode(params: Json): WebAccessChoice["mode"] {
  const raw = params.mode === "off" ? "none" : String(params.mode ?? "");
  if (!WEB_MODES.has(raw)) throw new Error(`web_access.invalid: unknown web access ${raw}`);
  return raw as WebAccessChoice["mode"];
}

function setWeb(params: Json): WebAccessChoice {
  const mode = webMode(params);
  const domains = mode === "allowlist" && Array.isArray(params.domains) ? params.domains.map(String) : [];
  if (mode === "allowlist" && domains.length === 0) {
    throw new Error("web_access.invalid: allowlist web access needs at least one domain");
  }
  session.web = { mode, domains, chosen_at: new Date().toISOString(), source: "prompt" };
  return session.web;
}

function initialize(params: Json): unknown {
  const web = params.web_access as Json | undefined;
  if (WEB && web && web.mode) setWeb(web);
  return { ...snapshot(), bridge_protocol: PROTOCOL, source: null, workspace_root: process.cwd() };
}

/** Pending server-initiated questions (approval_request id → resolver). */
const approvals = new Map<string, (decision: string) => void>();
let cancelTurn: (() => void) | null = null;

function emit(line: Json): void {
  process.stdout.write(`${JSON.stringify(line)}\n`);
}

function emitEvent(type: string, message = "", data: Json = {}): void {
  emit({ event: { type, message, data } });
}

/** _venue_runtime_projection of a running runtime (frontend sessions only). */
function venueRuntime(): VenueRuntime | null {
  if (!FRONTEND) return null;
  const waiting = remoteQuestion.status === "pending";
  const phase = waiting ? "awaiting_user" : session.remote ? "running" : "idle";
  return { state: "running", reason: "", lease_expires_at: null, source_names: [], default_source: "", web_policy: "", phase };
}

/** The snapshot's session block, typed by the bridge contract (rpc-types.gen.ts). */
function sessionBlock(): SnapshotSession {
  return {
    id: session.id,
    name: "fixture session",
    created_at: FIXTURE_STARTED,
    updated_at: FIXTURE_STARTED,
    model: "fixture-model",
    profile_default: "",
    trust_source: "",
    write_requires_plan: false,
    workflow_armed: false,
    knowledge_acquisition: { mode: "open", graphs: [] },
    model_provisioned: true,
    needs_model_selection: false,
    permission_mode: session.permission,
    interaction_mode: session.mode,
    plan_awaiting_confirmation: Boolean(session.plan && !session.plan.revising),
    plan_pending_request: session.plan?.pending ?? "",
    chat_queue: [...session.queue],
    venue: FRONTEND ? "frontend" : "local",
    venue_runtime: venueRuntime(),
    ...(WEB ? { web_access: { ...session.web } } : {}),
    ...(SANDBOX ? { sandbox: sandboxState() } : {}),
  };
}

const FIXTURE_STARTED = new Date().toISOString();

/** _message_to_dict: the ADR-057 turn id rides metadata, as on the real bridge.
 * No timestamp is recorded here; "" makes the TUI stamp the row on arrival. */
function messageRow(m: Message): SnapshotMessage {
  return { role: m.role, content: m.content, timestamp: "", metadata: m.turn_id ? { turn_id: m.turn_id } : {} };
}

const EMPTY_USAGE: RpcResult<"get_state">["usage"] = {
  summary: {
    llm_calls: 0,
    compaction_calls: 0,
    subagent_calls: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    estimated_calls: 0,
  },
  active_endpoints: [],
  budget: {
    node_hours_estimated: 0,
    active_endpoint_count: 0,
    nearest_expiry_seconds: null,
    warning_threshold_seconds: 0,
    block_threshold_seconds: 0,
  },
};

/** CompanionBridgeServer.snapshot, typed by the bridge contract. */
function snapshot(): RpcResult<"get_state"> {
  return {
    session: sessionBlock(),
    config: { profile: "fixture", gateway_url: "", project_id: "", source_name: "fixture", username: "", credential: "none" },
    total_messages: session.messages.length,
    message_window: 200,
    messages: session.messages.map(messageRow),
    skills: [],
    usage: EMPTY_USAGE,
    active_endpoints: [],
    progress: { orientation: null },
  };
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Wait for the TUI's approval_response line for `id` (or a cancel). */
function awaitApproval(id: string): Promise<string> {
  return new Promise((resolve) => approvals.set(id, resolve));
}

let approvalSeq = 0;

/** bridge_server._on_tool_approval's legacy line: integer id, risk tier, lead agent (""). */
async function runApprovalStep(step: Extract<TurnStep, { approval: unknown }>): Promise<void> {
  approvalSeq += 1;
  const id = approvalSeq;
  emit({
    approval_request: {
      id,
      ...step.approval,
      agent: "",
      risk_tier: "risky",
      session_approval_allowed: true,
      options: ["once", "always", "reject"],
    },
  });
  const decision = await awaitApproval(String(id));
  emitEvent("response_start");
  const rejected = decision === "reject" || decision === "no";
  emitEvent("response_chunk", rejected ? "Understood — the tool call was rejected. " : "Tool ran. ");
}

async function runStep(step: TurnStep): Promise<void> {
  if ("wait" in step) return sleep(step.wait);
  if ("chunk" in step) return emitEvent("response_chunk", step.chunk);
  if ("round" in step) return emitEvent("response_start");
  if ("thinking" in step) return emitThinking(step.thinking);
  if ("approval" in step) return runApprovalStep(step);
}

let thinkingSeq = 0;

/** Canonical control envelopes (control_schema.ThinkingDelta / ThinkingDone). */
function emitThinking(text: string | null): void {
  if (text === null) {
    emit({ type: "thinking_done", payload: { version: 1, session_id: session.id, summary_hint: "" } });
    return;
  }
  const seq = thinkingSeq++;
  emit({ type: "thinking_delta", payload: { version: 1, session_id: session.id, seq, text, partial: true } });
}

/** Run a turn's steps; resolves false when the TUI cancelled it. */
async function runSteps(steps: TurnStep[]): Promise<boolean> {
  let cancelled = false;
  const stop = new Promise<void>((resolve) => {
    cancelTurn = () => {
      cancelled = true;
      resolve();
    };
  });
  const play = (async () => {
    for (const step of steps) {
      if (cancelled) return;
      await runStep(step);
    }
  })();
  await Promise.race([play, stop]);
  cancelTurn = null;
  // The real bridge acknowledges a cancelled turn with this line.
  if (cancelled) emit({ turn_stopped: { ok: true } });
  return !cancelled;
}

function priorUserTurns(): number {
  return session.messages.filter((m) => m.role === "user").length;
}

function openTurn(text: string): string {
  session.turnSeq += 1;
  const turnId = `t${session.turnSeq}`;
  session.messages.push({ role: "user", content: text, turn_id: turnId });
  emitEvent("turn_opened", "", { turn_id: turnId });
  return turnId;
}

/** The reply text a script ends with ({prior} = earlier user turns this session). */
function replyText(script: TurnScript, priorTurns: number): string {
  return script.reply.replace("{prior}", String(priorTurns));
}

/** A plan reply leaves the session awaiting the card — in plan mode only (session.py). */
function notePlan(script: TurnScript, text: string): void {
  if (script.plan && session.mode === "plan") session.plan = { pending: text, revising: false };
}

async function sendMessage(params: Json): Promise<RpcResult<"send_message">> {
  const text = String(params.text ?? "").trim();
  if (!text) throw new Error("message text is required");
  if (session.plan) session.plan.revising = false;
  const script = scriptFor(text);
  const prior = priorUserTurns();
  if (FRONTEND) return queueRemote(replyText(script, prior), text);
  const turnId = openTurn(text);
  emitEvent("response_start");
  const finished = await runSteps(script.steps);
  if (!finished) return { response: "", cancelled: true, turn_id: turnId, state: snapshot() };
  const reply = replyText(script, prior);
  emitEvent("response_chunk", reply);
  session.messages.push({ role: "assistant", content: reply });
  notePlan(script, text);
  const result: RpcResult<"send_message"> = { response: reply, turn_id: turnId, state: snapshot() };
  if (script.citations) result.citations = script.citations;
  return result;
}

/** bridge_server._send_remote: the turn rides the server queue to the worker. */
function queueRemote(reply: string, text: string): RpcResult<"send_message"> {
  session.remote = { polls: 0, reply, userText: text };
  return { response: "", state: snapshot(), remote: true, queued: true, queue: [{ content: text, client_id: "tui" }] };
}

/** session_venue_sync: runtime (phase, partial, turn_seq, tool_activity) + snapshot. */
function venueSync(): RpcResult<"session.venue_sync"> {
  const remote = session.remote;
  const base = { adopted: false, venue_state: "running", lease_remaining_sec: 3600, turn_seq: session.remoteSeq };
  if (!remote) return { ...base, runtime: { phase: "idle", turn_seq: session.remoteSeq }, state: snapshot() };
  remote.polls += 1;
  if (remote.polls < 3) {
    const partial = remote.reply.slice(0, Math.ceil((remote.reply.length * remote.polls) / 3));
    const runtime = { phase: "running", partial, tool_activity: ["fx_remote_probe(scope=fixture)"], turn_seq: session.remoteSeq + 1 };
    return { ...base, runtime, state: snapshot() };
  }
  // The worker finished: the journal row moved, the bridge adopts it.
  session.remoteSeq += 1;
  session.turnSeq += 1;
  session.messages.push({ role: "user", content: remote.userText, turn_id: `t${session.turnSeq}` });
  session.messages.push({ role: "assistant", content: remote.reply });
  session.remote = null;
  return { ...base, adopted: true, turn_seq: session.remoteSeq, runtime: { phase: "idle", turn_seq: session.remoteSeq }, state: snapshot() };
}

/** ADR-104 D-1: accepting a plan switches the mode (a → autopilot, m → chat). */
const PLAN_MODE: Record<string, "autopilot" | "chat"> = { execute_auto: "autopilot", execute_ask: "chat" };

/** agent_mode.permission_for: the permission derived from the mode. */
function permissionFor(mode: string): SnapshotSession["permission_mode"] {
  return mode === "autopilot" ? "trust" : "auto_safe";
}

/** confirm_plan (bridge_server + session.confirm_plan). */
async function confirmPlan(params: Json): Promise<RpcResult<"confirm_plan">> {
  const action = String(params.action ?? "").trim().toLowerCase();
  if (!action) throw new Error("action is required");
  if (!session.plan || session.mode !== "plan") throw new Error("no plan awaiting confirmation");
  if (action === "keep_planning") {
    session.plan.revising = true;
    return { response: "", state: snapshot() };
  }
  const mode = PLAN_MODE[action];
  if (!mode) throw new Error(`unknown plan action: ${action} (execute_auto|execute_ask|keep_planning)`);
  session.mode = mode;
  session.permission = permissionFor(mode);
  session.plan = null;
  emitEvent("response_start");
  const reply = `Executing the fixture plan (${action}): step 1 done, step 2 done.`;
  emitEvent("response_chunk", reply);
  session.messages.push({ role: "assistant", content: reply });
  return { response: reply, state: snapshot() };
}

const MODE_LINE: Record<string, string> = {
  plan: "mode → plan (read-only tools only; the first reply is a plan)",
  chat: "mode → chat",
  autopilot: "mode → autopilot",
};

/** command_bridge.set_mode's lines (ADR-104 D-1: one mode, permission derived). */
function setMode(target: string): SlashLine[] {
  if (target !== "plan" && target !== "chat" && target !== "autopilot") {
    return [{ kind: "error", text: `unknown mode: ${target}` }];
  }
  const lines: SlashLine[] = [];
  if (session.mode === "plan" && target !== "plan" && session.plan) {
    lines.push({ kind: "system", text: "pending plan cleared (left plan mode)" });
    session.plan = null;
  }
  session.mode = target;
  session.permission = permissionFor(target);
  return [...lines, { kind: "system", text: MODE_LINE[target]! }];
}

/** set_interaction_mode (ADR-104): {mode} → {mode, output, state}. */
function setInteractionMode(params: Json): RpcResult<"set_interaction_mode"> {
  const target = String(params.mode ?? "").trim().toLowerCase();
  const output = setMode(target);
  if (output.some((line) => line.kind === "error")) throw new Error(`unknown mode: ${target}`);
  return { mode: session.mode, output, state: snapshot() };
}

function dispatchSlash(params: Json): RpcResult<"dispatch_slash_command"> {
  const command = String(params.command ?? "").trim();
  if (!command.startsWith("/")) throw new Error("slash command must start with '/'");
  const [name = "", ...rest] = command.split(/\s+/);
  if (name === "/clear" || name === "/new") {
    session.messages = [];
    return { ...slashResult(true, []), cleared: true };
  }
  if (name === "/mode" && rest[0]) return slashResult(true, setMode(rest[0]));
  const reply = slashReply(command);
  return slashResult(reply.handled, reply.output);
}

/** The full dispatch_slash_command result shape (bridge_server.py). */
function slashResult(handled: boolean, output: SlashLine[]): RpcResult<"dispatch_slash_command"> {
  return {
    handled,
    cleared: false,
    output,
    state: snapshot(),
    dashboard: null,
    follow_up_messages: [],
    dropped_turn_ids: [],
  };
}

/** session_truncate: cut at the user turn `turn_id` names. */
function truncate(params: Json): RpcResult<"session.truncate"> {
  const turnId = String(params.turn_id ?? "").trim();
  if (!turnId) throw new Error("turn_id is required");
  if (FRONTEND) throw new Error("venue.remote: rewind cannot cut a remote session's context — fork the session to run locally");
  const at = session.messages.findIndex((m) => m.role === "user" && m.turn_id === turnId);
  if (at < 0) throw new Error(`rewind.desync: turn ${turnId} is not in the session history (compacted or retried) — nothing was cut`);
  const removed = session.messages.length - at;
  session.messages = session.messages.slice(0, at);
  return { ok: true, removed, messages: at, state: snapshot() };
}

/** Session-local queue verbs (session_queue_append / _pop / _clear). */
function queueAppend(params: Json): RpcResult<"session.queue_append"> {
  const content = String(params.content ?? "").trim();
  if (!content) return { ok: false, queue: [...session.queue] };
  session.queue.push(content);
  return { ok: true, queue: [...session.queue] };
}

function queuePop(): RpcResult<"session.queue_pop"> {
  const content = session.queue.shift() ?? null;
  return { content, remaining: session.queue.length };
}

function queueClear(): RpcResult<"session.queue_clear"> {
  const cleared = session.queue.length;
  session.queue = [];
  return { ok: true, cleared };
}

/** list_slash_commands rows for the companion-only commands slashReply answers (registry text). */
const SLASH_CATALOG = [
  { name: "export", help: "Preview markdown or save: /export [path.md]", category: "export", takesArgs: true },
  {
    name: "artifact",
    help: "A2L artifact list/show/lineage/derive/status",
    category: "data",
    takesArgs: true,
    subcommands: ["list", "show", "lineage", "derive", "status"],
  },
];

/** No UMRS behind the fixture: the server-side lanes fail as an unreachable journal does. */
const noServer = (verb: string) => () => {
  throw new Error(`${verb}: UMRS journal unreachable (pty fixture has no backend)`);
};

/** R4_PTY_FIXTURE_SESSIONS=<n>: the /sessions listing holds n canned rows
 * (bridge_server list_sessions row shape: store row + sync badge). */
function fixtureSessions(): Json[] {
  const n = Number(process.env.R4_PTY_FIXTURE_SESSIONS ?? 0);
  return Array.from({ length: Number.isFinite(n) ? n : 0 }, (_, i) => ({
    id: `fxsess${String(i).padStart(2, "0")}-0000-0000-0000-000000000000`,
    name: `fixture session ${i} with a deliberately long descriptive name for the pane`,
    model: "fixture-model",
    created_at: "2026-10-21T10:00:00",
    updated_at: `2026-10-21T1${i % 10}:0${i % 6}:00`,
    venue: i % 2 ? "frontend" : "local",
    state: i % 3 ? "completed" : "ongoing",
    sync: i % 2 ? "mirrored" : "local-only",
  }));
}

/** One canned UMRS workflow execution (wave 72, 2026-10-24): `workflow.detail`
 * answers `{execution: record}` (bridge_server.workflow_detail) for this id
 * only; any other id fails as UMRS's not-found does. */
const FX_EXECUTION_ID = "fxwf-run-0001";
const FX_WORKFLOW = "fx-pipeline";

function workflowDetail(params: Json): RpcResult<"workflow.detail"> {
  const id = String(params.execution_id ?? "");
  if (id !== FX_EXECUTION_ID) throw new Error(`workflow.not_found: execution ${id} not found`);
  return {
    execution: {
      execution_id: FX_EXECUTION_ID,
      workflow_name: FX_WORKFLOW,
      status: "running",
      started_at: 1761300000,
      steps: [
        // UMRS's own word for a finished step (workflow_runner.py set_step).
        { name: "fetch", status: "succeeded" },
        { name: "prep", status: "succeeded" },
        { name: "train", status: "running" },
      ],
    },
  };
}

/** `workflow.package` (UMRS GET /workflows/{name}/package, relayed): a
 * five-step definition with a branch, so the overlay draws a real DAG. */
function workflowPackage(params: Json): RpcResult<"workflow.package"> {
  if (String(params.workflow_name ?? "") !== FX_WORKFLOW) throw new Error("workflow.not_found");
  return {
    workflow_name: FX_WORKFLOW,
    steps: [
      { name: "fetch" },
      { name: "prep", depends_on: ["fetch"] },
      { name: "train", depends_on: ["prep"] },
      { name: "report", depends_on: ["prep"] },
      { name: "eval", depends_on: ["train"] },
    ],
  };
}

/** Empty-but-real answers of the panes' boot-time reads (bridge_server.py shapes). */
const EMPTY_READS: Record<string, (params: Json) => unknown> = {
  get_resources: () => ({ resources: { gpu: [], cpu: null, memory: null, slurm_jobs: [], hostname: "pty-fixture" } }),
  get_artifacts: () => ({ artifacts: [] }),
  get_science_loop: () => ({ trajectory: [], stats: {}, session_id: session.id }),
  get_knowledge: () => ({ stats: {}, patterns: [], lessons: [], session_id: session.id }),
  "circles.list": () => ({ circles: [], count: 0 }),
  "circles.create": () => ({ circle: {}, created: true }),
  "circles.member_add": () => ({ circle: {}, members: [] }),
  "circles.member_remove": () => ({ members: [], project_id: "", circle_id: "" }),
  get_session_changes: () => ({ changes: [] }),
  get_session_plan: () => ({ steps: [] }),
  trust_list: (): RpcResult<"trust_list"> => ({ rows: [], count: 0 }),
  "workflow.list": (): RpcResult<"workflow.list"> => ({ workflows: [], count: 0 }),
  list_sessions: () => ({ sessions: fixtureSessions(), remote_available: false, remote_reason: "", sync_mode: "" }),
  list_skills: () => ({ skills: [] }),
  list_models: () => ({ models: [], current: "fixture-model" }),
  "session.venue_approvals": (): RpcResult<"session.venue_approvals"> => ({ approvals: [], count: 0 }),
  "session.venue_questions": (): RpcResult<"session.venue_questions"> => ({
    questions: remoteQuestion.status === "pending" ? [{ ...remoteQuestion, session_id: session.id }] : [],
  }),
};

/** session.venue_answer (bridge_server → POST …/questions/{qid}/answer). */
function venueAnswer(params: Json): RpcResult<"session.venue_answer"> {
  if (String(params.question_id ?? "") !== remoteQuestion.question_id) {
    throw new Error("venue.not_found: question not found");
  }
  if (remoteQuestion.status !== "pending") throw new Error("venue.question_closed: question q-fx-1 is answered");
  remoteQuestion.status = "answered";
  remoteQuestion.answer = String(params.answer ?? "");
  // What the worker does with it: the turn goes on and records the answer.
  session.messages.push({ role: "assistant", content: `remote worker got the answer: ${remoteQuestion.answer}` });
  return { question: { ...remoteQuestion, session_id: session.id } };
}

const HANDLERS: Record<string, (params: Json) => unknown> = {
  ...EMPTY_READS,
  initialize,
  "session.web_access_get": (): RpcResult<"session.web_access_get"> => ({
    web_access: { ...session.web },
    remembered: null,
    preset: false,
  }),
  "session.web_access_set": (params: Json): RpcResult<"session.web_access_set"> => ({ web_access: setWeb(params) }),
  ...(SANDBOX
    ? {
        "session.sandbox_get": (): RpcResult<"session.sandbox_get"> => ({ sandbox: sandboxState() }),
        "session.sandbox_set": sandboxSet,
      }
    : {}),
  send_message: sendMessage,
  confirm_plan: confirmPlan,
  dispatch_slash_command: dispatchSlash,
  set_interaction_mode: setInteractionMode,
  "workflow.detail": workflowDetail,
  "workflow.package": workflowPackage,
  list_slash_commands: () => ({ commands: SLASH_CATALOG }),
  "session.truncate": truncate,
  "session.venue_sync": venueSync,
  "session.venue_answer": venueAnswer,
  "session.queue_remote": noServer("session.queue_remote"),
  "session.queue_append": queueAppend,
  "session.queue_pop": queuePop,
  "session.queue_clear": queueClear,
  set_model: () => snapshot(),
  shutdown: () => {
    setTimeout(() => process.exit(0), 20);
    return {};
  },
};

async function answer(request: Json): Promise<void> {
  const id = request.id;
  const handler = HANDLERS[String(request.method)];
  try {
    // bridge_server answers an unrouted method with this error, so must we.
    if (!handler) throw new Error(`unknown method: ${String(request.method)}`);
    const result = await handler((request.params as Json) ?? {});
    emit({ id, ok: true, result });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    emit({ id, ok: false, error: { type: "ValueError", message } });
  }
}

/** Out-of-band lines (no id): approval / ask_user answers and cancels. */
function handleControl(line: Json): void {
  const reply = line.approval_response as { id?: unknown; decision?: unknown } | undefined;
  if (reply) {
    const key = String(reply.id);
    approvals.get(key)?.(String(reply.decision ?? "reject"));
    approvals.delete(key);
    return;
  }
  if (line.cancel_turn) cancelTurn?.();
}

/** R4_PTY_FIXTURE_LOG=<file>: append every line the TUI sends (debugging aid). */
const TRACE = process.env.R4_PTY_FIXTURE_LOG;

function trace(raw: string): void {
  if (TRACE) appendFileSync(TRACE, `${process.pid} ${raw}\n`);
}

function onLine(raw: string): void {
  trace(raw);
  let line: Json;
  try {
    line = JSON.parse(raw) as Json;
  } catch {
    return;
  }
  if (line.id !== undefined && line.method) void answer(line);
  else handleControl(line);
}

/** Split the stdin byte stream into lines and hand each to onLine. */
function makeLineSplitter(): (chunk: Uint8Array) => void {
  const decoder = new TextDecoder();
  let buffer = "";
  return (chunk) => {
    buffer += decoder.decode(chunk, { stream: true });
    let nl = buffer.indexOf("\n");
    while (nl !== -1) {
      const raw = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (raw) onLine(raw);
      nl = buffer.indexOf("\n");
    }
  };
}

async function main(): Promise<void> {
  const reader = Bun.stdin.stream().getReader();
  const feed = makeLineSplitter();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) feed(value);
  }
  process.exit(0);
}

void main();
