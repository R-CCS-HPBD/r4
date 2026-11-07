/**
 * The texts of the ops slash verbs: `/loops`, `/traces`, `/provenance`,
 * `/sweep` (T5 topic-8/8b; sweep batch 3). Pure, so the VS Code host prints
 * them the same way (ADR-103 D-3); `commands/slash.ts` and
 * `commands/sweep-command.ts` use them.
 */

type Row = Record<string, unknown>;

function strProp(raw: Row, key: string, fallback = ""): string {
  const v = raw[key];
  return typeof v === "string" ? v : fallback;
}

function tupleJoin(parts: string[]): string {
  return parts.filter(Boolean).join(" · ");
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? (value as Row[]) : [];
}

/** One value on a key/value line (objects cut at 120 characters). */
export function objectToLine(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "boolean" || typeof v === "number") return String(v);
  return JSON.stringify(v).slice(0, 120);
}

function keyValueLines(payload: unknown): string {
  const entries = Object.entries(payload && typeof payload === "object" ? (payload as Row) : {});
  return entries
    .slice(0, 18)
    .map(([k, v]) => `  ${k} ${objectToLine(v)}`)
    .join("\n");
}

/** `<title>:` and up to 18 key/value lines (a one-shot detail view). */
export function keyValueText(title: string, payload: unknown): string {
  return `${title}:\n${keyValueLines(payload)}`;
}

// ── /loop help (maintainer 2026-11-03: "loop command should inform more
// kindly on how to use it") ─────────────────────────────────────────────

/** The slash picker's one line for /loop. */
export const LOOP_PICKER_HELP = "Research loops: start <goal> · list · status <id> · pause|resume|stop <id> — /loop help explains";

/** Verbs `/loop help` answers to (and a bare `?`). */
export const LOOP_HELP_VERBS = new Set(["help", "-h", "--help", "?"]);

const LOOP_HELP_LINES = [
  "/loop — research loops: R4 works on one research goal in iterations",
  "(plan → run workflows → check the evidence → next step) until the goal is",
  "met, the iteration budget is spent, or you stop it.",
  "",
  "  /loop start <goal>        start one, for example:",
  '                            /loop start "Screen MOF-5 analogues for CO2 uptake"',
  "                            options: --project <id> · --max-iterations <n>",
  "  /loop  ·  /loop list      your loops and where each one is",
  "  /loop status <id>         one loop: its iterations and findings",
  "  /loop pause <id>  ·  /loop resume <id>  ·  /loop stop <id>",
  "",
  "Good to know:",
  "  · Not a timer: /loop does not repeat a prompt on an interval.",
  "  · It runs on the project's runtime (UMRS on the server by default), so",
  "    the project must exist there: /project register <id> once.",
  "  · Models: every start says which one (its model: line). /loop start runs",
  "    the server's planned engine (workflows plus knowledge, no chat model).",
  '    A loop you ask for in chat ("start a research loop on …") uses your',
  "    /model choice where this machine drives it (local runtime, plan runs);",
  "    an agentic loop on UMRS uses the site's first ready chat endpoint (or",
  "    UMRS_AGENT_MODEL). Loops do not use the svc-* service models.",
  "  · /loops shows the same loops as plain text (adds detail <id>).",
];

/** The friendly `/loop` help; `wrong` names a verb that was not understood. */
export function loopHelpText(wrong = ""): string {
  const lead = wrong ? [`/loop: "${wrong}" is not a /loop verb. Here is what it does:`, ""] : [];
  return [...lead, ...LOOP_HELP_LINES].join("\n");
}

/** `/loop <verb>` without the loop id it needs. */
export function loopNeedsIdText(verb: string): string {
  return `/loop ${verb} needs a loop id: /loop ${verb} <loop-id> (ids: /loop list)`;
}

export const LOOP_START_NEEDS_GOAL =
  '/loop start needs a research goal, for example: /loop start "Screen MOF-5 analogues for CO2 uptake" (/loop help for more)';

// ── /loops ──────────────────────────────────────────────────────────────

export const LOOPS_CONTROL_VERBS = new Set(["status", "stop", "pause", "resume"]);
export const LOOPS_UNKNOWN = "loops: unknown sub — use list | status <id> | detail <id> | stop <id> | pause <id> | resume <id>";

export function loopsRowLine(l: Row): string {
  const iter = typeof l.current_iteration === "number" ? `#${l.current_iteration}` : "";
  const goal = strProp(l, "research_goal").slice(0, 48);
  return `  ${tupleJoin([strProp(l, "loop_id", "?"), strProp(l, "status"), strProp(l, "phase"), iter, goal])}`;
}

/** `loops.list` → the notice. */
export function loopsListText(payload: unknown): string {
  const list = rows((payload as Row | null)?.loops);
  if (list.length === 0) return "loops — none visible to this caller";
  return `loops (${list.length}):\n${list.map(loopsRowLine).join("\n")}`;
}

/** `loops.<sub>` → the notice. */
export function loopControlText(loopId: string, sub: string, payload: unknown): string {
  return `loop ${loopId} → ${sub} (${strProp((payload ?? {}) as Row, "status", sub)})`;
}

// ── /traces ─────────────────────────────────────────────────────────────

export const TRACES_USAGE = "usage: /traces [get <trace-id>|<job-id>]";

export function tracesRowLine(t: Row): string {
  const started = strProp(t, "started_at").slice(0, 19);
  const ended = strProp(t, "ended_at").slice(0, 19);
  return `  ${tupleJoin([strProp(t, "trace_id", "?"), strProp(t, "status"), strProp(t, "step_name"), started, ended])}`;
}

/** `traces.get` → the notice. */
export function traceGetText(traceId: string, payload: unknown): string {
  return `trace ${traceId}:\n${keyValueLines(payload)}`;
}

/** `traces.job` → the notice. */
export function tracesJobText(jobId: string, payload: unknown): string {
  const list = rows((payload as Row | null)?.traces);
  if (list.length === 0) return `traces — none for job ${jobId}`;
  return `traces for job ${jobId} (${list.length}):\n${list.map(tracesRowLine).join("\n")}`;
}

// ── /provenance ─────────────────────────────────────────────────────────

export const PROVENANCE_USAGE = "usage: /provenance <job-id> [lineage]";

/** `provenance.job` → the notice. */
export function provenanceJobText(jobId: string, payload: unknown): string {
  return `provenance — job ${jobId}:\n${keyValueLines(payload)}`;
}

export function lineageNodeLine(node: Row): string {
  const step = strProp(node, "step_name", strProp(node, "trace_id", "?"));
  const secs = typeof node.duration_seconds === "number" ? `${node.duration_seconds}s` : "";
  return `  ${tupleJoin([step, strProp(node, "step_type"), strProp(node, "status"), secs])}`;
}

/** `provenance.lineage` (LineageGraph.to_dict: nodes, edges, …) → the notice. */
export function provenanceLineageText(jobId: string, payload: unknown): string {
  const p = (payload ?? {}) as Row;
  const nodes = rows(p.nodes);
  const edges = Array.isArray(p.edges) ? p.edges.length : 0;
  if (nodes.length === 0) return `provenance lineage — none for job ${jobId}`;
  const lines = nodes.slice(0, 20).map(lineageNodeLine);
  return `provenance lineage — job ${jobId} (${nodes.length} steps, ${edges} edges):\n${lines.join("\n")}`;
}

// ── /sweep ──────────────────────────────────────────────────────────────

export const SWEEP_HELP =
  "sweep — HPC sweep templates:\n  /sweep list              inventory the pack\n  /sweep preview <name>    dry-render the default combo (no files written)\n  submit path: r4 sweep render <name> --out <dir> (CLI)";
export const SWEEP_PREVIEW_USAGE = "sweep preview: give a template name — /sweep preview <name>";

/** `sweep.templates` → the notice. */
export function sweepListText(out: unknown): string {
  const list = rows((out as Row | null)?.templates);
  if (list.length === 0) return "sweep: no templates in the pack (r4/src/r4/templates/sweeps)";
  const lines = list.map((row) => {
    const req = (Array.isArray(row.required_params) ? row.required_params : []).join(",") || "—";
    return `  △ ${String(row.name ?? "")} — ${String(row.description ?? "")} [requires: ${req}]`;
  });
  return `sweep templates (${list.length}):\n${lines.join("\n")}`;
}

/** `sweep.preview` → the notice. */
export function sweepPreviewText(name: string, out: unknown): string {
  const o = (out ?? {}) as Row;
  const req = (Array.isArray(o.required_params) ? o.required_params : []).join(", ");
  return (
    `sweep preview ${String(o.name ?? name)} (dry render of the default combo)\n` +
    (req ? `  requires: ${req}\n` : "") +
    `  job: ${String(o.job_name ?? "?")}\n` +
    `${String(o.script ?? "(empty script)")}`
  );
}
