/**
 * The live slash catalog, `/help`, and `/search-server` (ADR-103 VP-2d).
 *
 * The composer's `/` popup and `/help` read the bridge's own catalog
 * (`list_slash_commands`) plus the verbs this host answers itself, so a new
 * console command shows up here without an extension release. `/search-server`
 * drives the local SearXNG (ADR-102) over `search_server.*`; a start's
 * progress arrives as `search_server_event` lines, printed with the console's
 * texts (`lib/search-server-text.ts`). No vscode import.
 */

import {
  SEARCH_SERVER_BUSY,
  SEARCH_SERVER_STARTING,
  SEARCH_SERVER_STOPPED,
  SEARCH_SERVER_USAGE,
  searchServerEventText,
  searchServerStatusText,
  type SearchServerEvent,
} from "../../../packages/r4-tui-v3/src/lib/search-server-text";
import type { SearchServerStatusResult, WorktreeListResult } from "../../../packages/r4-tui-v3/src/bridge/rpc-types.gen";
import {
  WORKTREE_USAGE,
  parseWorktreeArgs,
  worktreeListText,
  worktreeModeText,
  type WorktreeArgs,
} from "../../../packages/r4-tui-v3/src/lib/worktree";

export type OutputLine = { kind: "info" | "error"; text: string };

export interface CatalogEntry {
  name: string;
  help: string;
  category: string;
  subcommands?: string[];
}

/** The verbs the host answers itself (the console intercepts the same ones). */
export const HOST_VERBS: readonly CatalogEntry[] = [
  { name: "help", help: "Commands by category; /help <command> for one", category: "help" },
  { name: "btw", help: "Side question about this conversation — no tools, not added to history (works mid-turn)", category: "session" },
  { name: "rewind", help: "Roll the conversation back to an earlier prompt; /rewind cancel keeps it", category: "session" },
  { name: "fork", help: "Fork this session into a new one", category: "session" },
  { name: "changes", help: "Files this session changed (diff, revert)", category: "session" },
  { name: "save", help: "Keep a turn in project knowledge: save [n] [mine] [--vouch]", category: "session" },
  { name: "bg", help: "Background work: `!` shell commands", category: "session" },
  { name: "tab", help: "Chat tabs: list/new/close [n]/next/prev/reopen/<n>", category: "companion", subcommands: ["list", "new", "close", "next", "prev", "reopen"] },
  { name: "venue", help: "Run this session on the server: status/start/stop/sources/approvals/questions", category: "companion", subcommands: ["status", "start", "stop", "sources", "approvals", "questions"] },
  { name: "sandbox", help: "Local tool sandbox: status/on/off/default on|off", category: "companion", subcommands: ["status", "on", "off", "default"] },
  { name: "web", help: "The agent's web access in this session", category: "companion" },
  { name: "search-server", help: "Local search server for web_search: status/up/down", category: "companion", subcommands: ["status", "up", "down"] },
  { name: "worktree", help: "Isolated git worktree: list | <name> | exit [keep|remove] | mode [off|auto]", category: "companion", subcommands: ["list", "exit", "mode"] },
  { name: "explain", help: "Explain an error code — cause + remediation from the registry", category: "endpoint" },
  { name: "palette", help: "Open the command palette", category: "ui" },
  { name: "teamup", help: "Teams: list | create <name> [roles=…] [goal] | status|logs|report|run|pause|resume|cancel|tailor <sid> | engines | crews | hardware | plans", category: "companion", subcommands: ["list", "create", "status", "logs", "report", "run", "pause", "resume", "cancel", "tailor", "engines", "crews", "hardware", "plans"] },
  { name: "work", help: "The Work pane: workflows, plan runs, teams, loops, jobs, endpoints; show|run <workflow>", category: "companion", subcommands: ["show", "run"] },
  { name: "workflows", help: "The Work pane (as /work)", category: "companion" },
  { name: "infra", help: "Infrastructure: backend readiness, endpoints, jobs", category: "companion" },
  { name: "loops", help: "Research loops: list | status|stop|pause|resume <id> | detail <id>", category: "endpoint", subcommands: ["list", "status", "stop", "pause", "resume", "detail"] },
  { name: "traces", help: "Execution traces: get <trace-id> | <job-id> | list|show|lineage|children", category: "endpoint", subcommands: ["get", "list", "show", "lineage", "children"] },
  { name: "provenance", help: "Provenance of a job: <job-id> [lineage]", category: "endpoint" },
  { name: "sweep", help: "HPC sweep templates: list | preview <name>", category: "endpoint", subcommands: ["list", "preview"] },
  { name: "models", help: "Pick the session model; /models default <name> saves the profile default", category: "endpoint" },
  { name: "mode", help: "Interaction mode — chat | plan | autopilot; write-plan on|off", category: "companion", subcommands: ["chat", "plan", "autopilot", "write-plan"] },
];

export interface ConsoleToolsDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  post(message: Record<string, unknown>): void;
  sessionId(): string;
  output(command: string, lines: OutputLine[]): void;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function toEntry(raw: unknown): CatalogEntry | null {
  const row = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const name = str(row.name);
  if (!name) return null;
  const subs = Array.isArray(row.subcommands) ? row.subcommands.map(str).filter(Boolean) : undefined;
  return { name, help: str(row.help), category: str(row.category) || "other", ...(subs?.length ? { subcommands: subs } : {}) };
}

/** The bridge's rows with the host's verbs on top (theirs win), sorted by name. */
export function mergeCatalog(bridgeRows: unknown): CatalogEntry[] {
  const byName = new Map<string, CatalogEntry>();
  for (const entry of (Array.isArray(bridgeRows) ? bridgeRows : []).map(toEntry)) if (entry) byName.set(entry.name, entry);
  for (const entry of HOST_VERBS) byName.set(entry.name, entry);
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** `/help`: commands by category; `/help <command>`: that one and its subcommands. */
export function helpLines(catalog: CatalogEntry[], args: string): OutputLine[] {
  let want = args.trim().toLowerCase();
  if (want.startsWith("/")) want = want.slice(1);
  return want ? helpOne(catalog, want) : helpAll(catalog);
}

function helpOne(catalog: CatalogEntry[], want: string): OutputLine[] {
  const entry = catalog.find((e) => e.name === want);
  if (!entry) return [{ kind: "error", text: `no command /${want} — /help lists them` }];
  const subs = entry.subcommands?.length ? `\n  subcommands: ${entry.subcommands.join(", ")}` : "";
  return [{ kind: "info", text: `/${entry.name} — ${entry.help}${subs}` }];
}

const HELP_KEYS =
  "Keys: Enter sends, Shift+Enter newline, Esc stops a turn, Esc Esc on an empty composer rewinds; editor keybindings: R4 commands in Keyboard Shortcuts.";

function helpAll(catalog: CatalogEntry[]): OutputLine[] {
  const groups = new Map<string, CatalogEntry[]>();
  for (const entry of catalog) groups.set(entry.category, [...(groups.get(entry.category) ?? []), entry]);
  const blocks = [...groups.keys()].sort().map((category) => {
    const rows = (groups.get(category) ?? []).map((e) => `  /${e.name} — ${e.help}`);
    return [`${category}:`, ...rows].join("\n");
  });
  return [{ kind: "info", text: [...blocks, HELP_KEYS].join("\n\n") }];
}

export class ConsoleTools {
  private catalog: CatalogEntry[] = [...HOST_VERBS];

  constructor(private readonly deps: ConsoleToolsDeps) {}

  /** The bridge's catalog for the popup and /help (best-effort: the host's verbs stay). */
  async refreshCatalog(): Promise<void> {
    try {
      const result = await this.deps.request("list_slash_commands", {}, 15_000);
      this.catalog = mergeCatalog((result as { commands?: unknown })?.commands);
    } catch {
      this.catalog = mergeCatalog([]);
    }
    this.deps.post({ type: "slashCatalog", commands: this.catalog.map((e) => ({ name: e.name, hint: e.help })) });
  }

  help(args: string): void {
    this.deps.output(`/help ${args}`.trim(), helpLines(this.catalog, args));
  }

  /** `/search-server [status|up|down]` (start/stop accepted too). */
  async searchServer(args: string): Promise<void> {
    const verb = args.trim().toLowerCase();
    const run = SEARCH_VERBS[verb];
    const command = `/search-server ${verb}`.trim();
    if (!run) return this.deps.output(command, [{ kind: "error", text: SEARCH_SERVER_USAGE }]);
    try {
      this.deps.output(command, [{ kind: "info", text: await run(this.deps) }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `search server: ${errText(error)}` }]);
    }
  }

  /** The registry's remediation for an error's text (`explain.match`; the
   * console's error card). Advisory: a miss or a failed lookup adds nothing. */
  async matchError(message: string, sessionId: string): Promise<void> {
    if (!message.trim()) return;
    try {
      const out = (await this.deps.request("explain.match", { message }, 8_000)) as { found?: boolean; error_id?: string; remediation?: string };
      if (!out?.found || !out.error_id || !out.remediation) return;
      this.deps.post({ type: "notice", sessionId: sessionId || this.deps.sessionId(), text: `? ${out.error_id}: ${out.remediation} (/explain ${out.error_id})` });
    } catch {
      // the match is advisory: a lookup failure never adds a second error
    }
  }

  /** A start's pushed progress and end, as notices. */
  onSearchServerEvent(event: SearchServerEvent): void {
    this.deps.post({ type: "notice", sessionId: this.deps.sessionId(), text: searchServerEventText(event ?? {}) });
  }

  /** `/worktree` (ADR-108): the session's isolated git worktree, through
   * the console's bridge verbs and texts. */
  async worktree(args: string): Promise<void> {
    const command = `/worktree ${args}`.trim();
    try {
      this.deps.output(command, [{ kind: "info", text: await worktreeVerb(this.deps, parseWorktreeArgs(args)) }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: errText(error) }]);
    }
  }

  /** `worktree_event`: say when the agent (or auto mode) moved the session. */
  onWorktreeEvent(event: Record<string, unknown>): void {
    const source = String(event?.source ?? "");
    if (!event?.ok || (source !== "agent" && source !== "auto")) return;
    const text = `${source === "auto" ? "auto " : "agent: "}${String(event.message ?? "")}`;
    this.deps.post({ type: "notice", sessionId: this.deps.sessionId(), text });
  }
}

type Reply = { ok?: boolean; message?: string };

function replyText(reply: unknown): string {
  const r = (reply ?? {}) as Reply;
  if (!r.ok) throw new Error(r.message || "worktree: refused");
  return r.message ?? "";
}

async function worktreeVerb(deps: ConsoleToolsDeps, parsed: WorktreeArgs): Promise<string> {
  if (parsed.verb === "usage") throw new Error(WORKTREE_USAGE);
  if (parsed.verb === "enter") return replyText(await deps.request("worktree.enter", { name: parsed.name, base: parsed.base }, 120_000));
  if (parsed.verb === "exit") return replyText(await deps.request("worktree.exit", { action: parsed.action, discard: parsed.discard }, 60_000));
  if (parsed.verb === "mode") {
    const reply = (await deps.request("worktree.mode", parsed.mode ? { mode: parsed.mode } : {}, 15_000)) as Reply & { mode: string; mode_source: string };
    replyText(reply);
    return worktreeModeText(reply.mode, reply.mode_source);
  }
  const listed = (await deps.request("worktree.list", {}, 15_000)) as WorktreeListResult;
  return worktreeListText(listed.worktrees, listed.current, listed.error ?? "", worktreeModeText(listed.mode ?? "off", listed.mode_source ?? "default"));
}

type SearchVerb = (deps: ConsoleToolsDeps) => Promise<string>;

const status: SearchVerb = async (deps) =>
  searchServerStatusText((await deps.request("search_server.status", {}, 15_000)) as SearchServerStatusResult);
const up: SearchVerb = async (deps) => {
  const result = (await deps.request("search_server.up", {}, 15_000)) as { accepted?: boolean };
  return result?.accepted ? SEARCH_SERVER_STARTING : SEARCH_SERVER_BUSY;
};
const down: SearchVerb = async (deps) => {
  const result = (await deps.request("search_server.down", {}, 60_000)) as { state?: string; reason?: string };
  if (result?.state !== "stopped") throw new Error(`not stopped — ${result?.reason ?? result?.state ?? "unknown"}`);
  return SEARCH_SERVER_STOPPED;
};

const SEARCH_VERBS: Record<string, SearchVerb> = { "": status, status, up, start: up, down, stop: down };
