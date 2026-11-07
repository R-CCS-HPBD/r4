/**
 * `/teamup` (also `/team`, `/teams`) as the console answers it (ADR-103
 * VP-4b): the request verbs, each one RPC and the console's notice
 * (`lib/teamup-text.ts`), as an output card; `list` and `status` print the
 * rows the console's Work pane shows. Part 2: `on|off` arms the NEXT message
 * as a team goal (a plan run through its approval card, or the hpc crew's
 * start), and `TeamLive` folds the bridge's `team_event` lines into one
 * keyed row per team and per plan run, as the console's notices. No vscode import.
 */

import * as text from "../../../packages/r4-tui-v3/src/lib/teamup-text";
import {
  PLAN_RESULT_KINDS,
  agentResultLines,
  appendPlanEvent,
  applyPlanEvent,
  isPlanEvent,
  type PlanEventLine,
  type TeamRunRow,
} from "../../../packages/r4-tui-v3/src/lib/plan-live";
import type { TeamEventLine } from "../../../packages/r4-tui-v3/src/lib/team-live";

/** team.plan_run replies after the human answers the plan's card (MD-5). */
const PLAN_CARD_TIMEOUT_MS = 15 * 60_000;

/** What `/teamup on` armed: the hpc crew's start, or a plan template. */
export type TeamArm = { kind: "crew"; crew: string; parallel: number } | { kind: "plan"; template: string; args: Record<string, string> };

/** The console's status chip for an armed team. */
export function teamArmLabel(arm: TeamArm | null): string {
  if (!arm) return "";
  if (arm.kind === "plan") return `team:on · plan:${arm.template}`;
  const width = arm.parallel > 1 ? " ×" + arm.parallel : "";
  return "team:on · " + arm.crew + width;
}

export type OutputLine = { kind: "info" | "error"; text: string };

export interface TeamupDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  output(command: string, lines: OutputLine[]): void;
  /** A notice row; a `key` updates the same row in place. */
  notice(text: string, key?: string): void;
  /** The armed chip (`team:on · …`, "" when disarmed). */
  armed(label: string): void;
}

type Row = Record<string, unknown>;

/** One session verb: its RPC and its text. */
const SESSION_VERBS: Record<string, { method: string; params?: Row; render: (sid: string, result: Row) => string }> = {
  status: { method: "teamup.status", render: (_sid, r) => text.teamStatusText(r) },
  logs: { method: "teamup.logs", params: { limit: 12 }, render: text.teamLogsText },
  report: { method: "teamup.report", render: text.teamReportText },
  run: { method: "teamup.run", render: text.teamRunText },
  pause: { method: "teamup.pause", render: (_sid, r) => text.teamLifecycleText("pause", r) },
  resume: { method: "teamup.resume", render: (_sid, r) => text.teamLifecycleText("resume", r) },
  cancel: { method: "teamup.cancel", render: (_sid, r) => text.teamLifecycleText("cancel", r) },
  tailor: { method: "teamup.tailor", render: text.teamTailorText },
};

/** The inventory verbs: no session id. */
const LIST_VERBS: Record<string, { method: string; render: (result: Row) => string }> = {
  list: { method: "teamup.list", render: text.teamListText },
  engines: { method: "teamup.engines", render: text.teamEnginesText },
  crews: { method: "teamup.crews", render: text.teamCrewsText },
  hardware: { method: "teamup.hardware", render: text.teamHardwareText },
};

const HELP = new Set(["help", "--help", "-h", "?"]);

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function words(args: string): string[] {
  return args.split("\t").join(" ").split(" ").filter(Boolean);
}

/** `create <name> [roles=a,b] [goal …]` → teamup.create params (the console's parse). */
export function createParams(tail: string[]): Row | null {
  const name = tail[0] ?? "";
  if (!name) return null;
  const rest = tail.slice(1);
  const rolesToken = rest.find((t) => t.toLowerCase().startsWith("roles="));
  const goal = rest.filter((t) => t !== rolesToken).join(" ");
  return {
    name,
    goal,
    roles: rolesToken ? rolesToken.slice(6) : "pm,se,pg",
    hardware: "local",
    max_iterations: 10,
    auto_approve: false,
    start: true,
  };
}

export class TeamupVerbs {
  private arm: TeamArm | null = null;

  constructor(private readonly deps: TeamupDeps) {}

  get isArmed(): boolean {
    return this.arm !== null;
  }

  private setArm(arm: TeamArm | null): void {
    this.arm = arm;
    this.deps.armed(teamArmLabel(arm));
  }

  /** The armed one-shot, disarmed as it is taken (the console's auto-disarm). */
  takeArm(): TeamArm | null {
    const arm = this.arm;
    if (arm) this.setArm(null);
    return arm;
  }

  /** `/teamup [verb …]`; bare lists today's sessions. */
  async command(args: string): Promise<void> {
    const [raw = "list", ...tail] = words(args);
    const verb = raw.toLowerCase();
    const command = `/teamup ${args.trim()}`.trim();
    if (await this.special(verb, tail, command)) return;
    const list = Object.hasOwn(LIST_VERBS, verb) ? LIST_VERBS[verb] : undefined;
    if (list) return this.call(command, list.method, {}, list.render, list.method);
    await this.sessionVerb(verb, tail[0] ?? "", command);
  }

  /** help, create, plans, on/off; true when the verb was one of them. */
  private async special(verb: string, tail: string[], command: string): Promise<boolean> {
    if (HELP.has(verb)) this.deps.output(command, [{ kind: "info", text: text.teamupHelpText() }]);
    else if (verb === "create") await this.create(command, tail);
    else if (verb === "plans") await this.call(command, "team.plan_templates", {}, text.teamPlansText, "team.plan_templates");
    else if (verb === "on") await this.on(tail, command);
    else if (verb === "off") this.off(command);
    else return false;
    return true;
  }

  /** `status|logs|report|run|pause|resume|cancel|tailor <sid>`. */
  private async sessionVerb(verb: string, sid: string, command: string): Promise<void> {
    const session = Object.hasOwn(SESSION_VERBS, verb) ? SESSION_VERBS[verb] : undefined;
    if (!session) return this.deps.output(command, [{ kind: "error", text: text.teamupUnknownText(verb) }]);
    if (!sid) return this.deps.output(command, [{ kind: "error", text: `usage: /teamup ${verb} <session-id>` }]);
    await this.call(command, session.method, { session_id: sid, ...session.params }, (r) => session.render(sid, r), session.method);
  }

  private async create(command: string, tail: string[]): Promise<void> {
    const params = createParams(tail);
    if (!params) {
      return this.deps.output(command, [{ kind: "error", text: "teamup.create needs a name — `/teamup create <name> [roles=pm,se,pg] [goal text..]`" }]);
    }
    await this.call(command, "teamup.create", params, text.teamCreateText, "teamup.create");
  }

  private async call(command: string, method: string, params: Row, render: (result: Row) => string, label: string): Promise<void> {
    try {
      const result = (await this.deps.request(method, params, 30_000)) as Row;
      this.deps.output(command, [{ kind: "info", text: render(result ?? {}) }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `${label} failed: ${errText(error)}` }]);
    }
  }

  private say(command: string, line: string, kind: "info" | "error" = "info"): void {
    this.deps.output(command, [{ kind, text: line }]);
  }

  /** `/teamup on [crew|plan:<template>] [width | name=value …]` (the console's rules). */
  private async on(tail: string[], command: string): Promise<void> {
    const first = tail[0] ?? "research";
    const crew = first.toLowerCase();
    if (crew.startsWith("plan:")) return this.armPlan(first.slice(5), tail.slice(1), command);
    if (crew !== "hpc") return this.armCrewPlan(crew, tail.slice(1), command);
    const parallel = tail[1] === undefined ? 1 : Number.parseInt(tail[1], 10);
    if (!(parallel >= 1 && parallel <= 8)) return this.say(command, text.TEAMUP_PARALLEL_RANGE, "error");
    this.setArm({ kind: "crew", crew: "hpc", parallel });
    this.say(command, text.crewArmedText(crew, parallel));
  }

  private off(command: string): void {
    const was = this.arm !== null;
    this.setArm(null);
    this.say(command, text.teamupOffText(was));
  }

  /** A plan template, checked against team.plan_templates before arming. */
  private async armPlan(template: string, rest: string[], command: string): Promise<void> {
    const name = template.trim();
    if (!name) return this.say(command, text.PLAN_NAME_NEEDED, "error");
    const args = planArgs(rest);
    if (typeof args === "string") return this.say(command, text.planArgNotNameValueText(name, args), "error");
    let names: string[];
    try {
      const listed = (await this.deps.request("team.plan_templates", {}, 15_000)) as Row;
      names = (Array.isArray(listed?.templates) ? (listed.templates as Row[]) : []).map((r) => String(r?.name ?? "")).filter(Boolean);
    } catch (error) {
      return this.say(command, text.planTemplatesUnavailableText(name, errText(error)), "error");
    }
    if (!names.includes(name)) return this.say(command, text.planNoTemplateText(name, names), "error");
    this.setArm({ kind: "plan", template: name, args });
    this.say(command, text.planArmedText(name, args));
  }

  /** A crew that is a plan template (teamup.crews maps crew or alias → template). */
  private async armCrewPlan(crew: string, rest: string[], command: string): Promise<void> {
    let rows: Row[];
    try {
      const listed = (await this.deps.request("teamup.crews", {}, 15_000)) as Row;
      rows = Array.isArray(listed?.crews) ? (listed.crews as Row[]) : [];
    } catch (error) {
      return this.say(command, text.crewsUnavailableText(crew, errText(error)), "error");
    }
    const row = rows.find((r) => r.name === crew || (Array.isArray(r.aliases) && r.aliases.includes(crew)));
    if (!row || !row.template) {
      return this.say(command, text.crewMustBeText(rows.map((r) => String(r.name ?? "")).filter(Boolean)), "error");
    }
    await this.armPlan(String(row.template), rest, command);
  }

  /** The armed message: a plan run (its card asks first) or the hpc crew's start. */
  async fire(arm: TeamArm, goal: string): Promise<void> {
    if (arm.kind === "plan") return this.firePlan(arm, goal);
    try {
      const params: Row = { goal, crew: arm.crew, max_iterations: 6, run: true };
      if (arm.parallel > 1) params.parallel_roles = arm.parallel;
      await this.deps.request("teamup.start", params, 30_000);
      this.deps.notice(text.crewStartText(arm.crew, true));
    } catch (error) {
      this.deps.notice(`team start failed: ${errText(error)}`);
      this.deps.notice(text.crewStartText(arm.crew, false));
    }
  }

  private async firePlan(arm: Extract<TeamArm, { kind: "plan" }>, goal: string): Promise<void> {
    const params: Row = { template: arm.template, goal };
    if (Object.keys(arm.args).length) params.args = arm.args;
    try {
      const reply = (await this.deps.request("team.plan_run", params, PLAN_CARD_TIMEOUT_MS)) as Row;
      if (!reply?.ok || !reply.run_id) return this.deps.notice(text.planRefusalText(arm.template, reply));
      this.deps.notice(text.planStartedText(arm.template, reply), `plan:${String(reply.run_id)}`);
    } catch (error) {
      this.deps.notice(`team.plan_run failed: ${errText(error)}`);
    }
  }
}

/** `name=value …` after a plan template; the offending token when one is not. */
function planArgs(rest: string[]): Record<string, string> | string {
  const args: Record<string, string> = {};
  for (const token of rest) {
    const eq = token.indexOf("=");
    if (eq <= 0) return token;
    args[token.slice(0, eq)] = token.slice(eq + 1);
  }
  return args;
}

/** One team the live rows follow. */
interface LiveTeam {
  name: string;
  iteration: number;
  maxIterations?: number;
}

/** One plan run the live rows follow. */
interface LivePlan {
  ring: PlanEventLine[];
  row: TeamRunRow | undefined;
}

/**
 * The bridge's `team_event` lines as the console shows them: one keyed row
 * per team (`team:<sid>`) and per plan run (`plan:<run>`), updated in place;
 * a start error, a failed tailor and a plan's gate as their own rows.
 */
export class TeamLive {
  private readonly teams = new Map<string, LiveTeam>();
  private readonly plans = new Map<string, LivePlan>();

  constructor(private readonly notice: (line: string, key?: string) => void) {}

  ingest(event: unknown): void {
    if (isPlanEvent(event)) this.plan(event);
    else if (event && typeof event === "object") this.team(event as TeamEventLine);
  }

  private team(event: TeamEventLine): void {
    if (event.kind === "team_start_error") return this.notice(text.teamStartErrorText(event as unknown as Row));
    const sid = event.session_id;
    if (!sid || !event.kind) return;
    const team = this.follow(sid, event);
    const key = `team:${sid}`;
    if (event.kind === "team_started") {
      this.notice(text.teamStartedText(event as unknown as Row), key);
      if (event.tailor?.degraded) this.notice(text.tailorDegradedText(event.tailor.degraded));
    } else if (event.kind === "run_finished") {
      this.notice(text.runFinishedText(event, team.name), key);
    } else {
      this.notice(text.teamProgressText(team.name, team.iteration, team.maxIterations, event), key);
    }
  }

  private follow(sid: string, event: TeamEventLine): LiveTeam {
    const known = this.teams.get(sid);
    const team: LiveTeam = {
      name: event.session_name ?? known?.name ?? sid.slice(0, 12),
      iteration: event.iteration ?? known?.iteration ?? 0,
      maxIterations: event.max_iterations ?? known?.maxIterations,
    };
    this.teams.set(sid, team);
    return team;
  }

  private plan(event: PlanEventLine): void {
    const runId = event.run_id;
    const known = this.plans.get(runId) ?? { ring: [], row: undefined };
    const live = { ring: appendPlanEvent(known.ring, event), row: applyPlanEvent(known.row, event) };
    this.plans.set(runId, live);
    const name = event.plan ?? live.row?.name ?? runId;
    const key = `plan:${runId}`;
    if (PLAN_RESULT_KINDS.has(event.kind)) return this.notice(text.planResultRowText(name, live.row, agentResultLines(live.ring)), key);
    if (event.kind === "gate_requested") return this.notice(text.planGateText(name, event));
    if (event.kind === "plan_finished") this.notice(text.planFinishedText(name, event, agentResultLines(live.ring)), key);
  }
}
