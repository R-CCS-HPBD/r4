/**
 * Slash command catalog, filtering, and dispatch for the console slash bar.
 *
 * Catalog merge: local TS + Companion ops (list_slash_commands) + Bun-only /project.
 * Lifecycle /project … routes to typed project_* RPCs — never dispatch_slash_command.
 */

import * as opsText from "../lib/ops-text";
import { PROJECT_REGISTER_HELP, createdNote, handleProjectRegister } from "./project-register-command";
import fuzzysort from "fuzzysort";
import { actions, setWorkspace, workspace, activeTab } from "../stores";
import { readStatuslineCommand, statuslinePath, writeStatuslineCommand } from "../lib/statusline";
import { keymapConfigPath } from "../lib/keymap-config";
import { getCommands } from "./index";
import {
  applyBridgeStatePatch,
  bridgeErrorHint,
  getCompanionBridge,
  mapBridgeStateToWorkspace,
} from "../bridge";
import { COMPACTION_NOTICE_KEY } from "../bridge/companion-runtime-bridge";
import { isOfflineBackend } from "../lib/console-mode";
import { refreshAfterProjectConfigChange } from "../hooks/project-refresh";
import { buildProjectStatusLines } from "../lib/project-status";
import { buildHealthReportLines } from "../lib/health-report";
import { clearConversation } from "../lib/clear-conversation";
import { buildMcpReportLines } from "../lib/mcp-report";
import { handleSettingsRules, settingsMatrixNotice } from "../lib/settings-rules";
import { umrsWorkflowActions, type UmrsWorkflowCatalogItem } from "../stores/umrs-workflow";
import { workflowGraphCommand } from "./workflow-graph";
import { workflowExportCommand } from "./workflow-export";
import { trackActiveProjectJob, wireProjectJobEventsOnce } from "./project-job-events";
import { trackSlashInFlight } from "../lib/slash-in-flight";
import { rpc } from "../bridge/rpc";
import { isChatPrompt } from "../lib/tui-parity";
import {
  aliasCanon,
  applyManifestOwnership,
  companionRowIgnored,
  isWaveIntercepted,
  manifestVerbs,
  stageRowName,
} from "./catalog-manifest";

/** One subcommand row offered by a parent command (hint + dispatch). */
export interface SlashSubcommandEntry {
  name: string;
  help: string;
  takesArgs?: boolean;
}

export interface SlashCommandEntry {
  name: string;
  help: string;
  category: string;
  takesArgs: boolean;
  aliases?: string[];
  /** D2: keep out of the ctrl+p palette unless the user reveals with `!query`. */
  hidden?: boolean;
  /** Local handler — when set, dispatch stays in-process (no Python bridge). */
  localRun?: () => void | Promise<void>;
  /**
   * localRun serves only the bare command; with args the line goes to the
   * companion command of the same name (TUI audit 2026-10-17: overlay, pane
   * and picker rows swallowed `/skills on x`, `/research stop L1`, …).
   * Set from catalog.manifest.json (owner bare-local-args-forward), as are
   * the rows' aliases (plan H-5).
   */
  bareOnly?: boolean;
  /** The bare form is a useful default (list, show current): one Enter runs
   * it instead of seeding a `/name ` draft (TUI audit 2026-10-17). */
  argsOptional?: boolean;
  /** Bun-only: route to project_* RPC instead of companion slash. */
  projectRpc?: string;
  unavailable?: boolean;
  subcommands?: string[];
  /**
   * Usage tokens shown by the slash bar hint line while the user types args
   * (e.g. "[open|off|extend <graph>]"). Without it a takesArgs command is a
   * blank wall once its row is selected — first surfaced via /acquisition.
   */
  argsHint?: string;
  /**
   * Concrete subcommand rows: when the typed first token exactly names this
   * command (or an alias), the slash bar lists these instead of fuzzy rows
   * (same grammar as the hardcoded /project and /endpoint tables).
   */
  subs?: SlashSubcommandEntry[];
}

export type SlashDispatchResult =
  | { kind: "handled" }
  | { kind: "insert"; text: string }
  | { kind: "error"; message: string };

/** Bun-only project subcommands — do NOT add these to Python SUBCOMMANDS["project"]. */
export const PROJECT_SUBCOMMANDS: Array<{
  name: string;
  help: string;
  takesArgs: boolean;
  rpc: string;
  unavailable?: boolean;
  phase: "c1" | "c2";
}> = [
  { name: "list", help: "List projects", takesArgs: false, rpc: "project_list", phase: "c1" },
  { name: "current", help: "Show current project", takesArgs: false, rpc: "project_current", phase: "c1" },
  { name: "create", help: "Create and select a project: /project create <id> [--name …]", takesArgs: true, rpc: "project_create", phase: "c1" },
  { name: "register", help: PROJECT_REGISTER_HELP, takesArgs: true, rpc: "project_register", phase: "c1" },
  { name: "switch", help: "Switch active project: /project switch <id>", takesArgs: true, rpc: "project_switch", phase: "c1" },
  { name: "delete", help: "Delete a project: /project delete <id>", takesArgs: true, rpc: "project_delete", phase: "c1" },
  { name: "import", help: "Import sources: /project import <id> <paths…>", takesArgs: true, rpc: "project_import", phase: "c2" },
  { name: "init", help: "Initialize knowledge graph: /project init <id>", takesArgs: true, rpc: "project_init", phase: "c2" },
  { name: "verify", help: "Verify knowledge: /project verify <id> <question>", takesArgs: true, rpc: "project_verify", phase: "c2" },
  { name: "readiness", help: "Readiness report: /project readiness <id>", takesArgs: true, rpc: "project_readiness", phase: "c2" },
  { name: "plan", help: "Generate research plan: /project plan <id> <goal>", takesArgs: true, rpc: "project_plan", phase: "c2" },
  {
    name: "run",
    help: "Start the UMRS research loop: /project run <id> [--approve] [--max-iterations N]",
    takesArgs: true,
    rpc: "project_run",
    phase: "c2",
  },
  { name: "cancel", help: "Cancel a lifecycle job: /project cancel <job-id>", takesArgs: true, rpc: "project_cancel_job", phase: "c2" },
  {
    name: "status",
    help: "Project status summary (current project + ingested files / plan)",
    takesArgs: true,
    rpc: "project_status",
    phase: "c2",
  },
  {
    name: "findings",
    help: "Verification findings: /project findings <id>",
    takesArgs: true,
    rpc: "project_findings",
    phase: "c2",
  },
  {
    name: "ingest",
    help: "Ingest into named graph: /project ingest <id> --graph <name> [--url U…] [--file F…]",
    takesArgs: true,
    rpc: "project_ingest",
    phase: "c2",
  },
  {
    name: "session",
    help: "Create research session: /project session <id> <goal> [--title T]",
    takesArgs: true,
    rpc: "project_session_create",
    phase: "c2",
  },
];

/** Companion /endpoint subcommands — slash-bar autocomplete only. */
export const ENDPOINT_SUBCOMMANDS: Array<{
  name: string;
  help: string;
  takesArgs: boolean;
}> = [
  { name: "list", help: "List active (ready/degraded) endpoints", takesArgs: false },
  { name: "up", help: "Provision an endpoint", takesArgs: true },
  { name: "down", help: "Tear down an endpoint", takesArgs: true },
  { name: "status", help: "Show endpoint status", takesArgs: true },
  { name: "help", help: "Show endpoint help", takesArgs: false },
];

/** T5 (2026-09-19): teamup verbs as their own family — same grammar the
 * endpoint family uses (visible only when rows exist; never a deck-pair). */
export const TEAMUP_SUBCOMMANDS: Array<{
  name: string;
  help: string;
  takesArgs: boolean;
}> = [
  { name: "create", help: "Create a session: /teamup create <name> [roles=pm,se,pg] [goal text..]", takesArgs: true },
  { name: "list", help: "Today sessions + statuses", takesArgs: false },
  { name: "status", help: "Status of one session (latest by default)", takesArgs: true },
  { name: "run", help: "1-3 iterations, manual + bounded (not an autonomous loop — /loops for that)", takesArgs: true },
  { name: "pause", help: "Pause a running session", takesArgs: true },
  { name: "resume", help: "Resume a paused session", takesArgs: true },
  { name: "cancel", help: "Cancel the session", takesArgs: true },
  { name: "logs", help: "Recent log entries (latest by default)", takesArgs: true },
  { name: "report", help: "Full session report (latest by default)", takesArgs: true },
  { name: "hardware", help: "Available hardware profiles", takesArgs: false },
  { name: "engines", help: "Installed team engines (ADR-020 engine SPI)", takesArgs: false },
  { name: "crews", help: "Research-writing crew packs (preset compositional teams)", takesArgs: false },
];

/** Companion /code workbench verbs (Python SUBCOMMANDS["code"]) — listed so
 * the slash bar offers them next to the v3 on|off toggle. edit/review/fix
 * hand their prompt back as a chat turn (sendFollowUpTurns). */
const CODE_WORKBENCH_SUBS: SlashSubcommandEntry[] = [
  { name: "status", help: "Detect languages, build systems and tools" },
  { name: "lsp", help: "Inspect or start language-server backends" },
  { name: "plan", help: "Show suggested compile/run/test commands" },
  { name: "diff", help: "Show working-tree diff (stat + patch)" },
  { name: "explain", help: "Open a file with lines in the pane", takesArgs: true },
  { name: "compile", help: "Compile or syntax-check code" },
  { name: "run", help: "Run a source file or executable", takesArgs: true },
  { name: "test", help: "Run detected tests" },
  { name: "edit", help: "Agentic edit of a path (runs as a chat turn)", takesArgs: true },
  { name: "review", help: "Agentic code review (runs as a chat turn)", takesArgs: true },
  { name: "fix", help: "Agentic diagnose-and-fix loop (runs as a chat turn)", takesArgs: true },
];

export function localSlashCommands(): SlashCommandEntry[] {
  const fromRegistry: SlashCommandEntry[] = getCommands()
    .filter((cmd) => cmd.slashName)
    .map((cmd) => ({
      name: cmd.slashName!,
      help: cmd.slashDescription ?? cmd.title,
      category: cmd.namespace,
      takesArgs: Boolean(cmd.slashTakesArgs),
      localRun: cmd.run,
    }));

  const builtins: SlashCommandEntry[] = [
    {
      name: "models",
      help: "Pick session model (interactive picker); /models default <name> saves the profile default",
      category: "endpoint",
      takesArgs: false,
      localRun: () => {
        /* handled by resolveSlashPicker in dispatchSlashFromChat */
        void actions.dispatchSlashFromChat("/models");
      },
    },
    {
      // ADR-076: the decision model (Bespoke-Nimble-9B, always on at rccs)
      // as an opt-in agent tool. Local row so it is listed even when the
      // companion catalog failed to load; behaviour lives in the Python
      // registry (/decision-model there), reached via dispatch_slash_command.
      name: "decision-model",
      help: "Decision model for fast typed judgments (profile): on [model] | off | status",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[on [model]|off|status]",
      subcommands: ["on", "off", "status"],
      subs: [
        { name: "on", help: "Give the agent decision_evaluate (default Bespoke-Nimble-9B; or name a model)" },
        { name: "off", help: "Stop using the decision model" },
        { name: "status", help: "Show the setting and whether the platform serves the model" },
      ],
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      // Metadata row — no localRun: /endpoint dispatches stay on the companion
      // bridge (provisioning is server state). The row exists so the command
      // is VISIBLE (palette, /tour, autocomplete) instead of a blind verb.
      name: "endpoint",
      help: "Endpoint lifecycle — up/down a serving endpoint (companion)",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<up|down|status|list>",
      subcommands: ENDPOINT_SUBCOMMANDS.map((s) => s.name),
      subs: ENDPOINT_SUBCOMMANDS,
    },
    {
      name: "theme",
      help: "Color theme — picker, or /theme <name> (aurora, nord, dracula…)",
      category: "ui",
      takesArgs: true,
      argsHint: "[name]",
      // Declared so needsArgPrompt returns false on the bare form — Enter
      // dispatches immediately (opens the picker) instead of leaving
      // "/theme " in the draft for a second Enter (the 2-Enter report).
      subcommands: THEME_SLASH_SUBCOMMANDS.map((s) => s.name),
      subs: THEME_SLASH_SUBCOMMANDS,
      localRun: () => {
        /* bare /theme hits resolveSlashPicker first; args hit handleThemeCommand */
        void actions.dispatchSlashFromChat("/theme");
      },
    },
    {
      name: "teamup",
      help: "Multi-agent team session control (/team aliases + Work pane teams)",
      category: "ui",
      takesArgs: true,
      argsHint: "[list|status|run|pause|resume|cancel|logs|report|hardware]",
      subcommands: TEAMUP_SUBCOMMANDS.map((s) => s.name),
      subs: TEAMUP_SUBCOMMANDS,
      localRun: () => {
        // Bare /teamup shows today's statuses; subs reach named verbs.
        void actions.dispatchSlashFromChat("/teamup list");
      },
    },
    {
      name: "btw",
      help: "Side question about this conversation — no tools, not added to history (works mid-turn)",
      category: "session",
      takesArgs: true,
      argsHint: "<question>",
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "bg",
      help: "Background work: running/finished `!` shell commands and backgrounded turns (Ctrl+B)",
      category: "session",
      takesArgs: false,
      localRun: () => {
        const tasks = workspace.backgroundTasks;
        if (tasks.length === 0) {
          actions.sendSystemNotice("no background work (Ctrl+B backgrounds a running turn; `!cmd` runs in the background)");
          return;
        }
        const now = Date.now();
        const lines = [...tasks].reverse().map((t) => {
          const secs = Math.round(((t.endedAt ?? now) - t.startedAt) / 1000);
          return `${t.status.padEnd(9)} ${t.kind.padEnd(5)} ${secs}s  ${t.label}${t.detail ? `  — ${t.detail}` : ""}`;
        });
        actions.sendSystemNotice(`background work (${tasks.length}):\n${lines.join("\n")}`);
      },
    },
    {
      name: "statusline",
      help: "Show a shell command's output in the status bar — /statusline <cmd> · /statusline off",
      category: "ui",
      takesArgs: true,
      argsHint: "[<command>|off]",
      argsOptional: true,
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "keybindings",
      help: "Where key overrides live (v3-keymap.json) and how to write them",
      category: "ui",
      takesArgs: false,
      localRun: () => {
        actions.sendSystemNotice(
          `key overrides: ${keymapConfigPath()}\n` +
            'format: { "<command.id>": "<key>" } — adds a key, never removes defaults ' +
            '(e.g. { "turn.background": "ctrl+shift+b" }); F12 lists every live binding',
        );
      },
    },
    {
      name: "rewind",
      help: "Roll the conversation back to an earlier prompt (also Esc Esc on an empty prompt)",
      category: "session",
      takesArgs: false,
      localRun: () => {
        actions.openRewindMenu();
      },
    },
    {
      name: "fork",
      help: "Fork this session into a new one (copy of the conversation so far)",
      category: "session",
      takesArgs: false,
      localRun: () => {
        const sid = activeTab().sessionId;
        if (!sid) {
          actions.sendSystemNotice("fork: no active session to fork.");
          return;
        }
        void rpc(getCompanionBridge(), "session.fork", { session_id: sid }, 30_000)
          .then((forked) => {
            actions.sendSystemNotice(
              `forked session → ${forked.session_id ?? "?"} ("${forked.name ?? ""}")`,
            );
          })
          .catch((err: unknown) => {
            actions.sendSystemNotice(`fork failed: ${err instanceof Error ? err.message : String(err)}`);
          });
      },
    },
    {
      name: "clear",
      help: "Clear the chat and reset the conversation the model sees",
      category: "ui",
      takesArgs: false,
      localRun: () => clearConversation(),
    },
    {
      name: "health",
      help: "Check platform health (gateway / connection summary)",
      category: "endpoint",
      takesArgs: false,
      localRun: async () => {
        const lines = await buildHealthReportLines();
        actions.sendSystemNotice(lines.join("\n"));
      },
    },
    {
      name: "admin",
      help: "Owner-scope operator rows — usage by sub (rag-stats / corpus-attrition)",
      category: "endpoint",
      takesArgs: true,
      argsHint: "rag-stats [project] | corpus-attrition <project>",
      localRun: async () => { /* dispatch via owned verbs */ },
    },
    {
      name: "rag",
      help: "RAG semantic corpus query (caller-scoped LLM-side retrieval)",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<question> [top_k=<n>] [project=<id>]",
      localRun: async () => { /* dispatch via owned verbs */ },
    },
    {
      name: "traces",
      help: "Execution traces — `/traces <job-id>` list · `/traces get <trace-id>`",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[get <trace-id>|<job-id>]",
      localRun: async () => { /* dispatch via dispatchBuiltinVerbs-family */ },
    },
    {
      name: "checkpoint",
      help: "Manual checkpoint: save (named snapshot of a session) / list for this session",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[save | list]",
      argsOptional: true,
      localRun: async () => { /* dispatch via owned verbs */ },
    },
    {
      name: "provenance",
      help: "Provenance record + lineage for a job — `/provenance <job-id> [lineage]`",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<job-id> [lineage]",
      localRun: async () => { /* dispatch via dispatchBuiltinVerbs-family */ },
    },
    {
      name: "loops",
      help: "Research loops — list / status / detail (recent iterations) / stop|pause|resume control (caller-scoped)",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[list|status <id>|detail <id>|stop <id>|pause <id>|resume <id>]",
      argsOptional: true,
      localRun: async () => {
        // verb-shaped dispatch happens in dispatchBuiltinVerbs (needs parsed args).
      },
    },
    {
      name: "grants",
      help: "Live grants the caller received (read-only inventory)",
      category: "endpoint",
      takesArgs: false,
      localRun: async () => {
        if (isOfflineBackend()) {
          actions.sendSystemNotice("grants: requires a live backend");
          return;
        }
        const bridge = getCompanionBridge();
        try {
          const payload = (await bridge.request("grants.mine", {}, 15_000)) as {
            grants?: Array<Record<string, unknown>>;
            count?: number;
          };
          const rows = Array.isArray(payload.grants) ? payload.grants : [];
          if (rows.length === 0) {
            actions.sendSystemNotice("grants — none received by the caller");
            return;
          }
          const lines = rows.map(grantRowLine);
          actions.sendSystemNotice(`grants (${rows.length}):\n${lines.join("\n")}`);
        } catch (err) {
          actions.sendSystemNotice(
            `grants: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      },
    },
    {
      name: "usage",
      help: "Owner-scoped LLM token usage rows — grouped by model by default",
      category: "endpoint",
      takesArgs: false,
      localRun: async () => {
        if (isOfflineBackend()) {
          actions.sendSystemNotice("usage: requires a live backend");
          return;
        }
        const bridge = getCompanionBridge();
        try {
          const payload = (await bridge.request("usage.me", {}, 15_000)) as {
            user_id?: string;
            group_by?: string;
            rows?: Array<Record<string, unknown>>;
          };
          const rows = Array.isArray(payload.rows) ? payload.rows : [];
          if (rows.length === 0) {
            actions.sendSystemNotice(
              `usage — ${payload.user_id ?? "caller"}: no rows in range`,
            );
            return;
          }
          const lines = rows.map(usageRowLine);
          actions.sendSystemNotice(
            `usage — ${payload.user_id ?? "caller"} (${payload.group_by ?? "model"}):\n${lines.join("\n")}`,
          );
        } catch (err) {
          actions.sendSystemNotice(
            `usage: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      },
    },
    {
      name: "circles",
      help: "Circles + rosters the caller may see — `/circles [project]` · `/circles <project> <id>` members · `enter`/`create` · `add`/`remove <project> <id> <user>`",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[project] [circle] | <enter|create|add|remove|members> …",
      argsOptional: true,
      localRun: async () => {
        // verb-shaped dispatch happens in dispatchBuiltinVerbs (needs parsed args).
      },
    },
    {
      name: "files",
      help: "Gateway file transfer — `/files upload <path> [key]` · `/files download <key> <dest>`",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<upload <path> [key] | download <key> <dest>>",
      localRun: async () => { /* dispatch via dispatchBuiltinVerbs */ },
    },
    {
      name: "explain",
      help: "Explain an error code — cause + remediation from the registry",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<error.id>",
      localRun: async () => { /* dispatch via dispatchBuiltinVerbs */ },
    },
    {
      name: "sweep",
      help: "HPC sweep templates — `/sweep list` inventory · `/sweep preview <name>` dry render",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[list|preview <name>]",
      argsOptional: true,
      localRun: async () => { /* dispatch via dispatchBuiltinVerbs */ },
    },
    {
      name: "evidence",
      help: "Evidence plane — caller-scoped claims/retractions rows, optional status filter",
      category: "endpoint",
      takesArgs: true,
      argsHint: "[status]",
      argsOptional: true,
      localRun: async () => { /* dispatch via dispatchBuiltinVerbs */ },
    },
    {
      name: "quota",
      help: "Owner-scoped resource poles (CPU + disk bytes) — read-only gauge",
      category: "endpoint",
      takesArgs: false,
      localRun: async () => {
        if (isOfflineBackend()) {
          actions.sendSystemNotice("quota: requires a live backend");
          return;
        }
        const bridge = getCompanionBridge();
        try {
          const payload = (await bridge.request("quota.units", {}, 15_000)) as {
            user_id?: string;
            cpu_process?: { current?: number; limit?: number };
            disk_bytes?: { current?: number; limit?: number };
          };
          const cpu = payload.cpu_process ?? {};
          const disk = payload.disk_bytes ?? {};
          actions.sendSystemNotice(
            [
              `quota — ${payload.user_id ?? "caller"}`,
              `${gaugeGlyph(cpu)}cpu process  · ${cpu.current ?? "?"}/${cpu.limit ?? "?"}`,
              `${gaugeGlyph(disk)}disk bytes   · ${disk.current ?? "?"}/${disk.limit ?? "?"}`,
            ].join("\n"),
          );
        } catch (err) {
          actions.sendSystemNotice(
            `quota: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      },
    },
    {
      name: "mcp",
      help: "MCP servers — selectable overlay (inventory)",
      category: "endpoint",
      takesArgs: false,
      localRun: async () => {
        if (isOfflineBackend()) {
          actions.sendSystemNotice("MCP: offline/demo backend — no gateway inventory");
          return;
        }
        actions.setMcpStatus("ok");
        actions.openListOverlay("mcp");
      },
    },
    {
      name: "changes",
      help: "Session file changes — files edited this session (Enter for side-by-side diff)",
      category: "companion",
      takesArgs: false,
      localRun: () => {
        actions.openListOverlay("changes");
      },
    },
    {
      name: "governance",
      help: "UMRS governance — ownership / quotas / audit trail overlay",
      category: "endpoint",
      takesArgs: false,
      localRun: async () => {
        if (isOfflineBackend()) {
          actions.sendSystemNotice("governance: offline/demo backend — no gateway inventory");
          return;
        }
        actions.openListOverlay("governance");
      },
    },
    {
      name: "job",
      help: "Job attempt timeline — /job <id> overlays attempt state history; list|describe|cancel|retry run on the companion",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<job-id>|list|describe|cancel|retry <id>",
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "source",
      help: "Active-source switcher — gateway/source inventory, Enter to switch",
      category: "endpoint",
      takesArgs: false,
      localRun: () => {
        actions.openListOverlay("sources");
      },
    },
    {
      name: "connect",
      help: "Connection menu — auth login status, JWT login, add public endpoints",
      category: "endpoint",
      takesArgs: false,
      localRun: () => {
        actions.openListOverlay("connect");
      },
    },
    {
      name: "login",
      help: "Authenticate with JWT / onboarding token: /login <token>",
      category: "endpoint",
      takesArgs: true,
      argsHint: "<JWT-or-onboarding-token>",
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "acquisition",
      help: "Knowledge write mode (session): open|off|extend <graph>",
      category: "companion",
      takesArgs: true,
      argsHint: "[open|off|extend <graph>[,<g2>…]]",
      subcommands: ["open", "off", "extend"],
      subs: [
        { name: "open", help: "Allow knowledge writes (approvals still gate each write)" },
        { name: "off", help: "Forbid all knowledge writes this session" },
        {
          name: "extend",
          help: "Extend ONLY the named pre-existing graph(s): extend <graph>[,<g2>…]",
          takesArgs: true,
        },
      ],
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "skills",
      help: "Skills — selectable overlay (detail with Enter)",
      category: "companion",
      takesArgs: false,
      localRun: () => {
        actions.openListOverlay("skills");
      },
    },
    {
      name: "mode",
      help: "Interaction mode — chat | plan | autopilot (live companion only)",
      category: "companion",
      takesArgs: false,
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "code",
      help: "Coding mode — toggle the builtin agentic code skill (on|off); other verbs run the companion workbench",
      category: "companion",
      takesArgs: true,
      argsHint: "[on|off|status|diff|compile|run|test|…]",
      subcommands: ["on", "off", ...CODE_WORKBENCH_SUBS.map((s) => s.name)],
      subs: [
        { name: "on", help: "Enable coding mode (read/edit/run files with gates)" },
        { name: "off", help: "Disable coding mode" },
        ...CODE_WORKBENCH_SUBS,
      ],
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    // Wave D/E/F/H row registration — the dispatch-level intercepts above own
    // these; registering them is what makes them VISIBLE in the palette
    // (trap: intercepts without a row = invisible commands; /mode was first).
    {
      name: "sessions",
      help: "Sessions — browse/resume/rename/pin/archive/export (Tab into pane)",
      category: "companion",
      takesArgs: true,
      argsHint: "[new|resume|rename|pin|unpin|archive|export [<id>]]",
      subcommands: ["new", "resume", "rename", "pin", "unpin", "archive", "export"],
      subs: [
        { name: "new", help: "Start a new session (venue picker: local/frontend)" },
        { name: "resume", help: "Resume a session in chat: sessions resume <id>", takesArgs: true },
        { name: "rename", help: "Rename a session row: sessions rename <id> <new name>", takesArgs: true },
        { name: "pin", help: "Pin a session to the top of the list: sessions pin <id>", takesArgs: true },
        { name: "unpin", help: "Remove the pin: sessions unpin <id>", takesArgs: true },
        { name: "archive", help: "Archive a session (recoverable): sessions archive <id>", takesArgs: true },
        { name: "export", help: "Export a session transcript to markdown: sessions export <id>", takesArgs: true },
      ],
      localRun: async () => {
        /* handled in dispatch (bare → pane + fetch) */
      },
    },
    // Chat tabs (2026-09-29): parallel session strip (opencode-v2 parity).
    {
      name: "tab",
      help: "Chat tabs — parallel sessions, one session each (list/new/close/next/prev/reopen/<n>)",
      category: "companion",
      takesArgs: true,
      argsHint: "[list|new|close [n]|next|prev|reopen|<n>]",
      subcommands: ["list", "new", "close", "next", "prev", "reopen"],
      subs: [
        { name: "list", help: "List open chat tabs (bare /tab does the same)" },
        { name: "new", help: "New tab — fresh companion session (Ctrl+N)" },
        { name: "close", help: "Close a tab: tab close [n] (Ctrl+Alt+W closes the active one)" },
        { name: "next", help: "Activate the next tab (Alt+→)" },
        { name: "prev", help: "Activate the previous tab (Alt+←)" },
        { name: "reopen", help: "Reopen the last closed tab — session resumes (Ctrl+Shift+T)" },
      ],
      localRun: async () => {
        /* handled in dispatch (args: /tab <verb>) */
      },
    },
    // Wave 2026-09-21 (ADR-046 A2): remote (frontend-venue) session runtime.
    {
      name: "venue",
      help: "Remote session runtime — status / start / stop / sources / approvals / questions",
      category: "companion",
      takesArgs: true,
      argsHint: "[status|start|stop|sources|approvals|questions]",
      subcommands: ["status", "start", "stop", "sources", "approvals", "questions"],
      subs: [
        { name: "status", help: "Show the remote runtime state + lease for this session" },
        { name: "start", help: "Run this session's turns on the server (pick a pushed source)" },
        { name: "stop", help: "Tear the remote runtime down (lease revoked; local resume stays)" },
        { name: "sources", help: "Push / unpush sealed sources (the key never reaches the sandbox)" },
        { name: "approvals", help: "Answer tool approvals the remote worker is waiting on" },
        { name: "questions", help: "Answer the remote worker's questions (set-aside ones too)" },
      ],
      localRun: async () => {
        /* handled in dispatch */
      },
    },
    // ADR-099 D-2 (M-4): the agent's web access for this session — asked
    // when the session opens; /web shows and changes it (a running remote
    // session must be restarted to change it).
    {
      name: "web",
      help: "Web access — the agent's web access for this session (off / allowlist / public)",
      category: "companion",
      takesArgs: false,
      localRun: async () => {
        actions.openWebAccessPrompt({ purpose: "slash", remote: activeTab().sessionVenue === "frontend" });
      },
    },
    // ADR-101: the local tool sandbox — off by default; the session-start
    // notice says so and points here.
    {
      name: "sandbox",
      help: "Sandbox — confine the agent's shell, Python and local MCP servers (off by default)",
      category: "companion",
      takesArgs: true,
      argsHint: "[status|on|off|default on|default off]",
      subcommands: ["status", "on", "off", "default"],
      subs: [
        { name: "status", help: "Show mode, backend, write roots, network and temp dir" },
        { name: "on", help: "Turn the sandbox on for this session (from the agent's next command)" },
        { name: "off", help: "Turn the sandbox off for this session" },
        { name: "default", help: "default on|off — also store it as the profile default", takesArgs: true },
      ],
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    // ADR-102 (2026-11-01): the local SearXNG behind web_search, started
    // from here or from the offer after /web Public.
    {
      name: "search-server",
      help: "Search server — a local SearXNG so the agent's web_search works (podman or docker)",
      category: "companion",
      takesArgs: true,
      argsHint: "[status|up|down]",
      subcommands: ["status", "up", "down"],
      subs: [
        { name: "status", help: "Show whether the local search server runs, and where" },
        { name: "up", help: "Start it (the first start downloads about 200 MB); progress follows" },
        { name: "down", help: "Stop and remove it (its settings are kept)" },
      ],
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    // ADR-108 D-2 (2026-11-03): isolated git worktrees, modeled on Claude Code.
    {
      name: "worktree",
      help: "Worktree — work in an isolated git worktree of this repository (branch r4/<name>)",
      category: "companion",
      takesArgs: true,
      argsHint: "[list|<name>|exit [keep|remove]|mode [off|auto]]",
      subcommands: ["list", "exit", "mode"],
      subs: [
        { name: "list", help: "The repository's worktrees, their parents and changes" },
        { name: "exit", help: "Leave the worktree: keep it, or remove (a changed one needs --discard)" },
        { name: "mode", help: "off | auto — in auto the agent moves into a worktree before its first write" },
      ],
      localRun: async () => {
        /* handled in dispatch (needs parsed args) */
      },
    },
    {
      name: "catalog",
      help: "Catalog — unified asset kinds (skills/MCP code changes sessions)",
      category: "companion",
      takesArgs: false,
      localRun: async () => {
        actions.openPane("catalog");
      },
    },
    {
      name: "codeview",
      help: "Code view — git change sets, diffs, guarded revert, tests",
      category: "companion",
      takesArgs: false,
      localRun: async () => {
        const { listChanges } = await import("../lib/git-changes");
        actions.openPane("codeview");
        actions.setChangeSets(listChanges() ?? []);
      },
    },
    // Terminal pane (2026-10-03): interactive PTY shell. Alias /terminal —
    // the verb family is dispatched in wave-intercepts (bare/new/kill).
    {
      name: "term",
      help: "Terminal pane — interactive shell docked right of chat (click to focus)",
      category: "ui",
      takesArgs: true,
      argsHint: "[new|kill|share [lines]]",
      subcommands: ["new", "kill", "share"],
      subs: [
        { name: "new", help: "Restart the shell fresh (buffer discarded)" },
        { name: "kill", help: "Kill the shell and close the pane" },
        { name: "share", help: "Relay the last N buffer lines (default 40) to the agent as a turn", takesArgs: true },
      ],
      localRun: async () => {
        /* handled in dispatch */
      },
    },
    // V4 pane-open commands — summon the detail pane on a view. overview
    // and research are also companion commands: their args form goes there.
    ...( [
      ["overview", "dashboard"],
      ["research", "research"],
      ["tasks", "tasks"],
      ["work", "work"],
      // Keyboard-muscle alias — the pane is the unified Work view (P2).
      ["workflows", "work"],
      ["knowledge", "knowledge"],
      ["infra", "infrastructure"],
      ["settings", "settings"],
      ["loop", "loop"],
      ["logs", "logs"],
    ] as const ).map(([name, view]) => ({
      name,
      // /loop is the research-loop verbs when live (dispatchManagedVerbs);
      // the picker said only "Open detail pane → loop" (2026-11-03).
      help: name === "loop" ? opsText.LOOP_PICKER_HELP : `Open detail pane → ${view}`,
      category: "ui" as const,
      takesArgs: false,
      localRun: () => {
        actions.openPane(view);
      },
    })),
    {
      name: "tour",
      help: "Guided project walkthrough (· map = full function index)",
      category: "ui",
      takesArgs: true,
      argsHint: "[map|n]",
      // Bare-Enter must dispatch (wizard opens) — subcommands decl bypasses
      // needsArgPrompt's draft-fill (same grammar as /theme).
      subcommands: ["map"],
      subs: [{ name: "map", help: "Print the full r4/umrs function index" }],
      localRun: () => {
        /* args route through handleTourCommand (wizard/map/jump) */
        void actions.dispatchSlashFromChat("/tour");
      },
    },
    {
      name: "palette",
      help: "Open the command palette",
      category: "ui",
      takesArgs: false,
      localRun: () => actions.openCommandPalette(),
    },
    {
      name: "quit",
      help: "Quit the console (press Ctrl+Q again to confirm)",
      category: "ui",
      takesArgs: false,
      hidden: true,
      localRun: () => actions.requestQuit(),
    },
    {
      name: "project",
      help: "Project lifecycle (Bun): list|current|create|switch|import|init|…",
      category: "project",
      takesArgs: true,
      projectRpc: "project",
      subcommands: PROJECT_SUBCOMMANDS.map((s) => s.name),
      argsHint: `<${PROJECT_SUBCOMMANDS.map((s) => s.name).join("|")}>`,
    },
  ];

  const merged = new Map<string, SlashCommandEntry>();
  for (const entry of [...builtins, ...fromRegistry]) {
    merged.set(entry.name, entry);
  }
  return applyManifestOwnership([...merged.values()]).sort((a, b) => a.name.localeCompare(b.name));
}

let catalogCache: SlashCommandEntry[] | null = null;
let catalogPromise: Promise<SlashCommandEntry[]> | null = null;

function mergeLocalEntry(merged: Map<string, SlashCommandEntry>, entry: SlashCommandEntry): void {
  const existing = merged.get(entry.name);
  merged.set(entry.name, {
    ...existing,
    ...entry,
    localRun: entry.localRun ?? existing?.localRun,
    projectRpc: entry.projectRpc ?? existing?.projectRpc,
  });
}

function mergeCatalog(local: SlashCommandEntry[], remote: SlashCommandEntry[]): SlashCommandEntry[] {
  const merged = new Map<string, SlashCommandEntry>();
  for (const entry of remote) {
    // Rows v3 owns wholesale (manifest companionRow "ignore": /project)
    // keep the companion's stub metadata out of the way.
    if (companionRowIgnored(entry.name)) continue;
    merged.set(entry.name, entry);
  }
  for (const entry of local) {
    mergeLocalEntry(merged, entry);
  }
  return [...merged.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export async function loadSlashCatalog(force = false): Promise<SlashCommandEntry[]> {
  if (!force && catalogCache) return catalogCache;
  if (!force && catalogPromise) return catalogPromise;

  catalogPromise = (async () => {
    const local = localSlashCommands();
    if (isOfflineBackend()) {
      catalogCache = local;
      return catalogCache;
    }
    try {
      const bridge = getCompanionBridge();
      const result = (await bridge.request("list_slash_commands", {}, 20_000)) as {
        commands?: SlashCommandEntry[];
      };
      const remote = (result.commands ?? []) as SlashCommandEntry[];
      catalogCache = mergeCatalog(local, remote);
    } catch (err) {
      catalogCache = local;
      // Header/status only — do not dump into chat history.
      const detail =
        err instanceof Error ? err.message.slice(0, 80) : "companion bridge down";
      actions.setChatFallback(true, detail);
    }
    return catalogCache!;
  })();

  return catalogPromise;
}

function projectSubSlashEntries(subQ: string): SlashCommandEntry[] {
  return PROJECT_SUBCOMMANDS.filter(
    (s) => !subQ || s.name.startsWith(subQ.toLowerCase()),
  ).map(
    (s): SlashCommandEntry => ({
      name: `project ${s.name}`,
      help: s.unavailable ? `${s.help} [unavailable]` : s.help,
      category: "project",
      takesArgs: s.takesArgs,
      projectRpc: s.rpc,
      unavailable: s.unavailable,
    }),
  );
}

function endpointSubSlashEntries(subQ: string): SlashCommandEntry[] {
  return ENDPOINT_SUBCOMMANDS.filter(
    (s) => !subQ || s.name.startsWith(subQ.toLowerCase()),
  ).map(
    (s): SlashCommandEntry => ({
      name: `endpoint ${s.name}`,
      help: s.help,
      category: "endpoint",
      takesArgs: s.takesArgs,
    }),
  );
}

function fuzzyFilterCatalog(
  catalog: SlashCommandEntry[],
  q: string,
  parts: string[],
  first: string,
): SlashCommandEntry[] {
  // While typing `/endpoint up`, hide unrelated aliases like `/up`.
  const keepEntry = (entry: SlashCommandEntry): boolean =>
    parts.length <= 1 || entry.name.includes(" ") || first === entry.name;

  return fuzzysort
    .go(q, catalog, { keys: ["name", "help", "category", (c) => (c.aliases ?? []).join(" ")] })
    .map((r) => r.obj)
    .filter(keepEntry);
}

/** Exact first-token match against names/aliases (sub-row hint lookup). */
export function exactCatalogEntry(
  catalog: SlashCommandEntry[],
  token: string,
): SlashCommandEntry | undefined {
  const needle = token.toLowerCase();
  return catalog.find(
    (e) =>
      e.name.toLowerCase() === needle ||
      (e.aliases ?? []).some((a) => a.toLowerCase() === needle),
  );
}

export function filterSlashCommands(catalog: SlashCommandEntry[], query: string): SlashCommandEntry[] {
  const q = query.trim().replace(/^\//, "");
  if (!q) return catalog;

  const parts = q.split(/\s+/);
  const first = parts[0]!.toLowerCase();
  const subQ = parts[1] ?? "";

  if (first === "project" && (parts.length === 1 || subQ)) {
    return projectSubSlashEntries(subQ);
  }

  if (first === "endpoint" && (parts.length === 1 || subQ)) {
    return endpointSubSlashEntries(subQ);
  }

  // Generalized sub rows (D-next): any entry carrying `subs` produces hint
  // rows the same way /project and /endpoint always did — /acquisition is
  // the first consumer beyond the two hardcoded parents.
  if (parts.length === 1 || subQ) {
    const parent = exactCatalogEntry(catalog, first);
    if (parent?.subs?.length) {
      return parent.subs
        .filter((s) => !subQ || s.name.startsWith(subQ.toLowerCase()))
        .map(
          (s): SlashCommandEntry => ({
            name: `${parent.name} ${s.name}`,
            help: s.help,
            category: parent.category,
            takesArgs: Boolean(s.takesArgs),
          }),
        );
    }
  }

  return fuzzyFilterCatalog(catalog, q, parts, first);
}

/**
 * Usage-hint line shown by the slash bar while a takesArgs command has its
 * args typed (or right after its name is completed). Returns null while the
 * user is still fuzzy-searching commands — suggestion rows guide that phase.
 */
export function argHintLineForInput(
  catalog: SlashCommandEntry[],
  rawLine: string,
  suggestionCount: number,
): string | null {
  const q = rawLine.trim().replace(/^\//, "");
  if (!q) return null;
  const parts = q.split(/\s+/);
  const first = parts[0]!.toLowerCase();
  const entry = exactCatalogEntry(catalog, first);
  if (!entry || !entry.takesArgs) return null;
  const askedArgs = parts.length > 1 || rawLine.endsWith(" ");
  if (!askedArgs && suggestionCount > 0) return null;
  // Server-catalog parents (endpoint) carry no client-side subs/argsHint —
  // derive a generic "<sub|…>" hint from the client-known tables.
  const fallbackSubs =
    first === "endpoint" ? ENDPOINT_SUBCOMMANDS : entry.subs ?? [];
  const usage =
    entry.argsHint ??
    (fallbackSubs.length ? `<${fallbackSubs.map((s) => s.name).join("|")}>` : "");
  const line = `/${entry.name}${usage ? ` ${usage}` : ""} — ${entry.help}`;
  return line.replace(/\s+/g, " ").trim();
}

export function parseSlashLine(line: string): { name: string; args: string } | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("/")) return null;
  const body = trimmed.slice(1).trim();
  if (!body) return null;
  const space = body.indexOf(" ");
  if (space === -1) return { name: body.toLowerCase(), args: "" };
  return { name: body.slice(0, space).toLowerCase(), args: body.slice(space + 1).trim() };
}

interface QuotedConsumeResult {
  token: string;
  quote: "'" | '"' | null;
  /** Extra indexes consumed beyond i (escaped quote inside double quotes). */
  skip: number;
}

/** Consume one character inside a quoted span. */
function consumeQuotedChar(value: string, i: number, char: string, quote: "'" | '"', token: string): QuotedConsumeResult {
  if (char === quote) return { token, quote: null, skip: 0 };
  if (char === "\\" && quote === '"' && value[i + 1] === '"') {
    return { token: token + '"', quote, skip: 1 };
  }
  return { token: token + char, quote, skip: 0 };
}

/** Consume one character outside quotes; pushes finished tokens onto `tokens`. */
function consumeUnquotedChar(char: string, token: string, tokens: string[]): { token: string; quote: "'" | '"' | null } {
  if (char === "'" || char === '"') return { token, quote: char };
  if (/\s/.test(char)) {
    if (token) tokens.push(token);
    return { token: "", quote: null };
  }
  return { token: token + char, quote: null };
}

/** Split slash arguments while preserving quoted names and Windows paths. */
export function tokenizeSlashArgs(value: string): string[] {
  const tokens: string[] = [];
  let token = "";
  let quote: "'" | '"' | null = null;
  for (let i = 0; i < value.length; i += 1) {
    const char = value[i]!;
    if (quote) {
      const consumed = consumeQuotedChar(value, i, char, quote, token);
      token = consumed.token;
      quote = consumed.quote;
      i += consumed.skip;
    } else {
      const consumed = consumeUnquotedChar(char, token, tokens);
      token = consumed.token;
      quote = consumed.quote;
    }
  }
  if (quote) throw new Error("Unterminated quote in slash command");
  if (token) tokens.push(token);
  return tokens;
}

function findEntry(catalog: SlashCommandEntry[], name: string): SlashCommandEntry | undefined {
  const lower = name.toLowerCase();
  // T5 fix (2026-09-19): prefer exact name match over aliases — `/teamup`
  // must route to the /teamup row, NOT the `/team` row whose aliases
  // simply include "teamup" (iterative find() = name-then-alias ordering;
  // exact-match beats alias-prefix shorthand generation).
  const exact = catalog.find((entry) => entry.name === lower);
  if (exact) return exact;
  return catalog.find((entry) => (entry.aliases ?? []).includes(lower));
}

/**
 * True when Enter should leave `/{name} ` in the draft instead of dispatching.
 * Parent commands with subcommands (e.g. /models, /endpoint) are runnable bare —
 * Companion lists / opens a picker. Optional `[args]` in help must not trap Enter.
 */
export function needsArgPrompt(
  entry: Pick<SlashCommandEntry, "takesArgs" | "subcommands"> &
    Partial<Pick<SlashCommandEntry, "help" | "argsHint" | "argsOptional">>,
  args: string,
): boolean {
  if (!entry.takesArgs) return false;
  if (args.trim()) return false;
  if ((entry.subcommands?.length ?? 0) > 0) return false;
  return !(entry.argsOptional || argsLookOptional(entry));
}

/** Usage text shows only optional `[args]`, no required `<arg>`: the
 * companion marks `/export [path.md]` takesArgs from its help, and the bare
 * form (preview) re-inserted `/export ` forever (TUI audit 2026-10-17). */
function argsLookOptional(entry: Partial<Pick<SlashCommandEntry, "help" | "argsHint">>): boolean {
  const usage = `${entry.argsHint ?? ""} ${entry.help ?? ""}`;
  return usage.includes("[") && !usage.includes("<");
}

/** /theme argument rows for the slash bar (names double as the bare-Enter bypass). */
const THEME_SLASH_SUBCOMMANDS: Array<{ name: string; help: string; takesArgs?: boolean }> = [
  // Lazy import would circular-load themes in dispatch tests;
  // the canonical list lives in ../themes — this is a name-mirror only.
  { name: "aurora", help: "R4 identity (default)" },
  { name: "tokyo-night", help: "Tokyo Night — cool blue/lavender dark" },
  { name: "nord", help: "Nord — arctic, low-contrast blue" },
  { name: "dracula", help: "Dracula — vibrant purple dark" },
  { name: "gruvbox-dark", help: "Gruvbox — warm retro groove" },
  { name: "catppuccin-mocha", help: "Catppuccin Mocha — soft pastel dark" },
  { name: "solarized-dark", help: "Solarized Dark — precision contrast" },
  { name: "solarized-light", help: "Solarized Light — paper-bright" },
  { name: "monokai", help: "Monokai — classic editor neon" },
  { name: "one-dark", help: "One Dark — Atom's subdued palette" },
  { name: "rose-pine", help: "Rosé Pine — muted pines & roses" },
  { name: "kanagawa", help: "Kanagawa — wave-inspired sumi ink" },
  { name: "high-contrast", help: "High contrast — accessibility first" },
  { name: "list", help: "Print theme names inline" },
];

type ProjectSpec = (typeof PROJECT_SUBCOMMANDS)[number];
type ProjectBridge = ReturnType<typeof getCompanionBridge>;
type ProjectCommandHandler = (
  bridge: ProjectBridge,
  spec: ProjectSpec,
  tokens: string[],
) => Promise<SlashDispatchResult>;

/** A failed slash RPC; the server's recovery hint (401 → /login, …) rides
 * along on its own line (TUI audit r2 #28). */
function slashError(err: unknown): SlashDispatchResult {
  return { kind: "error", message: slashErrorText(err) };
}

function slashErrorText(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const hint = bridgeErrorHint(err);
  return hint ? `${message}\n  → ${hint}` : message;
}

/** Subcommands that hint `/project <name> ` when invoked with no further args. */
const PROJECT_ARG_HINT_NAMES = ["create", "delete", "switch", "import", "init", "verify", "readiness", "plan", "run", "findings", "ingest", "session"];

function needsProjectArgHint(spec: ProjectSpec, tokens: string[]): boolean {
  // create/switch/import/… need more args
  return (
    spec.takesArgs &&
    tokens.length < 2 &&
    spec.name !== "list" &&
    spec.name !== "current" &&
    PROJECT_ARG_HINT_NAMES.includes(spec.name)
  );
}

function validateProjectSpec(spec: ProjectSpec, tokens: string[]): SlashDispatchResult | null {
  if (spec.unavailable) {
    return {
      kind: "error",
      message: spec.help.includes("unavailable")
        ? spec.help
        : `${spec.name} is unavailable until the summary API is ready`,
    };
  }
  if (needsProjectArgHint(spec, tokens)) {
    return { kind: "insert", text: `/project ${spec.name} ` };
  }
  return null;
}

interface ProjectPrep {
  spec?: ProjectSpec;
  tokens?: string[];
  shortCircuit?: SlashDispatchResult;
}

function prepareProjectDispatch(args: string): ProjectPrep {
  if (isOfflineBackend()) {
    return {
      shortCircuit: {
        kind: "error",
        message: "Project commands require a live backend (not offline/demo mode)",
      },
    };
  }

  let tokens: string[];
  try {
    tokens = tokenizeSlashArgs(args);
  } catch (err) {
    return { shortCircuit: slashError(err) };
  }

  const sub = (tokens[0] ?? "").toLowerCase();
  if (!sub) {
    return { shortCircuit: { kind: "insert", text: "/project " } };
  }

  const spec = PROJECT_SUBCOMMANDS.find((s) => s.name === sub);
  if (!spec) {
    return { shortCircuit: { kind: "error", message: `Unknown /project subcommand: ${sub}` } };
  }

  const validationError = validateProjectSpec(spec, tokens);
  if (validationError) return { shortCircuit: validationError };

  return { spec, tokens };
}

async function handleProjectCancelJob(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const jobId = tokens[1];
  if (!jobId) return { kind: "insert", text: "/project cancel " };
  const result = (await bridge.request("project_cancel_job", { jobId })) as {
    ok?: boolean;
    error?: string;
  };
  if (!result.ok) return { kind: "error", message: result.error ?? "Unable to cancel job" };
  actions.sendSystemNotice(`Cancellation requested for project job ${jobId}`);
  return { kind: "handled" };
}

async function handleProjectList(bridge: ProjectBridge): Promise<SlashDispatchResult> {
  const result = (await bridge.request("project_list", {})) as {
    projects?: Array<{ project_id: string; name?: string; active?: boolean }>;
    current?: string | null;
  };
  const lines = (result.projects ?? []).map((p) => {
    const mark = p.active ? "*" : " ";
    return `  ${mark} ${p.project_id}${p.name ? ` (${p.name})` : ""}`;
  });
  actions.sendSystemNotice(
    lines.length ? `Projects:\n${lines.join("\n")}` : "No projects configured. Use /project create <id>",
  );
  return { kind: "handled" };
}

async function handleProjectCurrent(bridge: ProjectBridge): Promise<SlashDispatchResult> {
  const result = (await bridge.request("project_current", {})) as {
    current?: string | null;
  };
  actions.sendSystemNotice(
    result.current ? `Current project: ${result.current}` : "No current project set",
  );
  return { kind: "handled" };
}

async function handleProjectDelete(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: "/project delete " };
  // Destructive: refuse to delete the project this tab is standing on
  // unless it is named explicitly (it IS what was typed — the guard is
  // against the no-arg default, which the insert rule above already covers).
  const result = (await bridge.request("project_delete", {
    project_id: projectId,
  })) as { ok?: boolean; project_id?: string; was_selected?: boolean; config_changed?: boolean; error?: string };
  if (!result.ok) return { kind: "error", message: result.error ?? `delete failed for ${projectId}` };
  actions.sendSystemNotice(
    result.was_selected
      ? `Deleted project: ${projectId} (this tab's selection cleared — other tabs keep theirs until they touch the ghost id)`
      : `Deleted project: ${projectId}`,
  );
  if (activeTab().project?.id === projectId) actions.setProject(null);
  if (result.config_changed) await refreshAfterProjectConfigChange();
  return { kind: "handled" };
}

async function handleProjectCreate(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: "/project create " };
  const nameIdx = tokens.indexOf("--name");
  const name = nameIdx >= 0 ? tokens[nameIdx + 1] : undefined;
  const result = (await bridge.request("project_create", {
    project_id: projectId,
    name,
  })) as { config_changed?: boolean; project_id?: string; created?: boolean };
  actions.sendSystemNotice(
    `${result.created === false ? "Updated" : "Created"} and selected project: ${result.project_id ?? projectId}` +
      createdNote(projectId, result.created !== false),
  );
  if (result.config_changed) await refreshAfterProjectConfigChange();
  return { kind: "handled" };
}

async function handleProjectSwitch(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: "/project switch " };
  const result = (await bridge.request("project_switch", {
    project_id: projectId,
  })) as { config_changed?: boolean };
  actions.sendSystemNotice(`Switched to project: ${projectId}`);
  if (result.config_changed) await refreshAfterProjectConfigChange();
  // Badge bug fix (2026-09-27): the status bar's project name only refreshed
  // on the next chat turn's snapshot — pull one now so the badge follows
  // the switch immediately.
  try {
    const state = await rpc(bridge, "get_state", {}, 15_000);
    applyBridgeStatePatch(mapBridgeStateToWorkspace(state, undefined));
  } catch {
    // snapshot refresh is best-effort; the switch itself already landed.
  }
  return { kind: "handled" };
}

async function handleProjectStatus(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const targetId = tokens[1];
  const current = (await bridge.request("project_current", {})) as { current?: string | null };
  const pid = targetId || current.current;
  if (!pid) {
    return { kind: "error", message: "No project id — use /project status <id> or /project switch <id>" };
  }
  const lines = await buildProjectStatusLines(pid, current.current, workspace.dataSource);
  actions.sendSystemNotice(lines.join("\n"));
  return { kind: "handled" };
}

/** Parse optional flags after `/project run <id>`.
 *
 * The RC1 `simulate` default is retired (WP-08): placeholder runs no longer
 * exist, so `simulate` is only returned when the operator EXPLICITLY passes
 * a legacy flag (`--simulate`/`--real`) — the bridge answers that with its
 * "simulate ignored" notice. Nothing is sent for the normal path.
 * Exported for unit tests.
 */
export function parseProjectRunFlags(tokens: string[]): {
  simulate?: boolean;
  approve: boolean;
  max_iterations?: number;
} {
  let simulate: boolean | undefined;
  let approve = false;
  let max_iterations: number | undefined;
  for (let i = 2; i < tokens.length; i++) {
    const raw = tokens[i] ?? "";
    const flag = raw.toLowerCase();
    if (flag === "--simulate" || flag === "simulate") {
      simulate = true;
      continue;
    }
    if (flag === "--real" || flag === "real") {
      simulate = false;
      continue;
    }
    if (flag === "--approve" || flag === "approve") {
      approve = true;
      continue;
    }
    if (flag === "--max-iterations" || flag === "max-iterations" || flag === "max_iterations") {
      const next = Number(tokens[++i]);
      if (Number.isFinite(next) && next > 0) max_iterations = Math.floor(next);
      continue;
    }
  }
  return { simulate, approve, max_iterations };
}

/** Builds RPC params for the long-running C2 ops; insertHint is set when args are missing. */
export function buildLongOpParams(rpc: string, projectId: string, tokens: string[]): {
  params: Record<string, unknown>;
  insertHint: SlashDispatchResult | null;
} {
  if (rpc === "project_import") {
    const paths = tokens.slice(2);
    if (paths.length === 0) return { params: {}, insertHint: { kind: "insert", text: `/project import ${projectId} ` } };
    return { params: { project_id: projectId, paths }, insertHint: null };
  }
  if (rpc === "project_verify") {
    const question = tokens.slice(2).join(" ");
    if (!question) return { params: {}, insertHint: { kind: "insert", text: `/project verify ${projectId} ` } };
    return { params: { project_id: projectId, question }, insertHint: null };
  }
  if (rpc === "project_plan") {
    const goal = tokens.slice(2).join(" ");
    if (!goal) return { params: {}, insertHint: { kind: "insert", text: `/project plan ${projectId} ` } };
    return { params: { project_id: projectId, research_goal: goal }, insertHint: null };
  }
  if (rpc === "project_run") {
    const flags = parseProjectRunFlags(tokens);
    const params: Record<string, unknown> = {
      project_id: projectId,
      approve: flags.approve,
    };
    // Legacy flag forwarded only when explicitly given — the bridge answers
    // it with the "simulate ignored" notice (retired RC1 placeholder runs).
    if (flags.simulate !== undefined) params.simulate = flags.simulate;
    if (flags.max_iterations != null) params.max_iterations = flags.max_iterations;
    return { params, insertHint: null };
  }
  return { params: { project_id: projectId }, insertHint: null };
}

async function handleProjectLongOp(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  // C2 long ops — return jobId immediately; progress via events
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: `/project ${spec.name} ` };

  const { params, insertHint } = buildLongOpParams(spec.rpc, projectId, tokens);
  if (insertHint) return insertHint;

  const started = (await bridge.request(spec.rpc, params)) as { jobId?: string };
  if (!started.jobId) {
    return { kind: "error", message: `${spec.name} did not return a job id` };
  }
  trackActiveProjectJob(started.jobId, spec.name);
  actions.sendSystemNotice(
    `Started ${spec.name} (job ${started.jobId}). Progress will appear here. Cancel with /project cancel ${started.jobId}`,
  );
  return { kind: "handled" };
}

async function handleProjectFindings(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const targetId = tokens[1];
  const current = (await bridge.request("project_current", {})) as { current?: string | null };
  const pid = targetId || current.current;
  if (!pid) {
    return { kind: "error", message: "No project id — use /project findings <id> or /project switch <id>" };
  }
  const result = (await bridge.request("project_findings", { project_id: pid })) as {
    result?: { findings?: Array<{ severity?: string }>; total?: number };
  };
  const payload = result.result ?? {};
  const findings = payload.findings ?? [];
  const total = payload.total ?? findings.length;
  const bySeverity = new Map<string, number>();
  for (const finding of findings) {
    const severity = String(finding?.severity ?? "unknown");
    bySeverity.set(severity, (bySeverity.get(severity) ?? 0) + 1);
  }
  const header = `Findings for ${pid}: ${total} total`;
  const lines = [...bySeverity.entries()].sort().map(
    ([severity, count]) => `  ${severity}: ${count}`,
  );
  actions.sendSystemNotice([header, ...lines].join("\n"));
  return { kind: "handled" };
}

/** Split tokens after the project id into --flag value pairs. */
function collectFlagValues(tokens: string[], startIndex: number, flag: string): string[] {
  const values: string[] = [];
  for (let i = startIndex; i < tokens.length; i++) {
    if ((tokens[i] ?? "").toLowerCase() === flag && i + 1 < tokens.length) {
      values.push(tokens[i + 1] ?? "");
      i++;
    }
  }
  return values;
}

async function handleProjectIngest(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: "/project ingest " };
  const graphs = collectFlagValues(tokens, 2, "--graph");
  const graph = graphs[0];
  const files = collectFlagValues(tokens, 2, "--file");
  const urls = collectFlagValues(tokens, 2, "--url");
  if (!graph) {
    return { kind: "error", message: "ingest needs a named graph: --graph <name>" };
  }
  if (files.length === 0 && urls.length === 0) {
    return { kind: "error", message: "nothing to ingest — pass --file and/or --url" };
  }
  const result = (await bridge.request("project_ingest", {
    project_id: projectId,
    graph,
    files,
    urls,
  })) as { ingest?: { job_id?: string; status?: string } };
  const ingest = result.ingest ?? {};
  actions.sendSystemNotice(
    `Ingest queued into graph '${graph}' — job: ${ingest.job_id ?? "?"} (${ingest.status ?? "?"})`,
  );
  return { kind: "handled" };
}

async function handleProjectSessionCreate(bridge: ProjectBridge, spec: ProjectSpec, tokens: string[]): Promise<SlashDispatchResult> {
  void spec;
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: "/project session " };
  const positionalGoal: string[] = [];
  for (const token of tokens.slice(2)) {
    if (token.startsWith("--")) break;
    positionalGoal.push(token);
  }
  const goal = positionalGoal.join(" ").trim();
  if (!goal) return { kind: "insert", text: `/project session ${projectId} ` };
  const titles = collectFlagValues(tokens, 2, "--title");
  const result = (await bridge.request("project_session_create", {
    project_id: projectId,
    goal,
    title: titles[0],
  })) as { session?: { session_id?: string; title?: string; status?: string } };
  const session = result.session ?? {};
  actions.sendSystemNotice(
    `Research session created: ${session.session_id ?? "?"} ${session.title ? `(${session.title})` : ""}`,
  );
  return { kind: "handled" };
}

const PROJECT_RPC_HANDLERS: Record<string, ProjectCommandHandler> = {
  project_cancel_job: handleProjectCancelJob,
  project_list: handleProjectList,
  project_current: handleProjectCurrent,
  project_create: handleProjectCreate,
  project_register: (bridge, _spec, tokens) => handleProjectRegister(bridge, tokens),
  project_delete: handleProjectDelete,
  project_switch: handleProjectSwitch,
  project_status: handleProjectStatus,
  project_findings: handleProjectFindings,
  project_ingest: handleProjectIngest,
  project_session_create: handleProjectSessionCreate,
  project_import: handleProjectLongOp,
  project_init: handleProjectLongOp,
  project_verify: handleProjectLongOp,
  project_readiness: handleProjectLongOp,
  project_plan: handleProjectLongOp,
  project_run: handleProjectLongOp,
};

async function dispatchProjectCommand(args: string): Promise<SlashDispatchResult> {
  const prep = prepareProjectDispatch(args);
  if (prep.shortCircuit) return prep.shortCircuit;

  wireProjectJobEventsOnce();
  const bridge = getCompanionBridge();

  try {
    const spec = prep.spec!;
    const handler = PROJECT_RPC_HANDLERS[spec.rpc] ?? handleProjectLongOp;
    return await handler(bridge, spec, prep.tokens!);
  } catch (err) {
    return slashError(err);
  }
}

/** Management workflow verbs rendered as cards (mgmt-card.v1), notice fallback. */
async function wfListCard(bridge: ProjectBridge): Promise<SlashDispatchResult> {
  const payload = await bridge.request("workflow.list", {}, 30_000);
  umrsWorkflowActions.setCatalog((payload as { workflows?: UmrsWorkflowCatalogItem[] } | null)?.workflows);
  actions.sendMgmtCard("wf-list", payload);
  return { kind: "handled" };
}

async function wfShowCard(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const name = tokens[1];
  if (!name) return { kind: "insert", text: "/workflow show " };
  const payload = await bridge.request("workflow_describe", { workflow_name: name }, 30_000);
  // The describe payload is flat (version, steps, …); nesting it under
  // `describe` hid every row but the name (TUI audit 2026-10-17).
  actions.sendMgmtCard("wf-show", { workflow_name: name, ...(payload as Record<string, unknown>) });
  return { kind: "handled" };
}

async function wfStatusCard(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const executionId = tokens[1];
  if (!executionId) return { kind: "insert", text: "/workflow status " };
  const payload = await bridge.request("workflow.detail", { execution_id: executionId }, 30_000);
  actions.sendMgmtCard("wf-run", payload);
  return { kind: "handled" };
}

async function wfResultsCard(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const executionId = tokens[1];
  if (!executionId) return { kind: "insert", text: "/workflow results " };
  const payload = await bridge.request("workflow.results", { execution_id: executionId }, 30_000);
  actions.sendMgmtCard("wf-results", payload);
  return { kind: "handled" };
}

type WfCardHandler = (bridge: ProjectBridge, tokens: string[]) => Promise<SlashDispatchResult>;

/**
 * Own-key lookup in a verb table keyed by user input: `/constructor` or
 * `/admin __proto__` must miss, not resolve to an Object.prototype member
 * (TUI audit 2026-10-17 review).
 */
function ownHandler<T>(table: Record<string, T>, key: string): T | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined;
}

const WF_CARD_VERBS: Record<string, WfCardHandler> = {
  list: wfListCard,
  show: wfShowCard,
  status: wfStatusCard,
  results: wfResultsCard,
  graph: workflowGraphCommand,
  export: workflowExportCommand,
};

async function dispatchWorkflowManagement(args: string): Promise<SlashDispatchResult | null> {
  if (isOfflineBackend()) return null;
  const tokens = args.split(/\s+/).filter(Boolean);
  const verb = (tokens[0] ?? "list").toLowerCase();
  const canonical: Record<string, string> = {
    ls: "list",
    describe: "show",
    info: "show",
    state: "status",
    output: "results",
  };
  const sub = canonical[verb] ?? verb;
  // ADR-075 G2: /workflow on|off — the session workflow arm (before cards).
  if (sub === "on" || sub === "off") return await handleWorkflowArm(sub === "on");
  const bridge = getCompanionBridge();
  try {
    const handler = ownHandler(WF_CARD_VERBS, sub);
    // mutations (run/cancel/approve) stay on the backend dispatch path
    // for the approval flow — forward to the generic handler.
    if (!handler) return null;
    return await handler(bridge, tokens);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    actions.sendErrorCard({
      what: `workflow ${sub} failed`,
      action: "needs_you",
      detail,
    });
    return { kind: "error", message: detail };
  }
}

/** /workflow on|off — ADR-075 G2 session arm: while armed the compute
 * submit guards refuse the non-UMRS lane by name; the toggle rides the
 * session state (persists across resumes), not a fire-once arming. */
async function handleWorkflowArm(armed: boolean): Promise<SlashDispatchResult> {
  try {
    const bridge = getCompanionBridge();
    const res = (await bridge.request("set_workflow_armed", { armed }, 15_000)) as {
      workflow_armed?: boolean;
      state?: unknown;
    };
    if (res.state) {
      const { applyBridgeStatePatch, mapBridgeStateToWorkspace } = await import(
        "../bridge/map-state"
      );
      applyBridgeStatePatch(mapBridgeStateToWorkspace(res.state, undefined));
    }
    actions.sendSystemNotice(
      armed
        ? "⚙ workflow arm ON — compute must ride the UMRS durable lane (local fallback refused while armed). /workflow off to lift."
        : "workflow arm off — compute lanes back to default routing.",
    );
    return { kind: "handled" };
  } catch (err) {
    return {
      kind: "error",
      message: `workflow arm failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

/** Management research-loop verbs rendered as cards (M2). */
async function handleLoopMgmt(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult | null> {
  const verb = (tokens[0] ?? "list").toLowerCase();
  switch (verb) {
    case "list":
    case "ls": {
      const payload = await bridge.request("research_loop.list", {}, 30_000);
      actions.sendMgmtCard("loop-list", payload);
      return { kind: "handled" };
    }
    case "status":
    case "show": {
      const loopId = tokens[1];
      if (!loopId) return { kind: "insert", text: "/loop status " };
      const payload = await bridge.request(
        "research_loop.status",
        { loop_id: loopId },
        30_000,
      );
      actions.sendMgmtCard("loop-status", payload);
      return { kind: "handled" };
    }
    default:
      return handleLoopControl(verb, tokens);
  }
}

const LOOP_CONTROL_VERBS = new Set(["stop", "pause", "resume"]);

/** /loop help (or a verb it does not know): the friendly help as a notice. */
function loopHelp(wrong = ""): SlashDispatchResult {
  actions.sendSystemNotice(opsText.loopHelpText(wrong));
  return { kind: "handled" };
}

/** /loop start <goal>: the goal is required (the companion's own usage
 * line named /research, not /loop). */
function loopStart(tokens: string[]): Promise<SlashDispatchResult> | SlashDispatchResult {
  if (tokens.length < 2) return { kind: "error", message: opsText.LOOP_START_NEEDS_GOAL };
  return forwardToCompanion("research", tokens.join(" "));
}

/** /loop start <goal> and /loop stop <id> are the companion's /research
 * start|stop, which resolve the loop through the runtime (umrs, local or
 * hybrid); pause|resume ride the loops.<verb> RPCs, which follow the same
 * resolved runtime.
 * Returning null here fell through to the builder pane row (TUI audit
 * 2026-10-17). */
async function handleLoopControl(verb: string, tokens: string[]): Promise<SlashDispatchResult> {
  if (opsText.LOOP_HELP_VERBS.has(verb)) return loopHelp();
  if (verb === "start") return loopStart(tokens);
  // An unknown verb gets the whole help, not a one-line usage (2026-11-03).
  if (!LOOP_CONTROL_VERBS.has(verb)) return loopHelp(tokens[0] ?? verb);
  const loopId = tokens[1];
  if (!loopId) return { kind: "error", message: opsText.loopNeedsIdText(verb) };
  if (verb === "stop") return forwardToCompanion("research", tokens.join(" "));
  return loopsControl(verb, loopId);
}

async function sessionViewCard(bridge: ProjectBridge): Promise<SlashDispatchResult> {
  const payload = await bridge.request("research_session.view", {}, 30_000);
  actions.sendMgmtCard("session-view", payload);
  return { kind: "handled" };
}

// C2: pin/unpin/rename route through the bridge (pinned marker shows
// on tui-js/webview session browsers; v3 keeps a notice here).
async function sessionPinOrUnpin(bridge: ProjectBridge, tokens: string[], verb: string): Promise<SlashDispatchResult> {
  const sid = tokens[1];
  if (!sid) return { kind: "insert", text: `/session ${verb} ` };
  await bridge.request(`session.${verb}`, { session_id: sid }, 30_000);
  actions.sendSystemNotice(`${verb === "pin" ? "📌 pinned" : "unpinned"} session ${sid}`);
  return { kind: "handled" };
}

async function sessionRename(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const sid = tokens[1];
  const newName = tokens.slice(2).join(" ").trim();
  if (!sid || !newName) return { kind: "insert", text: "/session rename " };
  await bridge.request("session.rename", { session_id: sid, name: newName }, 30_000);
  actions.sendSystemNotice(`session ${sid} renamed to ${newName}`);
  return { kind: "handled" };
}

/** Fork a session row under a fresh id (G-P9-2). */
async function sessionFork(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const sid = tokens[1];
  if (!sid) return { kind: "insert", text: "/session fork <id> [new name]" };
  const newName = tokens.slice(2).join(" ").trim();
  const result = await rpc(bridge, "session.fork", { session_id: sid, name: newName || undefined }, 30_000);
  actions.sendSystemNotice(
    `forked ${sid} → ${result.session_id} (${result.messages ?? 0} messages, "${result.name}")`,
  );
  return { kind: "handled" };
}

/** Management session verbs (pin/unpin/rename/fork/view; M2 cards + notices). */
async function handleSessionMgmt(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult | null> {
  const verb = (tokens[0] ?? "").toLowerCase();
  if (verb === "view") return sessionViewCard(bridge);
  if (verb === "pin" || verb === "unpin") return sessionPinOrUnpin(bridge, tokens, verb);
  if (verb === "rename") return sessionRename(bridge, tokens);
  if (verb === "fork") return sessionFork(bridge, tokens);
  // Bare /session, help or a typo: say what /session does. Falling through
  // to the catalog printed "Unknown slash command: /session" for a command
  // that exists (stabilization 2026-10-21, slash-c#0 follow-up).
  const lead = verb && verb !== "help" ? `unknown /session verb "${verb}" — ` : "";
  return { kind: "error", message: `${lead}${SESSION_USAGE}` };
}

const SESSION_USAGE = "usage: /session view | pin | unpin | rename <name> | fork — /sessions lists and resumes sessions";

// The verbs v3 serves itself ("", status, show, detail): manifest localVerbs.
const KNOWLEDGE_LOCAL_VERBS = manifestVerbs("knowledge", "localVerbs");

/** Management /knowledge verb family (M2 cards); `/knowledge <query>` searches. */
async function handleKnowledgeMgmt(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const verb = (tokens[0] ?? "").toLowerCase();
  if (!KNOWLEDGE_LOCAL_VERBS.has(verb)) {
    // The query used to be dropped for the status card (2026-10-17); the
    // companion's /knowledge runs the K3 query facade.
    const args = tokens.join(" ");
    return bridgeDispatchSlash({ name: "knowledge", args }, `/knowledge ${args}`);
  }
  if (verb === "detail") {
    const payload = await bridge.request("get_knowledge", {}, 30_000);
    actions.sendMgmtCard("knowledge-detail", payload);
    return { kind: "handled" };
  }
  // bare /knowledge (status|show): project graph status projection.
  const payload = await bridge.request("research_knowledge.status", {}, 30_000);
  actions.sendMgmtCard("knowledge-status", payload);
  return { kind: "handled" };
}

/** Management /graph verb family (M2 cards). */
async function handleGraphMgmt(bridge: ProjectBridge, tokens: string[]): Promise<SlashDispatchResult> {
  const verb = (tokens[0] ?? "").toLowerCase();
  if (!verb) {
    // bare /graph: return the project's knowledge status as a card in the
    // chat (summon, not space-jump). /graph <graph-id|name> peeks content.
    const payload = await bridge.request("research_knowledge.status", {}, 30_000);
    actions.sendMgmtCard("knowledge-status", payload);
    return { kind: "handled" };
  }
  const graphId = tokens[0];
  const payload = await bridge.request(
    "knowledge_graph_content",
    { graph_id: graphId },
    30_000,
  );
  // GraphRegistry.graph_content (nodes, edges, owner_id, truncated) has its
  // own card; the graph-list card dropped the content (TUI audit 2026-10-17).
  actions.sendMgmtCard("knowledge-graph-content", payload);
  return { kind: "handled" };
}

type MgmtFamilyHandler = (bridge: ProjectBridge, tokens: string[]) => Promise<SlashDispatchResult | null>;

const MGMT_FAMILY_HANDLERS: Record<string, MgmtFamilyHandler> = {
  loop: handleLoopMgmt,
  session: handleSessionMgmt,
  knowledge: handleKnowledgeMgmt,
  graph: handleGraphMgmt,
};

/** Management research-loop/session verbs rendered as cards (M2). */
async function dispatchLoopAndSessionManagement(
  name: string,
  args: string,
): Promise<SlashDispatchResult | null> {
  if (isOfflineBackend()) return null;
  const tokens = args.split(/\s+/).filter(Boolean);
  const verb = (tokens[0] ?? "list").toLowerCase();
  const bridge = getCompanionBridge();
  try {
    const handler = ownHandler(MGMT_FAMILY_HANDLERS, name);
    if (!handler) return null;
    return await handler(bridge, tokens);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    actions.sendErrorCard({
      what: `/${name} ${verb} failed`,
      action: "needs_you",
      detail,
    });
    return { kind: "error", message: detail };
  }
}

/** Toggle helper for /settings ambient_cards|bell on|off. */
function settingsToggle(verb: string, value: string | undefined): SlashDispatchResult | null {
  if (value !== "on" && value !== "off") return null;
  const enabled = value === "on";
  if (verb === "ambient_cards" || verb === "ambient") {
    actions.setAmbientCardsEnabled(enabled);
    actions.sendSystemNotice(`ambient event cards: ${enabled ? "ON" : "OFF"}`);
    return { kind: "handled" };
  }
  if (verb === "bell") {
    actions.setCompletionBellEnabled(enabled);
    actions.sendSystemNotice(`completion bell: ${enabled ? "ON" : "OFF"}`);
    return { kind: "handled" };
  }
  return null;
}

/** /settings — personal prefs for the current session (ambient/bell/matrix/rules). */
async function handleSettingsCommand(args: string): Promise<SlashDispatchResult> {
  const tokens = args.split(/\s+/).filter(Boolean);
  const verb = (tokens[0] ?? "").toLowerCase();
  const toggled = settingsToggle(verb, tokens[1]);
  if (toggled) return toggled;
  if (verb !== "" && verb !== "matrix" && verb !== "policy" && verb !== "rules") {
    return { kind: "error", message: "usage: /settings ambient_cards on|off · /settings bell on|off · /settings rules" };
  }
  return handleSettingsSurface(verb, tokens);
}

/** Async settings surfaces after the toggle fast-path (matrix/rules/status). */
async function handleSettingsSurface(verb: string, tokens: string[]): Promise<SlashDispatchResult> {
  if (verb === "matrix" || verb === "policy") return settingsMatrixNotice();
  if (verb === "rules") return handleSettingsRules(tokens);
  actions.sendSystemNotice(
    `ambient event cards: ${workspace.ambientCardsEnabled ? "ON" : "OFF"}\n` +
      `completion bell: ${workspace.completionBellEnabled ? "ON" : "OFF"}\n` +
      `Usage: /settings ambient_cards on|off · /settings bell on|off · /settings rules`,
  );
  return { kind: "handled" };
}

/** /statusline [<cmd>|off] — set/clear/show the user status-line command. */
function handleStatuslineCommand(args: string): SlashDispatchResult {
  const command = args.trim();
  if (!command) {
    const current = readStatuslineCommand();
    actions.sendSystemNotice(
      current
        ? `status line: ${current}  (${statuslinePath()}; /statusline off removes it)`
        : `no status line set — /statusline <shell command> (env: R4_MODEL R4_PROJECT R4_SESSION R4_PERMISSION)`,
    );
    return { kind: "handled" };
  }
  writeStatuslineCommand(command);
  actions.sendSystemNotice(
    command === "off" ? "status line removed" : `status line set — refreshes every 5 s: ${command}`,
  );
  return { kind: "handled" };
}

/** /btw <question> — side question (Claude-Code parity); bare /btw reopens. */
function handleBtwCommand(args: string): SlashDispatchResult {
  if (isOfflineBackend()) return { kind: "error", message: "/btw needs a live bridge" };
  void actions.askBtw(args);
  return { kind: "handled" };
}

/** /undo — restore the last user prompt into the draft (C4). */
/**
 * /undo — drop the last chat prompt and everything after it, and put that
 * prompt back in the composer. Slash echoes (this /undo's own included)
 * and shell relays are not prompts: matching them made /undo cut only its
 * own echo and report "draft restored (5 chars)" (stabilization
 * 2026-10-21, slash-c#2). A sent turn is cut from the companion history
 * too (session.truncate), or the model kept it in context.
 */
async function handleUndoCommand(): Promise<SlashDispatchResult> {
  const messages = activeTab().chatMessages;
  const cutFrom = messages.findLastIndex(isChatPrompt);
  if (cutFrom === -1) {
    actions.sendSystemNotice("nothing to undo — no chat prompt in this session");
    return { kind: "handled" };
  }
  const target = messages[cutFrom]!;
  const failure = await truncateCompanionAt(target.turnId);
  if (failure) return { kind: "error", message: `undo failed — nothing was cut: ${failure}` };
  actions.discardVisibleMessagesAfter(cutFrom);
  actions.setPendingChatDraft(target.content);
  actions.sendSystemNotice(`undone — your last prompt is back in the composer (${target.content.length} chars)`);
  return { kind: "handled" };
}

/** Cut the companion history at `turnId` (null = done or nothing to cut). */
async function truncateCompanionAt(turnId: string | undefined): Promise<string | null> {
  if (!turnId || isOfflineBackend()) return null;
  try {
    await rpc(getCompanionBridge(), "session.truncate", { turn_id: turnId }, 30_000);
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

const COMPACT_USAGE =
  "usage: /compact [keep] — summarize older history, keeping the last <keep> messages (default 6)";

/** /compact's argument: the keep count, null for none, or a usage text. */
function compactKeepArg(args: string): { keep: number | null } | { usage: string } {
  const token = args.trim().split(/\s+/)[0] ?? "";
  if (!token) return { keep: null };
  if (/^\d+$/.test(token) && Number(token) > 0) return { keep: Number(token) };
  if (["help", "--help", "-h", "?"].includes(token)) return { usage: COMPACT_USAGE };
  return { usage: `/compact: "${token}" is not a message count — ${COMPACT_USAGE}` };
}

/** /compact — force compaction of the session history via the bridge (C4).
 * Only a positive count is a keep argument: `help` or any other word
 * compacted with the default keep — irreversibly (slash-c#1). */
async function handleCompactCommand(args: string): Promise<SlashDispatchResult> {
  const arg = compactKeepArg(args);
  if ("usage" in arg) {
    actions.sendSystemNotice(arg.usage);
    return { kind: "handled" };
  }
  if (isOfflineBackend()) return { kind: "error", message: "compact needs a live bridge" };
  try {
    const bridge = getCompanionBridge();
    const result = await rpc(bridge, "session.compact", arg.keep ? { keep_recent: arg.keep } : {}, 60_000);
    if (result?.changed) {
      // The bridge's context_compact event writes the same keyed line, so
      // /compact shows one "compacted" row, not two (TUI audit r2 #20); an
      // earlier compaction's row stays where it is.
      actions.upsertExchangeNotice(
        COMPACTION_NOTICE_KEY,
        `⇅ compacted history: ${result.from} → ${result.to} messages (kept recent ${result.keep_recent})`,
      );
    } else {
      actions.sendSystemNotice(`nothing to compact (${result?.message_count ?? 0} messages)`);
    }
    return { kind: "handled" };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { kind: "error", message: `compact failed: ${detail}` };
  }
}

/** /login <token> — authenticate with a JWT (r4 auth login) and reconnect.
 *  `help`, a flag or several words are refused before r4 runs (slash-b#0:
 *  "help" was stored as the key and reported "login ok"); r4 stores without
 *  asking the gateway, so the receipt comes from `r4 auth status` and a
 *  token the gateway rejects is reported as rejected (connect#6). */
async function handleLoginCommand(args: string): Promise<SlashDispatchResult> {
  const { LOGIN_USAGE, authStatus, loginReceipt, loginTokenOf, loginWithJwt } = await import("../lib/connect");
  const token = loginTokenOf(args);
  if (!token) return { kind: "error", message: LOGIN_USAGE };
  try {
    const res = await loginWithJwt(token);
    if (!res.ok) return { kind: "error", message: `login failed: ${res.output}` };
    actions.sendSystemNotice("token saved — reconnecting companion bridge, then asking the gateway…");
    const { getCompanionBridge } = await import("../bridge");
    try {
      await getCompanionBridge().reconnect();
    } catch (err) {
      actions.sendSystemNotice(`bridge reconnect failed (restart TUI): ${slashErrorText(err)}`);
      return { kind: "handled" };
    }
    // A silent success used to look exactly like a wedged reconnect ("then
    // nothing later" — field report 2026-10-21): the receipt names WHO is
    // signed in, or that the gateway refused the token.
    const receipt = loginReceipt(await authStatus());
    if (!receipt.ok) return { kind: "error", message: receipt.text };
    actions.sendSystemNotice(receipt.text);
    return { kind: "handled" };
  } catch (err) {
    return { kind: "error", message: `login failed: ${slashErrorText(err)}` };
  }
}

const JOB_COMPANION_VERBS = manifestVerbs("job", "forwardVerbs");

/** Usage error for a bare /job; the verbs are the companion's /job (they
 * used to be read as a job id — TUI audit 2026-10-17); null for an id. */
function jobNonIdResult(first: string, args: string): SlashDispatchResult | Promise<SlashDispatchResult> | null {
  if (!first) {
    return { kind: "error", message: "usage: /job <id|job-uuid> | list | describe|cancel|retry <id>" };
  }
  if (JOB_COMPANION_VERBS.has(first.toLowerCase())) return forwardToCompanion("job", args.trim());
  return null;
}

/** /job <id> — open the attempt-timeline overlay for a durable job. */
async function handleJobCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "job graph requires a live backend" };
  }
  const id = args.trim().split(/\s+/).filter(Boolean)[0] ?? "";
  const nonId = jobNonIdResult(id, args);
  if (nonId) return nonId;
  actions.openListOverlay("jobgraph", id);
  return { kind: "handled" };
}

/** /mode — show or switch the interaction mode (badge flips at Enter).
 * T5 topic-24: `/mode write-plan on|off` toggles the write_requires_plan
 * session flag via its own bridge verb (r4.mode lane is NOT fake-dispatched). */
async function handleModeCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "mode switch requires a live backend" };
  }
  const target = args.trim().toLowerCase();
  // T5 topic-24: own lane first (never invented).
  if (target === "write-plan on" || target === "write-plan off") {
    const bridge = getCompanionBridge();
    try {
      const enabled = target.endsWith(" on");
      const result = (await bridge.request(
        "set_write_requires_plan",
        { enabled },
        15_000,
      )) as { write_requires_plan?: boolean };
      actions.sendSystemNotice(
        `write_requires_plan → ${result.write_requires_plan ? "on" : "off"}`,
      );
      return { kind: "handled" };
    } catch (err) {
      return {
        kind: "error",
        message: `write-plan failed: ${err instanceof Error ? err.message : String(err)}`,
      };
    }
  }
  // ADR-104 D-1: `/mode <m>` is set_interaction_mode, answered on the tab
  // that asked (the badge flips at Enter); a failure is an error notice,
  // never a success. Any other form goes to the companion intact.
  const { forwardModeLine, isModeName, switchInteractionMode } = await import("../lib/interaction-mode");
  if (!isModeName(target)) return forwardModeLine(target ? `/mode ${args.trim()}` : "/mode");
  await switchInteractionMode(target);
  return { kind: "handled" };
}

const CODE_TOGGLE_VERBS = manifestVerbs("code", "localVerbs");

/** /code|coding [on|off] — toggle the builtin code skill (W2.5). Any other
 * verb (status, diff, run, test, …) is the companion's /code workbench; it
 * used to switch coding mode ON instead (TUI audit 2026-10-17). */
/** `/skills on|off code` through the companion; its output lines. */
async function toggleCodeSkill(enable: boolean): Promise<string[]> {
  const bridge = getCompanionBridge();
  const result = await rpc(
    bridge,
    "dispatch_slash_command",
    { command: enable ? "/skills on code" : "/skills off code" },
    30_000,
  );
  return (result.output ?? []).map((item) => (typeof item === "string" ? item : String(item?.text ?? "")));
}

/** The /code on|off notice, with the companion's own lines appended. */
function codeToggleNotice(enable: boolean, lines: string[]): string {
  const echo = lines.length ? ` (${lines.join(" ")})` : "";
  return enable
    ? `✎ coding mode ON — code skill active: the agent reads/edits/runs files with approval gates. Off with /code off.${echo}`
    : `coding mode off.${echo}`;
}

async function handleCodeCommand(args: string): Promise<SlashDispatchResult> {
  const verb = (args.trim().split(/\s+/)[0] ?? "").toLowerCase();
  const enable = verb !== "off";
  if (isOfflineBackend()) {
    return { kind: "error", message: "coding mode requires a live backend" };
  }
  if (!CODE_TOGGLE_VERBS.has(verb)) return codeWorkbench(verb, args.trim());
  try {
    const lines = await toggleCodeSkill(enable);
    actions.setCodingMode(enable);
    actions.sendSystemNotice(codeToggleNotice(enable, lines));
    return { kind: "handled" };
  } catch (err) {
    return {
      kind: "error",
      message: `code toggle failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

// The Python edit/review/fix verbs hand their prompt back as a chat turn
// (dispatch_slash_command follow_up_messages, r2 followup): the companion
// builds the prompt, bridgeDispatchSlash sends it as a streamed turn.
async function codeWorkbench(_verb: string, args: string): Promise<SlashDispatchResult> {
  return forwardToCompanion("code", args);
}

/** /acquisition [open|off|extend <graph>] — session knowledge write mode.
 * Server-side guards enforce it (tool refusals reach the model honestly);
 * this verb only flips the session state and reports the summary. */
async function handleAcquisitionCommand(args: string): Promise<SlashDispatchResult> {
  const { parseAcquisitionArgs } = await import("../lib/acquisition");
  const parsed = parseAcquisitionArgs(args);
  if (!parsed.ok) return { kind: "error", message: parsed.error! };
  try {
    const bridge = getCompanionBridge();
    const res = (await bridge.request(
      "set_knowledge_acquisition",
      parsed.params,
      15_000,
    )) as {
      summary?: string;
      acquisition?: { mode?: string; graphs?: string[] };
      state?: unknown;
    };
    if (res.state) {
      const { applyBridgeStatePatch, mapBridgeStateToWorkspace } = await import("../bridge/map-state");
      applyBridgeStatePatch(mapBridgeStateToWorkspace(res.state, undefined));
    }
    actions.sendSystemNotice(`⛁ ${res.summary ?? "knowledge acquisition updated"}`);
    return { kind: "handled" };
  } catch (err) {
    return {
      kind: "error",
      message: `acquisition failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

const DECISION_MODEL_VERBS = manifestVerbs("decision-model", "forwardVerbs");

/** /decision-model on [model] | off | status (ADR-076). The Python registry
 * owns the behaviour (profile setting + the live session's decision_evaluate
 * tool); the TUI validates the verb and forwards the canonical line. */
async function handleDecisionModelCommand(args: string): Promise<SlashDispatchResult> {
  const trimmed = args.trim();
  const verb = (trimmed.split(/\s+/)[0] ?? "").toLowerCase();
  if (!DECISION_MODEL_VERBS.has(verb)) {
    return { kind: "error", message: "usage: /decision-model [status] | on [MODEL] | off" };
  }
  const line = trimmed ? `/decision-model ${trimmed}` : "/decision-model";
  try {
    return await bridgeDispatchSlash({ name: "decision-model", args: trimmed }, line);
  } catch (err) {
    return {
      kind: "error",
      message: `decision-model failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

/** The line goes to the companion command of the same name; a bridge
 * failure becomes an error result. */
async function forwardToCompanion(name: string, args: string): Promise<SlashDispatchResult> {
  const line = args ? `/${name} ${args}` : `/${name}`;
  try {
    return await bridgeDispatchSlash({ name, args }, line);
  } catch (err) {
    return { kind: "error", message: `${name} failed: ${slashErrorText(err)}` };
  }
}

type BuiltinHandler = (args: string) => SlashDispatchResult | Promise<SlashDispatchResult>;

/** Built-in direct handlers, keyed by manifest row (intercept "builtin").
 * The row's aliases reach the same handler (stageRowName): matching "tour"
 * alone once sent `/guide map` to the bare wizard row and dropped the args
 * (TUI audit 2026-10-17). Lazy imports keep the slash cycles lazy. */
const BUILTIN_HANDLERS: Record<string, BuiltinHandler> = {
  settings: handleSettingsCommand,
  // Dynamic: theme-command → slash-pickers → slash cycle stays lazy.
  theme: async (args) => (await import("./theme-command")).handleThemeCommand(args),
  tour: async (args) => (await import("./tour-command")).handleTourCommand(args),
  undo: () => handleUndoCommand(),
  // TUI/rccs function sweep (2026-09-27) — lazy import, jail-free cycle.
  files: async (args) => (await import("./files-command")).handleFilesCommand(args),
  explain: async (args) => (await import("./files-command")).handleExplainCommand(args),
  sweep: async (args) => (await import("./sweep-command")).handleSweepCommand(args),
  evidence: async (args) => (await import("./evidence-command")).handleEvidenceCommand(args),
  // ADR-101: the session's local tool sandbox (lazy, like /files).
  sandbox: async (args) => (await import("./sandbox-command")).handleSandboxCommand(args),
  // ADR-102: the local search server behind web_search (lazy, like /sandbox).
  "search-server": async (args) => (await import("./search-server-command")).handleSearchServerCommand(args),
  // ADR-108 D-2: session worktrees (lazy, like /sandbox).
  worktree: async (args) => (await import("./worktree-command")).handleWorktreeCommand(args),
  btw: handleBtwCommand,
  statusline: handleStatuslineCommand,
  compact: handleCompactCommand,
  mode: handleModeCommand,
  code: handleCodeCommand,
  job: handleJobCommand,
  login: handleLoginCommand,
  acquisition: handleAcquisitionCommand,
  "decision-model": handleDecisionModelCommand,
};

/** Built-in direct handlers (settings/undo/compact/mode/code each
 * self-contained); null when the name is not a builtin row or alias. */
async function dispatchBuiltinVerbs(
  parsed: { name: string; args: string },
): Promise<SlashDispatchResult | null> {
  const row = stageRowName(parsed.name, "builtin");
  const handler = row ? ownHandler(BUILTIN_HANDLERS, row) : undefined;
  return handler ? handler(parsed.args) : null;
}

/** T5 topic-7b/8 ownership dispatcher (keeps dispatchBuiltinVerbs byte-shape
 * steady against the shipped baseline). */
const OWNER_OWNER_VERBS: Record<string, (args: string) => Promise<SlashDispatchResult>> = {};

async function dispatchOwnershipVerbs(
  parsed: { name: string; args: string },
): Promise<SlashDispatchResult | null> {
  const row = stageRowName(parsed.name, "owned");
  const handler = row ? ownHandler(OWNER_OWNER_VERBS, row) : undefined;
  return handler ? handler(parsed.args) : null;
}

function tupleJoin(parts: string[]): string {
  return parts.filter(Boolean).join(" · ");
}

/** Breach-class gauge glyph for quota/usage rows (T5 topic-17 wave F):
 * ✓ healthy, ⚠ approaching (ratio ≥ 0.75), ⛔ at/over limit. */
function gaugeGlyph(row: { current?: number; limit?: number }): string {
  const current = row.current ?? 0;
  const limit = row.limit ?? 0;
  if (limit <= 0) return "· ";
  const ratio = current / limit;
  if (ratio >= 1) return "⛔ ";
  if (ratio >= 0.75) return "⚠ ";
  return "✓ ";
}

function strProp(raw: Record<string, unknown>, key: string, fallback = ""): string {
  const v = raw[key];
  return typeof v === "string" ? v : fallback;
}

function grantPinnedCell(g: Record<string, unknown>): string {
  const pin = strProp(g, "version_pin");
  return pin ? `pin=${pin.slice(0, 8)}` : "";
}

function grantRowLine(g: Record<string, unknown>): string {
  const kind = strProp(g, "asset_kind", "?");
  const asset = strProp(g, "asset_id", "?");
  const grantor = strProp(g, "grantor_user_id");
  const expires = strProp(g, "expires_at");
  return `  ${tupleJoin([`${kind}:${asset}`, grantor, grantPinnedCell(g), expires])}`;
}

function usageRowLine(r: Record<string, unknown>): string {
  const model = String(r.model ?? r.group_by ?? "?");
  const prompt = typeof r.prompt_tokens === "number" ? `p=${r.prompt_tokens}` : "";
  const completion = typeof r.completion_tokens === "number" ? `c=${r.completion_tokens}` : "";
  const total = typeof r.total_tokens === "number" ? `Σ=${r.total_tokens}` : "";
  return `  ${tupleJoin([model, prompt, completion, total])}`;
}

// The ops verbs' texts are pure, in lib/ops-text.ts (the VS Code host prints
// them too, ADR-103 D-3).
async function loopsList(): Promise<SlashDispatchResult> {
  const payload = await getCompanionBridge().request("loops.list", {}, 15_000);
  actions.sendSystemNotice(opsText.loopsListText(payload));
  return { kind: "handled" };
}

async function loopsControl(sub: string, loopId: string): Promise<SlashDispatchResult> {
  const payload = await getCompanionBridge().request(`loops.${sub}`, { loop_id: loopId }, 15_000);
  actions.sendSystemNotice(opsText.loopControlText(loopId, sub, payload));
  return { kind: "handled" };
}

/** /loops detail <loop-id>: renders via the loops store so the card
 * auto-follows while the loop is live (batch 6, 2026-09-27). */
async function loopsDetail(loopId: string): Promise<SlashDispatchResult> {
  const { loopsActions } = await import("../stores/loops");
  await loopsActions.detail(loopId);
  return { kind: "handled" };
}

/** T5 topic-8: /loops [list|status <id>|detail <id>|stop <id>|pause <id>|resume <id>]. */
async function handleLoopsCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "loops: requires a live backend" };
  }
  const argv = args.trim().split(/\s+/).filter(Boolean);
  const sub = (argv[0] || "list").toLowerCase();
  const loopId = argv[1] ?? "";
  try {
    if (sub === "list") return await loopsList();
    if (sub === "status" || sub === "stop" || sub === "pause" || sub === "resume") {
      if (!loopId) {
        return { kind: "error", message: `usage: /loops ${sub} <loop-id>` };
      }
      return await loopsControl(sub, loopId);
    }
    if (sub === "detail") {
      if (!loopId) {
        return { kind: "error", message: "usage: /loops detail <loop-id>" };
      }
      return await loopsDetail(loopId);
    }
    return { kind: "error", message: opsText.LOOPS_UNKNOWN };
  } catch (err) {
    actions.sendSystemNotice(
      `loops: ${err instanceof Error ? err.message : String(err)}`,
    );
    return { kind: "handled" };
  }
}

async function tracesGet(traceId: string): Promise<SlashDispatchResult> {
  const payload = await getCompanionBridge().request("traces.get", { trace_id: traceId }, 15_000);
  actions.sendSystemNotice(opsText.traceGetText(traceId, payload));
  return { kind: "handled" };
}

async function tracesJob(jobId: string): Promise<SlashDispatchResult> {
  const payload = await getCompanionBridge().request("traces.job", { job_id: jobId }, 15_000);
  actions.sendSystemNotice(opsText.tracesJobText(jobId, payload));
  return { kind: "handled" };
}

const TRACE_COMPANION_VERBS = manifestVerbs("traces", "forwardVerbs");

/** `traces` is also the companion /trace alias: its verbs go there, not in
 * as a job id (TUI audit 2026-10-17). */
function tracesJobOrVerb(sub: string, args: string): Promise<SlashDispatchResult> {
  return TRACE_COMPANION_VERBS.has(sub) ? forwardToCompanion("trace", args.trim()) : tracesJob(sub);
}

/** T5 topic-8b: /traces [get <trace-id> | <job-id>]. */
async function handleTracesCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "traces: requires a live backend" };
  }
  const argv = args.trim().split(/\s+/).filter(Boolean);
  const sub = argv[0] ?? "";
  const arg = argv[1] ?? "";
  try {
    if (sub === "get") {
      if (!arg) return { kind: "error", message: "usage: /traces get <trace-id>" };
      return await tracesGet(arg);
    }
    if (sub) return await tracesJobOrVerb(sub, args);
    return { kind: "error", message: opsText.TRACES_USAGE };
  } catch (err) {
    actions.sendSystemNotice(`traces: ${err instanceof Error ? err.message : String(err)}`);
    return { kind: "handled" };
  }
}

async function provenanceJob(jobId: string): Promise<SlashDispatchResult> {
  const payload = await rpc(getCompanionBridge(), "provenance.job", { job_id: jobId }, 15_000);
  actions.sendSystemNotice(opsText.provenanceJobText(jobId, payload));
  return { kind: "handled" };
}

/** UMRS returns LineageGraph.to_dict — {nodes, edges, metrics, …}; reading
 * a `lineage` key always reported none (TUI audit 2026-10-17). */
async function provenanceLineage(jobId: string): Promise<SlashDispatchResult> {
  const payload = await rpc(getCompanionBridge(), "provenance.lineage", { job_id: jobId }, 15_000);
  actions.sendSystemNotice(opsText.provenanceLineageText(jobId, payload));
  return { kind: "handled" };
}

/** T5 topic-8b: /provenance <job-id> [lineage]. */
async function handleProvenanceCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "provenance: requires a live backend" };
  }
  const argv = args.trim().split(/\s+/).filter(Boolean);
  const jobId = argv[0] ?? "";
  const mode = (argv[1] || "").toLowerCase();
  if (!jobId) {
    return { kind: "error", message: opsText.PROVENANCE_USAGE };
  }
  try {
    if (mode === "lineage") return await provenanceLineage(jobId);
    if (!mode) return await provenanceJob(jobId);
    return { kind: "error", message: `provenance: unknown mode '${mode}' (use 'lineage')` };
  } catch (err) {
    actions.sendSystemNotice(`provenance: ${err instanceof Error ? err.message : String(err)}`);
    return { kind: "handled" };
  }
}

function adminUsageNotice(): SlashDispatchResult {
  actions.sendSystemNotice(
    "admin — owner-scope read-only rows:\n" +
      "  /admin rag-stats [project]                    · per-project corpus stats\n" +
      "  /admin corpus-attrition <project>             · read-only variance report\n" +
      "The next subs the users can call are never invoked: reads are always verified caller scope.",
  );
  return { kind: "handled" };
}

function adminKVParts(argv: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const tok of argv) {
    if (!tok.includes("=")) continue;
    const [key, value] = tok.split("=", 2);
    out[key] = value;
  }
  return out;
}

async function adminRagStats(projectId: string): Promise<SlashDispatchResult> {
  const bridge = getCompanionBridge();
  const payload = await rpc(bridge, "admin.rag_stats", projectId ? { project_id: projectId } : {}, 15_000);
  const lines = Object.entries(payload.stats ?? {}).slice(0, 14).map(([k, v]) => `  ${k} ${objectToLine(v)}`);
  actions.sendSystemNotice(lines.length > 0 ? `rag/stats${projectId ? ` (${projectId})` : ""}:\n${lines.join("\n")}` : "rag/stats — empty");
  return { kind: "handled" };
}

async function adminCorpusAttrition(projectId: string): Promise<SlashDispatchResult> {
  if (!projectId) return { kind: "error", message: "usage: /admin corpus-attrition <project-id>" };
  const bridge = getCompanionBridge();
  const payload = await rpc(bridge, "admin.corpus_attrition", { project_id: projectId }, 20_000);
  const lines = Object.entries(payload.report ?? {}).slice(0, 14).map(([k, v]) => `  ${k} ${objectToLine(v)}`);
  actions.sendSystemNotice(lines.length > 0 ? `corpus-attrition for ${projectId}:\n${lines.join("\n")}` : `corpus-attrition — no rows for ${projectId}`);
  return { kind: "handled" };
}

const ADMIN_SUBS: Record<string, (argv: string[], kv: Record<string, string>) => Promise<SlashDispatchResult>> = {
  "rag-stats": (argv) => adminRagStats(argv[1] ?? ""),
  "corpus-attrition": (argv) => adminCorpusAttrition(argv[1] ?? ""),
  "attrition": (argv) => adminCorpusAttrition(argv[1] ?? ""),
  // rag-profiles is gone: UMRS retired that CRUD (410 Gone), and memory had no
  // gateway location for the reflective-memory family (TUI audit 2026-10-17).
};

/** T5 topic-22 tail: /checkpoint [save|list] — manual session snapshot rows. */
async function handleCheckpointCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "checkpoint: requires a live backend" };
  }
  const argv = args.trim().split(/\s+/).filter(Boolean);
  const sub = (argv[0] || "list").toLowerCase();
  const bridge = getCompanionBridge();
  try {
    if (sub === "save") {
      const label = argv.slice(1).join(" ") || "manual";
      const result = (await bridge.request("save_checkpoint", { state: { note: label } }, 30_000)) as {
        checkpoint_id?: string;
      };
      actions.sendSystemNotice(
        `checkpoint saved${result.checkpoint_id ? ` → ${result.checkpoint_id}` : ""}`,
      );
      return { kind: "handled" };
    }
    if (sub === "list") {
      const result = (await bridge.request("list_checkpoints", {}, 30_000)) as {
        checkpoints?: Array<Record<string, unknown>>;
      };
      const rows = Array.isArray(result.checkpoints) ? result.checkpoints : [];
      if (rows.length === 0) {
        actions.sendSystemNotice("no checkpoints recorded for this session");
        return { kind: "handled" };
      }
      // CheckpointManager.list_checkpoints rows carry id/timestamp; reading
      // checkpoint_id/created_at showed '?' and no time (TUI audit 2026-10-17).
      const lines = rows.slice(0, 12).map((cp) => {
        const id = strProp(cp, "id", strProp(cp, "checkpoint_id", "?"));
        const agent = strProp(cp, "agent_id", "");
        const stamped = strProp(cp, "timestamp", strProp(cp, "created_at", ""));
        const type = strProp(cp, "checkpoint_type", "");
        return `  ${tupleJoin([id, agent, type, stamped])}`;
      });
      actions.sendSystemNotice(`checkpoints (${rows.length}):\n${lines.join("\n")}`);
      return { kind: "handled" };
    }
    return { kind: "error", message: "usage: /checkpoint [save [<note>]|list]" };
  } catch (err) {
    actions.sendSystemNotice(`checkpoint: ${err instanceof Error ? err.message : String(err)}`);
    return { kind: "handled" };
  }
}

/** T5 topic-14: /admin <sub> — owner-scope read-only operator stats. */
async function handleAdminCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "admin: requires a live backend" };
  }
  const argv = args.trim().split(/\s+/).filter(Boolean);
  const sub = (argv[0] || "").toLowerCase();
  const kv = adminKVParts(argv.slice(1));
  try {
    if (!sub) return adminUsageNotice();
    const handler = ownHandler(ADMIN_SUBS, sub);
    if (handler) return await handler(argv, kv);
    return {
      kind: "error",
      message: `admin: unknown sub — use rag-stats | corpus-attrition`,
    };
  } catch (err) {
    actions.sendSystemNotice(`admin: ${err instanceof Error ? err.message : String(err)}`);
    return { kind: "handled" };
  }
}

function ragFlagNumber(tokens: string[], prefix: string): number | undefined {
  const raw = tokens.find((tok) => tok.startsWith(prefix))?.slice(prefix.length) ?? "";
  if (!raw) return undefined;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function ragFlagString(tokens: string[], prefix: string): string | undefined {
  const raw = tokens.find((tok) => tok.startsWith(prefix))?.slice(prefix.length) ?? "";
  return raw || undefined;
}

function ragParseArgs(args: string): { question: string; top_k?: number; project_id?: string } {
  const tokens = args.trim().split(/\s+/).filter(Boolean);
  // `/rag query <q>` is the companion grammar; the verb is not question text.
  if (tokens[0]?.toLowerCase() === "query") tokens.shift();
  const question = tokens.filter((tok) => !tok.includes("=")).join(" ").trim();
  return {
    question,
    top_k: ragFlagNumber(tokens, "top_k="),
    project_id: ragFlagString(tokens, "project="),
  };
}

function ragPushSection(
  lines: string[],
  heading: string,
  rows: Array<Record<string, unknown>>,
  toLine: (row: Record<string, unknown>) => string = objectToLine,
): void {
  if (rows.length === 0) return;
  lines.push(`${heading} (${rows.length}):`);
  for (const row of rows.slice(0, 6)) lines.push(`  ${toLine(row)}`);
}

/** Like the bridge: UMRS RAGQueryRequest requires project_id, so a bare
 * `/rag <q>` defaults to the active project (it 422'd; TUI audit 2026-10-17). */
function ragParamsFor(parsed: ReturnType<typeof ragParseArgs>): Record<string, unknown> {
  const params: Record<string, unknown> = { query: parsed.question };
  if (parsed.top_k) params.top_k = parsed.top_k;
  const projectId = parsed.project_id || activeTab().project?.id;
  if (projectId) params.project_id = projectId;
  return params;
}

const RAG_SECTIONS: ReadonlyArray<string> = ["sources", "documents"];

function rowsAt(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

/** RAGQueryResponse.metadata (ADR-093 D-4) carries citations and abstained. */
function ragMetadata(payload: Record<string, unknown>): Record<string, unknown> {
  const meta = payload.metadata;
  return meta && typeof meta === "object" && !Array.isArray(meta) ? (meta as Record<string, unknown>) : {};
}

/** Spine citations are {label, source_id}; other shapes print as before. */
function ragCitationLine(row: Record<string, unknown>): string {
  if (typeof row.label === "string" && typeof row.source_id === "string") {
    return `${row.label} ${row.source_id}`;
  }
  return objectToLine(row);
}

function ragLinesFor(payload: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const meta = ragMetadata(payload);
  if (meta.abstained === true) lines.push("rag — abstained: the indexed sources do not answer this");
  if (typeof payload.answer === "string" && payload.answer) lines.push(payload.answer);
  const citations = rowsAt(payload.citations);
  ragPushSection(lines, "citations", citations.length ? citations : rowsAt(meta.citations), ragCitationLine);
  for (const key of RAG_SECTIONS) ragPushSection(lines, key, rowsAt(payload[key]));
  if (lines.length === 0) {
    lines.push("rag — no answer body (lane returned no rows honestly)");
  }
  return lines;
}

const RAG_COMPANION_VERBS = manifestVerbs("rag", "forwardVerbs");

/** T5 topic-9: /rag <question> [top_k=<n>] [project=<id>]. The document
 * verbs are the companion's /rag (they used to run as a query). */
async function handleRagCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "rag: requires a live backend" };
  }
  const verb = (args.trim().split(/\s+/)[0] ?? "").toLowerCase();
  if (RAG_COMPANION_VERBS.has(verb)) return forwardToCompanion("rag", args.trim());
  const parsed = ragParseArgs(args);
  if (!parsed.question) {
    return { kind: "error", message: "usage: /rag <question> [top_k=<n>] [project=<id>]" };
  }
  const bridge = getCompanionBridge();
  try {
    const payload = (await bridge.request("rag.query", ragParamsFor(parsed), 30_000)) as {
      answer?: string;
      citations?: Array<Record<string, unknown>>;
      sources?: Array<Record<string, unknown>>;
      documents?: Array<Record<string, unknown>>;
    };
    actions.sendSystemNotice(ragLinesFor(payload).join("\n"));
    return { kind: "handled" };
  } catch (err) {
    actions.sendSystemNotice(`rag: ${err instanceof Error ? err.message : String(err)}`);
    return { kind: "handled" };
  }
}

function objectToLine(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "boolean" || typeof v === "number") return String(v);
  return JSON.stringify(v).slice(0, 120);
}

/** T5 topic-7b/8 ownership verbs — fills the second dispatcher (keeps
 * dispatchBuiltinVerbs byte-shape steady against the shipped baseline). */
Object.assign(OWNER_OWNER_VERBS, {
  // ADR-086 — lazy import (files-command pattern); write verbs ride the
  // server's four-rung roster ladder (amendment 2026-10-19).
  circles: async (args: string) => (await import("./circles-command")).handleCirclesCommand(args),
  loops: handleLoopsCommand,
  traces: handleTracesCommand,
  provenance: handleProvenanceCommand,
  rag: handleRagCommand,
  admin: handleAdminCommand,
  checkpoint: handleCheckpointCommand,
});

/** Management workflow/loop verbs → cards (M1/M2), null to fall through. */
async function dispatchManagedVerbs(
  parsed: { name: string; args: string },
): Promise<SlashDispatchResult | null> {
  if (parsed.name === "workflow") {
    const managed = await dispatchWorkflowManagement(parsed.args);
    if (managed) return managed;
  }
  // The manifest's intercept "managed" rows (loop, session, knowledge, graph).
  const family = stageRowName(parsed.name, "managed");
  return family ? dispatchLoopAndSessionManagement(family, parsed.args) : null;
}

async function applyWaveIntercepts(
  parsed: { name: string; args: string },
): Promise<SlashDispatchResult | null> {
  // Only the names the manifest gives the wave stage (intercept "wave").
  if (!isWaveIntercepted(parsed.name)) return null;
  const { handleWaveIntercepts } = await import("./wave-intercepts");
  // Wave D/E/F/H intercepts live in wave-intercepts.ts (keeps this dispatcher
  // below the complexity line). See wave-intercepts for health/authz, catalog,
  // codeview, /project switch, /sessions.
  return handleWaveIntercepts(parsed);
}

/** S2: bare /workflow opens the workflows *pane* (was: temporal list overlay). */
function handleWorkflowPane(parsed: { name: string; args: string }): SlashDispatchResult | null {
  if (parsed.name !== "workflow" || parsed.args.trim() !== "") return null;
  if (isOfflineBackend()) {
    return { kind: "error", message: "workflows require a live backend" };
  }
  actions.openPane("work");
  return { kind: "handled" };
}

/** Fast path: local entries work without the bridge (also stops test spawns). */
async function resolveCatalogEntry(parsed: { name: string; args: string }): Promise<SlashCommandEntry | null> {
  const localEntry = findEntry(localSlashCommands(), parsed.name);
  if (localEntry) return localEntry;
  const catalog = await loadSlashCatalog();
  return findEntry(catalog, parsed.name) ?? null;
}

/** A bareOnly row hands its args form to the companion (none offline). */
function forwardsArgs(entry: SlashCommandEntry, parsed: { name: string; args: string }): boolean {
  return Boolean(entry.bareOnly && parsed.args.trim()) && !isOfflineBackend();
}

async function runLocalEntry(entry: SlashCommandEntry): Promise<SlashDispatchResult | null> {
  if (!entry.localRun) return null;
  try {
    await entry.localRun();
    return { kind: "handled" };
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : String(err) };
  }
}

function outputToLines(result: { output?: Array<{ text?: string } | string> }): string[] {
  const lines: string[] = [];
  for (const item of result.output ?? []) {
    if (typeof item === "string") lines.push(item);
    else if (item && typeof item === "object" && item.text) lines.push(item.text);
  }
  return lines;
}

const TRIGGERED_RE = /^workflow triggered: ([A-Za-z0-9][\w.:-]*)$/;

/**
 * `/workflow trigger|run` goes through the companion (one parser for the
 * flags); the execution it starts joins the work pane like a `t` run —
 * stamped with `--project` when given, else the active project the
 * companion defaults to.
 */
function adoptTriggeredRun(parsed: { name: string; args: string }, lines: string[]): void {
  if (parsed.name !== "workflow") return;
  const tokens = parsed.args.trim().split(/\s+/);
  if (!["trigger", "run"].includes((tokens[0] ?? "").toLowerCase())) return;
  const id = lines.map((line) => TRIGGERED_RE.exec(line.trim())?.[1]).find(Boolean);
  if (!id) return;
  const flag = tokens.indexOf("--project");
  const named = flag >= 0 ? tokens[flag + 1] : undefined;
  const projectId = (named ?? activeTab().project?.id ?? activeTab().identityProjectId) || undefined;
  void umrsWorkflowActions.adoptExecution(id, projectId);
}

/** Generic catalog command: bridge round-trip + output/state application. */
async function bridgeDispatchSlash(parsed: { name: string; args: string }, line: string): Promise<SlashDispatchResult> {
  const bridge = getCompanionBridge();
  const result = await trackSlashInFlight(() =>
    rpc(bridge, "dispatch_slash_command", { command: line.trim() }, 60_000),
  );

  if (result.cleared) actions.clearChat();
  // /retry cut its turn from the session: drop the same rows first.
  actions.dropTranscriptTurns(stringList(result.dropped_turn_ids));
  const lines = outputToLines(result);
  for (const text of lines) actions.sendSystemNotice(text);
  if (parsed.name === "workflow" && parsed.args.toLowerCase().startsWith("list")) {
    void umrsWorkflowActions.loadCatalog();
  }
  adoptTriggeredRun(parsed, lines);
  const patch = mapBridgeStateToWorkspace(result.state, result.dashboard);
  applyBridgeStatePatch(patch);
  sendFollowUpTurns(stringList(result.follow_up_messages));
  if (!result.handled && lines.length === 0) {
    return { kind: "error", message: `Command not handled: ${line.trim()}` };
  }
  return { kind: "handled" };
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

/** The project-context preamble sendChatMessage adds to the wire text; a
 * /retry hands the stored (wire) text back, and it must not be doubled. */
const PROJECT_CONTEXT_PREFIX = "[Project context] ";

function withoutProjectContext(text: string): string {
  if (!text.startsWith(PROJECT_CONTEXT_PREFIX)) return text;
  const cut = text.indexOf("\n\n");
  return cut < 0 ? text : text.slice(cut + 2);
}

/**
 * Prompts a companion command handed back as chat turns (/code edit,
 * /agent chat, /search, /retry). Each goes the typed-message path, so it
 * streams, is cancellable, gets its turn id, and a second one queues
 * behind the first (TUI audit r2 followup). They are server-built relays:
 * they never consume an armed teamup one-shot or name the tab.
 */
function sendFollowUpTurns(messages: string[]): void {
  actions.sendFollowUps(messages.map(withoutProjectContext));
}

/** Catalog/bridge final dispatch (resolution → prompt → local → bridge). */
async function dispatchViaCatalog(
  parsed: { name: string; args: string },
  line: string,
): Promise<SlashDispatchResult> {
  const entry = await resolveCatalogEntry(parsed);
  if (!entry) {
    return { kind: "error", message: `Unknown slash command: /${parsed.name}` };
  }
  if (needsArgPrompt(entry, parsed.args)) {
    return { kind: "insert", text: `/${entry.name} ` };
  }
  const localResult = forwardsArgs(entry, parsed) ? null : await runLocalEntry(entry);
  if (localResult) return localResult;
  try {
    return await bridgeDispatchSlash(parsed, line);
  } catch (err) {
    return slashError(err);
  }
}

/** Parsed-command chain: project → builtins → workflow pane → mgmt → catalog. */
async function dispatchParsedCommand(
  parsed: { name: string; args: string },
  line: string,
): Promise<SlashDispatchResult | null> {
  if (parsed.name === "project") {
    return dispatchProjectCommand(parsed.args);
  }
  const builtin = await dispatchBuiltinVerbs(parsed);
  if (builtin) return builtin;
  const owned = await dispatchOwnershipVerbs(parsed);
  if (owned) return owned;
  const pane = handleWorkflowPane(parsed);
  if (pane) return pane;
  const managed = await dispatchManagedVerbs(parsed);
  if (managed) return managed;
  return dispatchViaCatalog(parsed, line);
}

/**
 * Python-registry aliases whose canonical v3 row runs locally (TUI audit
 * 2026-10-17): the bridge does not export them, and a bare alias match would
 * run the local row and drop the args, so they become the canonical command
 * before any intercept sees them. `/resume <id>` is `/sessions resume <id>`.
 * The rewrites are the manifest rows' `canon` fields (plan H-5).
 */
export function canonicalizeSlashAlias(
  parsed: { name: string; args: string },
  line: string,
): { parsed: { name: string; args: string }; line: string } {
  const canon = aliasCanon(parsed.name);
  if (!canon) return { parsed, line };
  const next = canon(parsed.args);
  return { parsed: next, line: `/${next.name}${next.args ? ` ${next.args}` : ""}` };
}

export async function dispatchSlashCommand(rawLine: string): Promise<SlashDispatchResult> {
  const raw = parseSlashLine(rawLine);
  if (!raw) {
    return { kind: "error", message: "Slash commands must start with /" };
  }
  const { parsed, line } = canonicalizeSlashAlias(raw, rawLine);
  const waveHit = await applyWaveIntercepts(parsed);
  if (waveHit) return waveHit;
  const result = await dispatchParsedCommand(parsed, line);
  return result ?? { kind: "error", message: `Unknown slash command: /${parsed.name}` };
}

export function resetSlashCatalogCache() {
  catalogCache = null;
  catalogPromise = null;
}

/** @internal test helper: the rows each name-keyed dispatch stage serves
 * (pinned to the manifest's intercept stages in slash-reachability). */
export function _testOnly_stageTables(): Record<"builtin" | "owned" | "managed", string[]> {
  return {
    builtin: Object.keys(BUILTIN_HANDLERS),
    owned: Object.keys(OWNER_OWNER_VERBS),
    managed: Object.keys(MGMT_FAMILY_HANDLERS),
  };
}

/** @internal test helper */
export function _testOnly_workspaceDemoFlag(): boolean {
  return Boolean(workspace.demoMode);
}
