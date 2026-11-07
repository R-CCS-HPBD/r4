/**
 * `/teamup` texts (T5 wave; ADR-092 slices 3–4): the notice each verb prints.
 * Pure, so the VS Code host prints them the same way (ADR-103 D-3);
 * `stores/teams.ts` and `commands/wave-intercepts.ts` use them. The list and
 * status cards are what the console's Work pane shows as rows.
 */

import { formatTokens, planEventLine, type PlanEventLine, type TeamRunRow } from "./plan-live";
import { teamEventNoticeLine, type TeamEventLine } from "./team-live";

type Row = Record<string, unknown>;

/** Runs of whitespace (a RegExp object: lizard misreads some regex literals). */
const WHITESPACE_RUN = new RegExp("\\s+", "g");

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => String(v)) : [];
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? (value as Row[]) : [];
}

export const TEAMUP_VERBS =
  "on [crew|plan:<template>] · off · create <name> [roles=…] [goal] · plans · list · " +
  "status|logs|report|run|pause|resume|cancel|tailor <sid> · engines · crews · hardware";

export function teamupHelpText(): string {
  return `/teamup verbs: ${TEAMUP_VERBS}`;
}

export function teamupUnknownText(first: string): string {
  return `teamup: unknown sub "${first}" — use ${TEAMUP_VERBS}`;
}

/** `teamup.run` (async since 2026-09-27: the iteration starts on the server). */
export function teamRunText(sessionId: string, result: Row): string {
  if (result.started) return `teamup ${sessionId} — iteration ${String(result.expected_iteration ?? "?")} STARTED (follow live below)`;
  return `teamup ${sessionId} → ${str(result.status)}`;
}

/** `teamup.create`. */
export function teamCreateText(result: Row): string {
  const state = result.started ? "started" : str(result.status);
  return `teamup create ${str(result.name)} → ${state} (${str(result.session_id).slice(0, 12)} roles:${strings(result.roles).join(",")})`;
}

/** `teamup.logs`. */
export function teamLogsText(sessionId: string, result: Row): string {
  const entries = strings(result.entries);
  if (entries.length === 0) return `teamup logs ${sessionId}: no entries yet.`;
  return `teamup logs ${sessionId} (latest ${entries.length}):\n${entries.join("\n")}`;
}

/** `teamup.tailor`: the root agent wrote the role prompts for this project. */
export function teamTailorText(sessionId: string, result: Row): string {
  const tailored = strings(result.tailored);
  const kept = strings(result.kept_default);
  const plural = tailored.length === 1 ? "" : "s";
  return (
    `teamup tailor ${sessionId} — root ${str(result.root, "?")} wrote ${tailored.length} prompt${plural}` +
    (tailored.length ? ` ({${tailored.join(",")}})` : "") +
    (kept.length ? `; kept template: {${kept.join(",")}}` : "") +
    (tailored.length === 0 ? " — answer had no usable prompt blocks; templates stand" : "")
  );
}

/** `teamup.engines`. */
export function teamEnginesText(result: Row): string {
  const list = rows(result.engines);
  if (list.length === 0) return "teamup engines: no engines registered now (ADR-020)";
  const lines = list.map((e) => {
    const note = e.note ? ` · ${str(e.note)}` : "";
    return `  ${e.available ? "✓" : "✖"} ${str(e.name)}@v${str(e.version)} [${str(e.trust_tier)}]${note}`;
  });
  return `teamup engines (${list.length}):\n${lines.join("\n")}`;
}

/** `teamup.crews` (ADR-092 slice 4: hpc is the teamup preset; the rest name a plan template). */
export function teamCrewsText(result: Row): string {
  const list = rows(result.crews);
  if (list.length === 0) return "teamup crews: none registered now";
  const lines = list.map((c) => {
    const runs = c.template ? `plan ${str(c.template)}` : "teamup session";
    const aliases = strings(c.aliases);
    const also = aliases.length ? ` (also ${aliases.join(", ")})` : "";
    const what = c.description ? ` — ${String(c.description).split(". ")[0]!.slice(0, 70)}` : "";
    return `  △ ${str(c.name)} → ${runs}${also}${what}`;
  });
  return `teamup crews (${list.length}) — /teamup on <crew>:\n${lines.join("\n")}`;
}

/** `teamup.hardware`. */
export function teamHardwareText(result: Row): string {
  const list = rows(result.profiles);
  if (list.length === 0) return "teamup hardware: no profiles registered";
  const lines = list.map((h) => `  ▣ ${str(h.id)}${h.name ? ` — ${str(h.name)}` : ""}${h.description ? ` · ${str(h.description)}` : ""}`);
  return `teamup hardware (${list.length}):\n${lines.join("\n")}`;
}

/** `teamup.report`. */
export function teamReportText(sessionId: string, result: Row): string {
  const text = str(result.report);
  return text ? `teamup report ${sessionId}:\n${text}` : `teamup report ${sessionId}: (empty)`;
}

/** `teamup.pause|resume|cancel`. */
export function teamLifecycleText(verb: string, result: Row): string {
  return `teamup ${verb}: ${str(result.session_id)} → ${str(result.status)}`;
}

/** `team.plan_templates` (ADR-092 slice 3): what `/teamup on plan:<name>` can arm. */
export function teamPlansText(result: Row): string {
  const list = rows(result.templates);
  if (list.length === 0) return "team plan templates: none";
  const lines = list.map((row) => {
    const params = (row.params && typeof row.params === "object" ? row.params : {}) as Record<string, { required?: boolean } | null>;
    const required = Object.entries(params)
      .filter(([, spec]) => spec?.required)
      .map(([key]) => key);
    return `  ▸ ${str(row.name)}${required.length ? ` (needs ${required.join(", ")})` : ""} — ${String(row.description ?? "").slice(0, 80)}`;
  });
  return `team plan templates (${list.length}) — /teamup on plan:<name>:\n${lines.join("\n")}`;
}

/** `teamup.list` as rows (the console's Work pane teams section). */
export function teamListText(result: Row): string {
  const list = rows(result.sessions);
  if (list.length === 0) return "teams: none today — /teamup on <crew> arms the next message as a team goal";
  const lines = list.map((s) => {
    const max = typeof s.max_iterations === "number" ? `/${s.max_iterations}` : "";
    const roles = strings(s.roles);
    const agents = roles.length ? ` · ${roles.join(",")}` : "";
    return `  ${str(s.session_id).slice(0, 12)} · ${str(s.name)} · ${str(s.status)} · it ${String(s.iteration ?? 0)}${max}${agents}`;
  });
  return `teams (${list.length}):\n${lines.join("\n")}`;
}

/** `teamup.status` as a card: the session line and one line per agent. */
export function teamStatusText(result: Row): string {
  const head = `team ${str(result.session_id)} · ${str(result.name)} · ${str(result.status)} · iteration ${String(result.iteration ?? 0)}/${String(result.max_iterations ?? "?")}`;
  const crew = result.crew_pack ? ` · crew ${str(result.crew_pack)}` : "";
  const agents = Object.entries((result.agents ?? {}) as Record<string, Row>).map(([role, a]) => {
    const task = a.current_task ? ` — ${str(a.current_task).slice(0, 60)}` : "";
    const model = a.model ? ` · ${str(a.model)}` : "";
    const errors = strings(a.errors).length ? ` · ${strings(a.errors).length} error(s)` : "";
    return `  ${role}: ${str(a.status)}${model}${errors}${task}`;
  });
  return [head + crew, ...agents].join("\n");
}

// ── arming (`/teamup on|off`) and the armed fire ─────────────────────────

export const TEAMUP_PARALLEL_RANGE = "teamup on: parallel width must be an integer 1-8";
export const PLAN_NAME_NEEDED = "teamup on plan:<template> — name a template (/teamup plans lists them)";

/** `/teamup on hpc [width]` armed. */
export function crewArmedText(crew: string, parallel: number): string {
  const width = parallel > 1 ? `${crew}, ${parallel} parallel roles` : crew;
  return `teamup armed (${width}) — your NEXT chat message starts a team on that text (then auto-disarm). /teamup off cancels.`;
}

/** `/teamup on plan:<name> [k=v …]` armed. */
export function planArmedText(name: string, args: Record<string, string>): string {
  const given = Object.keys(args);
  const shown = given.length ? `; ${given.map((k) => `${k}=${args[k]}`).join(" ")}` : "";
  return (
    `team plan armed (${name}${shown}) — ` +
    "your NEXT chat message is its goal; you approve the plan's card before anything runs (then auto-disarm). /teamup off cancels."
  );
}

export function planArgNotNameValueText(name: string, token: string): string {
  return `teamup on plan:${name}: "${token}" is not name=value — the goal is your NEXT message`;
}

export function planTemplatesUnavailableText(name: string, message: string): string {
  return `teamup on plan:${name}: templates unavailable — ${message}`;
}

export function planNoTemplateText(name: string, names: string[]): string {
  return `teamup on plan: no template "${name}" — available: ${names.join(", ") || "(none)"}`;
}

export function crewsUnavailableText(crew: string, message: string): string {
  return `teamup on ${crew}: crews unavailable — ${message}`;
}

export function crewMustBeText(names: string[]): string {
  return `teamup on: crew must be ${names.join("|") || "hpc"}, or plan:<template>`;
}

export function teamupOffText(wasArmed: boolean): string {
  return wasArmed ? "teamup disarmed — next message stays a normal turn" : "teamup arm was not set";
}

/** The armed hpc crew's `teamup.start` answer. */
export function crewStartText(crew: string, accepted: boolean): string {
  return accepted
    ? `⚙ team bootstrap accepted (${crew}) — the session opens when ready, follow live in the Work pane`
    : "teamup start failed — see the notice above";
}

/** `team.plan_run` refused: the card was rejected, the plan has problems, or the bridge said why. */
export function planRefusalText(template: string, reply: Row | undefined): string {
  if (reply?.status === "rejected") return `plan ${template} not started — the card was not approved (nothing ran)`;
  const problems = strings(reply?.problems);
  if (problems.length) {
    return `plan ${template} not started — ${problems.length} problem${problems.length === 1 ? "" : "s"}:\n${problems.map((p) => `  · ${p}`).join("\n")}`;
  }
  return `plan ${template} not started — ${str(reply?.error, "no run id returned")}`;
}

/** `team.plan_run` accepted. */
export function planStartedText(template: string, reply: Row): string {
  return `⚙ plan ${str(reply.plan, template)} started (${str(reply.run_id)}) — agents stream below; Work pane → teams (Enter result, c cancel)`;
}

/** The `team_started` event's row. */
export function teamStartedText(event: Row): string {
  return `⚙ ${str(event.session_name)} [${str(event.crew)}] — roles {${strings(event.roles).join(",")}} — follow live (Work pane, Enter)`;
}

/** A team start that failed before any session existed (`team_start_error`). */
export function teamStartErrorText(event: Row): string {
  const goal = event.goal ? ` (${String(event.goal).slice(0, 80)})` : "";
  return `team start failed${goal} — ${str(event.error_text, "unknown")}`;
}

/** `team_started` with a failed tailor: baseline prompts, the reason named. */
export function tailorDegradedText(degraded: unknown): string {
  return `⚠ prompt tailoring skipped — team runs baseline prompts (tailor failed: ${String(degraded).slice(0, 160)})`;
}

// ── live rows (team_event / plan events) ─────────────────────────────────

/** A running team's one row: name, iteration, the latest event. */
export function teamProgressText(name: string, iteration: number, maxIterations: number | undefined, last: TeamEventLine | undefined): string {
  const tail = last ? teamEventNoticeLine(last) : "started";
  return `team ${name} — iter ${iteration}${maxIterations ? `/${maxIterations}` : ""} · ${tail}`;
}

/** `run_finished`: failed, finished with agent errors, or done (continues / its status). */
export function runFinishedText(event: TeamEventLine, name: string): string {
  const iter = event.iteration;
  const errors = Array.isArray(event.errors) ? event.errors : [];
  if (event.error_text) return `team ${name} — run FAILED (iter ${iter}): ${event.error_text.slice(0, 200)}`;
  if (errors.length > 0) {
    const lines = errors.map((e) => `  ${e.role ?? "?"}: ${(e.error ?? "").replace(WHITESPACE_RUN, " ").slice(0, 160)}`);
    const plural = errors.length === 1 ? "" : "s";
    return `team ${name} — iteration ${iter} done (${event.status ?? "?"}), ${errors.length} agent error${plural}:\n${lines.join("\n")}`;
  }
  return event.continued ? `team ${name} — iteration ${iter} done (continues)` : `team ${name} — iteration ${iter} done (${event.status ?? "complete"})`;
}

/** A plan run's row after an agent result: counts, tokens, the latest result. */
export function planResultRowText(name: string, row: TeamRunRow | undefined, lines: string[]): string {
  const ok = row?.agents?.ok ?? 0;
  const nulls = row?.agents?.null ?? 0;
  const tokens = row?.cost?.tokens ? ` · ${formatTokens(row.cost.tokens)}` : "";
  return `plan ${name} — ${ok} ok${nulls ? ` · ${nulls} null` : ""}${tokens} · latest: ${lines[lines.length - 1] ?? ""}`;
}

/** A plan's gate asks the human (its approval card follows). */
export function planGateText(name: string, event: PlanEventLine): string {
  return `⏸ plan ${name}: ${planEventLine(event)} (answer the approval card)`;
}

/** `plan_finished`: the run's closing row. */
export function planFinishedText(name: string, event: PlanEventLine, lines: string[]): string {
  const error = event.error ? ` — ${event.error.slice(0, 200)}` : "";
  return `⏺ plan ${name} — ${planEventLine(event)}${error} · ${lines.length} agent result${lines.length === 1 ? "" : "s"} (Work pane → Enter for the result)`;
}
