import { createHash } from "node:crypto";
import { appendFile, lstat, readFile, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { computeEditStats } from "./edit-session-core";

export type ToolDecision = "allow" | "deny";
export type ToolAccess = "allow" | "approval";

export interface AgentToolRule {
  access: ToolAccess;
  pathArgument?: string;
  commands?: Record<string, string[]>;
}

export interface AgentToolPolicy {
  name: string;
  tools: Record<string, AgentToolRule>;
}

export interface AgentToolRequest {
  sessionId: string;
  requestId?: string;
  toolCallId?: string;
  name: string;
  arguments: Record<string, unknown>;
  /** When true, skip the approval prompt for tools requiring it. */
  autoApprove?: boolean;
}

export interface AgentToolApproval {
  tool: string;
  action: string;
  scope: string;
}

export interface AgentToolAuditEvent {
  timestamp: string;
  session_id: string;
  tool: string;
  decision: ToolDecision;
  policy: string;
  evidence_hash: string;
  auto_approved?: boolean;
}

export type AgentToolHandler = (
  arguments_: Record<string, unknown>,
  context: {
    workspaceRoot: string;
    signal?: AbortSignal;
    /** Infrastructure identity — never LLM-controlled. */
    sessionId?: string;
    toolCallId?: string;
  },
) => Promise<unknown>;

export type ApprovalPrompt = (request: AgentToolApproval) => Promise<boolean>;
export type AuditWriter = (event: AgentToolAuditEvent) => Promise<void>;
export type ToolStateListener = (
  request: AgentToolRequest,
  status: "approved" | "denied" | "running" | "completed" | "failed",
) => void;

export class AgentToolRegistry {
  private readonly handlers = new Map<string, AgentToolHandler>();

  register(name: string, handler: AgentToolHandler): void {
    if (!name.trim() || this.handlers.has(name)) throw new Error(`Tool registration rejected: ${name}`);
    this.handlers.set(name, handler);
  }

  /** Replace an existing registration (or add a new one). Throws on invalid names. */
  override(name: string, handler: AgentToolHandler): void {
    if (!name.trim()) throw new Error("Tool registration rejected: empty name");
    this.handlers.set(name, handler);
  }

  get(name: string): AgentToolHandler | undefined {
    return this.handlers.get(name);
  }

  names(): string[] {
    return [...this.handlers.keys()].sort();
  }
}

export class AgentToolBridge {
  private readonly activeCalls = new Map<string, { requestId?: string; controller: AbortController }>();
  private stateListener: ToolStateListener | undefined;

  constructor(
    private readonly workspaceRoot: string,
    private readonly policy: AgentToolPolicy,
    private readonly registry: AgentToolRegistry,
    private readonly approve: ApprovalPrompt,
    private readonly audit: AuditWriter,
  ) {}

  listTools(): Array<{ name: string; description: string }> {
    return this.registry.names()
      .filter((name) => this.policy.tools[name] !== undefined)
      .map((name) => ({ name, description: describeTool(name) }));
  }

  setStateListener(listener: ToolStateListener): void {
    this.stateListener = listener;
  }

  async call(request: AgentToolRequest): Promise<unknown> {
    const controller = new AbortController();
    if (request.toolCallId) {
      this.activeCalls.set(request.toolCallId, { requestId: request.requestId, controller });
    }
    try {
      return await this.callWithSignal(request, controller.signal);
    } finally {
      if (request.toolCallId) this.activeCalls.delete(request.toolCallId);
    }
  }

  cancel(requestId: string): void {
    for (const active of this.activeCalls.values()) {
      if (active.requestId === requestId) active.controller.abort();
    }
  }

  private async callWithSignal(request: AgentToolRequest, signal: AbortSignal): Promise<unknown> {
    const rule = this.policy.tools[request.name];
    const handler = this.registry.get(request.name);
    if (!rule || !handler) return this.deny(request, "unknown");

    const scope = await this.resolveScope(request.arguments, rule);
    if (rule.pathArgument && !scope) return this.deny(request, "outside-workspace");
    if (rule.commands && !isAllowedCommand(request.arguments, rule.commands)) {
      return this.deny(request, "command-not-allowed");
    }

    let allowed = rule.access === "allow";
    if (rule.access === "approval") {
      if (!request.autoApprove) {
        try {
          allowed = await this.approve({ tool: request.name, action: describeAction(request), scope });
        } catch {
          allowed = false;
        }
      } else {
        allowed = true;
      }
    }
    await this.record(request, allowed ? "allow" : "deny", scope, request.autoApprove);
    if (!allowed) {
      this.stateListener?.(request, "denied");
      throw new Error(`Tool denied by policy: ${request.name}`);
    }
    this.stateListener?.(request, "approved");
    signal.throwIfAborted();
    this.stateListener?.(request, "running");
    try {
      const result = await handler(request.arguments, {
        workspaceRoot: this.workspaceRoot,
        signal,
        sessionId: request.sessionId,
        toolCallId: request.toolCallId,
      });
      this.stateListener?.(request, "completed");
      return result;
    } catch (error) {
      this.stateListener?.(request, "failed");
      throw error;
    }
  }

  private async resolveScope(arguments_: Record<string, unknown>, rule: AgentToolRule): Promise<string> {
    if (!rule.pathArgument) return this.workspaceRoot;
    const value = arguments_[rule.pathArgument];
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      return await resolveContainedPath(this.workspaceRoot, value);
    } catch {
      return "";
    }
  }

  private async deny(request: AgentToolRequest, scope: string): Promise<never> {
    await this.record(request, "deny", scope, request.autoApprove);
    this.stateListener?.(request, "denied");
    throw new Error(`Tool denied by policy: ${request.name}`);
  }

  private async record(
    request: AgentToolRequest,
    decision: ToolDecision,
    scope: string,
    autoApproved?: boolean,
  ): Promise<void> {
    const evidence = JSON.stringify({ session: request.sessionId, tool: request.name, decision, scope });
    await this.audit({
      timestamp: new Date().toISOString(),
      session_id: request.sessionId,
      tool: request.name,
      decision,
      policy: this.policy.name,
      evidence_hash: `sha256:${createHash("sha256").update(evidence).digest("hex")}`,
      auto_approved: autoApproved,
    });
  }
}

function isAllowedCommand(
  arguments_: Record<string, unknown>,
  commands: Record<string, string[]>,
): boolean {
  const command = arguments_.command;
  const args = arguments_.args;
  if (typeof command !== "string" || !Array.isArray(args) || !args.every((arg) => typeof arg === "string")) {
    return false;
  }
  const allowedSubcommands = commands[command];
  if (!allowedSubcommands) return false;
  if (args.some((arg) => path.isAbsolute(arg) || arg === ".." || arg.startsWith("../") || arg.includes("/../"))) {
    return false;
  }
  if (command === "git" && args.slice(1).some((arg) => arg.startsWith("-"))) return false;
  return allowedSubcommands.length === 0 || allowedSubcommands.includes(args[0] ?? "");
}

export function createCoreToolRegistry(): AgentToolRegistry {
  const registry = new AgentToolRegistry();
  registry.register("workspace.readFile", async (arguments_, context) => {
    const target = await requireContainedPath(arguments_, "path", context.workspaceRoot);
    const content = await readFile(target, "utf8");
    context.signal?.throwIfAborted();
    return { content };
  });
  registry.register("workspace.writeFile", async (arguments_, context) => {
    const target = await requireContainedPath(arguments_, "path", context.workspaceRoot);
    const content = arguments_.content;
    if (typeof content !== "string") throw new Error("content must be a string");
    context.signal?.throwIfAborted();
    let previous: string | undefined;
    try {
      previous = await readFile(target, "utf8");
    } catch {
      previous = undefined;
    }
    await writeFile(target, content, "utf8");
    context.signal?.throwIfAborted();
    const stats = computeEditStats(previous, content);
    return { path: target, added: stats.added, removed: stats.removed };
  });
  return registry;
}

export function createJsonlAuditWriter(filePath: string): AuditWriter {
  return async (event) => appendFile(filePath, `${JSON.stringify(event)}\n`, { encoding: "utf8", mode: 0o600 });
}

async function requireContainedPath(
  arguments_: Record<string, unknown>,
  name: string,
  root: string,
): Promise<string> {
  const value = arguments_[name];
  if (typeof value !== "string" || !value.trim()) throw new Error(`${name} must be a non-empty string`);
  return resolveContainedPath(root, value);
}

export async function resolveContainedPath(root: string, value: string): Promise<string> {
  const canonicalRoot = await realpath(root);
  const candidate = path.resolve(root, value);
  const canonicalCandidate = await canonicalizeCandidate(candidate);
  const relative = path.relative(canonicalRoot, canonicalCandidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Path is outside workspace");
  return canonicalCandidate;
}

async function canonicalizeCandidate(candidate: string): Promise<string> {
  try {
    await lstat(candidate);
    return realpath(candidate);
  } catch (error) {
    if (!isMissingPathError(error)) throw error;
    const parent = path.dirname(candidate);
    if (parent === candidate) throw error;
    return path.join(await canonicalizeCandidate(parent), path.basename(candidate));
  }
}

function isMissingPathError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

function describeAction(request: AgentToolRequest): string {
  switch (request.name) {
    case "workspace.writeFile":
      return `Write workspace file: ${String(request.arguments.path ?? "")}`;
    case "terminal.run":
      return `Run process without a shell: ${formatCommand(request.arguments)}`;
    default:
      return `Run ${request.name}`;
  }
}

function formatCommand(arguments_: Record<string, unknown>): string {
  const command = typeof arguments_.command === "string" ? arguments_.command : "";
  const args = Array.isArray(arguments_.args)
    ? arguments_.args.filter((value): value is string => typeof value === "string")
    : [];
  return [command, ...args].map((value) => JSON.stringify(value)).join(" ");
}

function describeTool(name: string): string {
  const descriptions: Record<string, string> = {
    "workspace.readFile": "Read a UTF-8 file inside the active VS Code workspace",
    "workspace.diagnostics": "Read VS Code diagnostics for a file inside the workspace",
    "workspace.applyEdit": "Stage anchored edits for review in a native diff (applied on accept)",
    "workspace.writeFile": "Write a UTF-8 file inside the workspace after approval",
    "terminal.run": "Run a command without a shell inside the workspace after approval",
  };
  return descriptions[name] ?? name;
}
