/**
 * Wave D/E/F/H slash intercepts — extracted from slash.ts dispatch to keep
 * the dispatcher's shape maintained. Returns the dispatch result, or null to
 * fall through to the rest of the slash flow.
 */

import * as teamupText from "../lib/teamup-text";
import { teamPlansText, teamupHelpText, teamupUnknownText } from "../lib/teamup-text";
import { actions, setScopedField, workspace, activeTab } from "../stores";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { applyOnTab } from "../tabs/registry";
import type { RemoteApprovalsOutcome, RemoteQuestionsOutcome } from "../bridge/venue-bridge";
import { manifestRow, rowNames } from "./catalog-manifest";

/** Return-shape contract (kept local to avoid a slash.ts ↔ here import cycle). */
export type WaveInterceptResult =
  | { kind: "handled" }
  | { kind: "error"; message: string }
  | null;

/** Wave E: /health authz */
async function handleHealthAuthz(): Promise<WaveInterceptResult> {
  const { probeAuthz, authzReportLines } = await import("../lib/authz-audit");
  const bridge = getCompanionBridge();
  const result = await probeAuthz((method, params, timeout) => bridge.request(method, params, timeout));
  setScopedField("authzFlags", result.flags.length);
  actions.sendSystemNotice(authzReportLines(result).join("\n"));
  return { kind: "handled" };
}

type VerbIntercept = (args: string) => Promise<WaveInterceptResult>;

/** "name verb" rows, plus "name *" rows that claim every verb of `/name`
 *  (bare included); such a handler answers null to fall through to the rest
 *  of the slash flow (K1.6 `/save`: a transcript path goes to the companion). */
class VerbInterceptTable extends Map<string, VerbIntercept> {
  override get(key: string): VerbIntercept | undefined {
    return super.get(key) ?? super.get(`${key.split(" ")[0]} *`);
  }
}

/** `/<name> <verb>` intercepts keyed "name verb" — a table, not more branches
 *  in the dispatcher below (already over the complexity line). */
const VERB_INTERCEPTS = new VerbInterceptTable([
  // S4 (onboarding plan 2026-10-15): `/source add …` — typed, or asked one
  // value at a time by the /connect wizard (2026-10-19) — registers a
  // public source; its key rides stdin to r4, never argv.
  ["source add", async (args) => (await import("./source-command")).handleSourceAdd(args)],
  ["health authz", () => handleHealthAuthz()],
  // K1.6 (2026-10-24): `/save [n]` keeps a chat turn in project knowledge.
  ["save *", async (args) => (await import("./save-command")).handleSaveCommand(args)],
]);

// The /team row's names (catalog.manifest.json, plan H-5): team, teams, teamup.
const TEAM_NAMES = new Set(rowNames(manifestRow("team")!));

/** `/term share [lines]`: relay the terminal buffer tail to the agent. */
async function handleTermShare(linesArg: string | undefined): Promise<WaveInterceptResult> {
  const { captureTermShare, formatTermShareForAgent } = await import("../terminal/share");
  const requestedN = linesArg === undefined ? undefined : Number(linesArg);
  if (requestedN !== undefined && !(requestedN >= 1)) {
    return { kind: "error", message: `usage: /term share [lines] — got "${linesArg}"` };
  }
  const capture = captureTermShare(requestedN);
  if (capture.kind === "no-session") {
    actions.sendSystemNotice("nothing to share — no terminal session (open /term first)");
    return { kind: "handled" };
  }
  if (capture.kind === "empty") {
    actions.sendSystemNotice("nothing to share — the terminal buffer is empty");
    return { kind: "handled" };
  }
  actions.sendChatMessage(formatTermShareForAgent(capture), { relay: true });
  actions.sendSystemNotice(await termShareReceipt(capture, linesArg === undefined));
  return { kind: "handled" };
}

async function termShareReceipt(
  capture: { lineCount: number; truncatedChars: boolean },
  defaulted: boolean,
): Promise<string> {
  const { TERM_SHARE_DEFAULT_LINES } = await import("../terminal/share");
  const tail = defaulted ? ` (default ${TERM_SHARE_DEFAULT_LINES})` : "";
  const cut = capture.truncatedChars ? " — truncated to 20000 chars" : "";
  return `shared ${capture.lineCount} terminal lines with the agent${tail}${cut}`;
}

/**
 * Terminal pane (2026-10-03): /term — interactive PTY shell. The pane
 * mounts → the shell spawns; bare /term just opens (reattaches after Esc,
 * since the session outlives the pane). share relays the buffer tail to
 * the session agent (2026-10-03; user-initiated only, never streamed).
 * A mistake is ONE error: the dispatcher prints the returned message, so
 * a notice as well printed it twice (stabilization 2026-10-21, slash-c#5).
 */
async function handleTermIntercept(verb: string | undefined, tokens: string[]): Promise<WaveInterceptResult> {
  if (verb === "share") return handleTermShare(tokens[1]);
  const { termSession } = await import("../terminal/pty-session");
  if (verb === "kill") {
    termSession.kill();
    actions.closePane();
    actions.sendSystemNotice("terminal shell killed — pane closed");
    return { kind: "handled" };
  }
  if (verb === "new") {
    termSession.kill();
    actions.openPane("term");
    actions.sendSystemNotice("fresh terminal shell (previous buffer discarded)");
    return { kind: "handled" };
  }
  if (verb) {
    return { kind: "error", message: `unknown /term verb "${verb}" — bare, new, kill, or share [lines]` };
  }
  actions.openPane("term");
  actions.sendSystemNotice("terminal pane — click to focus · click chat to release");
  return { kind: "handled" };
}

export async function handleWaveIntercepts(parsed: {
  name: string;
  args: string;
}): Promise<WaveInterceptResult> {
  const tokens = parsed.args.split(/\s+/).filter(Boolean);
  const verb = tokens[0]?.toLowerCase();

  const verbIntercept = VERB_INTERCEPTS.get(`${parsed.name} ${verb}`);
  if (verbIntercept) return verbIntercept(parsed.args);

  // Wave F: /catalog
  if (parsed.name === "catalog") {
    actions.openPane("catalog");
    return { kind: "handled" };
  }

  // T5: /teamup (and the `/team`, `/teams` aliases — every alias MUST route
  // identically because the slash-bar matches names/aliases at keyboard
  // time; the arg columns never differ by the slash name the user pressed.
  // `/teams status` used to hit the Work-pane row and lose its args.
  if (TEAM_NAMES.has(parsed.name)) {
    return _routeTeamupArgs(parsed.args);
  }

  // T5 topic-5b: bare /endpoint (no args) opens the Work pane where the
  // endpoints section lives now (host:port + tier + backend + capability-detected
  // cold-start). Verb-shaped paths still ride the bridge as before — only the
  // bare form gets the pane redirect (keeps /endpoint up/down/list/... byte-
  // identical chat chains).
  if (
    (parsed.name === "endpoint" || parsed.name === "endpoints") &&
    parsed.args.trim() === ""
  ) {
    actions.openPane("work");
    actions.sendSystemNotice("endpoints → work pane — teams, jobs, and endpoints sections");
    return { kind: "handled" };
  }

  // Terminal pane (2026-10-03): /term — interactive PTY shell. The pane
  // mounts → the shell spawns; bare /term just opens (reattaches after Esc,
  // since the session outlives the pane). share relays the buffer tail to
  // the session agent (2026-10-03; user-initiated only, never streamed).
  if (parsed.name === "term" || parsed.name === "terminal") {
    return handleTermIntercept(verb, tokens);
  }

  // Wave H: /codeview
  if (parsed.name === "codeview") {
    const { listChanges } = await import("../lib/git-changes");
    const changes = listChanges();
    actions.openPane("codeview");
    actions.setChangeSets(changes ?? []);
    actions.sendSystemNotice(
      changes === null
        ? "code view: not a git working tree (run inside a repo)"
        : `code view: ${changes.length} change(s) — Tab into the pane for verbs`,
    );
    return { kind: "handled" };
  }

  // Wave E: /project switch
  if (parsed.name === "project" && verb === "switch" && tokens[1]) {
    return switchProject(tokens[1]);
  }

  // Wave 2026-09-21 (ADR-046 A2): /venue — remote session runtime verbs.
  if (parsed.name === "venue") {
    return handleVenueIntercept(verb, tokens);
  }

  // Chat tabs (2026-09-29): /tab — parallel session strip verbs.
  if (parsed.name === "tab") {
    return handleTabIntercept(verb, tokens);
  }

  // Wave D: /sessions (+ mutating sub-verbs)
  if (parsed.name === "sessions") {
    const {
      fetchSessions,
      renameSession,
      archiveSession,
      pinSession,
      exportSession,
    } = await import("../bridge/session-bridge");
    if (!verb) {
      actions.openPane("sessions");
      const count = await fetchSessions();
      actions.sendSystemNotice(`sessions: ${count} records (Tab into the pane for verbs)`);
      return { kind: "handled" };
    }
    if (verb === "new") {
      // P2: venue is chosen at session start (picker gates Server on the
      // journal probe; disabled options carry their reason).
      actions.openVenuePicker();
      return { kind: "handled" };
    }
    if (verb === "resume" && tokens[1]) {
      // Chat tabs (2026-09-29): resume opens the session in its own tab
      // (dedupes to the tab already holding it), never clobbers the live one.
      await actions.tabOpenSession(tokens[1]);
      return { kind: "handled" };
    }
    if (verb === "rename" && tokens[1] && tokens.length > 2) {
      await renameSession(tokens[1], tokens.slice(2).join(" "));
      return { kind: "handled" };
    }
    if (verb === "archive" && tokens[1]) {
      await archiveSession(tokens[1]);
      return { kind: "handled" };
    }
    if ((verb === "pin" || verb === "unpin") && tokens[1]) {
      await pinSession(tokens[1], verb === "pin");
      return { kind: "handled" };
    }
    if (verb === "export") {
      await exportSession(tokens[1]);
      return { kind: "handled" };
    }
    // T5 topic-20 tail: per-turn drill into one session's own payload —
    // render rows inline (never surrogate full-overlay for detail rows yet).
    if (verb === "turns" && tokens[1]) {
      const { fetchSessionTurns } = await import("../bridge/session-bridge");
      await fetchSessionTurns(tokens[1], tokens[2]);
      return { kind: "handled" };
    }
    return {
      kind: "error",
      message:
        "usage: /sessions [new|resume <id>|rename <id> <name>|archive <id>|pin <id>|unpin <id>|export [id]|turns <session-id> [<turn-id>] ]",
    };
  }

  return null;
}

/** `/project switch <id>` for the tab that typed it. Every write after an
 *  await lands on THAT tab (2026-10-21: switching tabs mid-request flushed
 *  the OTHER tab's transcript and patched its project state). */
async function switchProject(projectId: string): Promise<WaveInterceptResult> {
  const tabId = workspace.activeChatTabId;
  const bridge = getCompanionBridge();
  try {
    await bridge.request("project_switch", { project_id: projectId }, 30_000);
    // B1 fix (2026-09-27): the switch reply is NOT a state snapshot — the
    // status badge only updated from a real one. Pull it now so
    // identityProjectId follows the switch immediately.
    const state = await bridge.request("get_state", {}, 15_000);
    const { applyBridgeStatePatch, mapBridgeStateToWorkspace } = await import("../bridge/map-state");
    applyOnTab(tabId, () => {
      applyBridgeStatePatch(mapBridgeStateToWorkspace(state, undefined));
      // Flush session-scoped client state so nothing cross-projects.
      setScopedField("chatMessages", []);
      actions.setSessionsList([]);
    });
    // Per-tab projects (2026-10-01): activeTab().project must follow THIS
    // tab's bridge — refreshAfterProjectConfigChange reads the ACTIVE tab's
    // bridge, so it runs only while the switching tab is still active.
    if (workspace.activeChatTabId === tabId) {
      const { refreshAfterProjectConfigChange } = await import("../hooks/project-refresh");
      await refreshAfterProjectConfigChange();
    }
    applyOnTab(tabId, () => actions.sendSystemNotice(`project → ${projectId} (tab-scoped; other tabs unchanged)`));
    return { kind: "handled" };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    applyOnTab(tabId, () => actions.sendErrorCard({ what: `project switch denied`, action: "needs_you", detail }));
    return { kind: "error", message: detail };
  }
}

function _teamArgTokens(args: string): string[] {
  return args.split(/\s+/).filter(Boolean);
}

function _teamVerbLabel(first: string): string {
  return `teamup.${first}`;
}

async function _teamupCreateFromArgs(tail: string[]): Promise<WaveInterceptResult> {
  const { teamsActions } = await import("../stores/teams");
  const name = tail[1] || "";
  const rolesArgs = tail.slice(2).find((tok) => /^roles=/i.test(tok))?.slice(6) ?? "pm,se,pg";
  const goalArgs = tail.slice(2).filter((tok) => !tok.startsWith("roles=")).join(" ");
  if (!name) {
    actions.sendSystemNotice(
      "teamup.create needs a name — `/teamup create <name> [roles=pm,se,pg] [goal text..]`",
    );
    return { kind: "handled" };
  }
  await teamsActions.create({
    name,
    goal: goalArgs,
    roles: rolesArgs,
    start: true,
  });
  return { kind: "handled" };
}

/**
 * B-family (2026-09-27): one-shot teamup arming — the NEXT chat message
 * becomes the team's goal (research crew default, hpc on request).
 * ADR-092 slice 4: every crew but hpc is a plan template, so it arms that
 * template's plan lane (the plan's card asks before anything runs).
 */
function _teamupOn(tail: string[]): Promise<WaveInterceptResult> | WaveInterceptResult {
  const crew = (tail[1] || "research").toLowerCase();
  if (crew.startsWith("plan:")) return _armTeamPlan(tail[1]!.slice(5), tail.slice(2));
  if (crew !== "hpc") return _armCrewPlan(crew, tail.slice(2));
  // Optional third token = parallel role width (roles run concurrently per
  // iteration; parallel roles trade away same-iteration changelog context).
  const parallel = tail[2] === undefined ? 1 : Number.parseInt(tail[2], 10);
  if (!(parallel >= 1 && parallel <= 8)) {
    actions.sendSystemNotice(teamupText.TEAMUP_PARALLEL_RANGE);
    return { kind: "handled" };
  }
  actions.setTeamupArmed(true, "hpc", parallel);
  actions.sendSystemNotice(teamupText.crewArmedText(crew, parallel));
  return { kind: "handled" };
}

function _teamupOff(): WaveInterceptResult {
  const was = workspace.teamupArmed;
  actions.setTeamupArmed(false);
  actions.sendSystemNotice(teamupText.teamupOffText(Boolean(was)));
  return { kind: "handled" };
}

async function _routeTeamupArgs(args: string): Promise<WaveInterceptResult> {
  const tail = _teamArgTokens(args);
  const first = (tail[0] || "").toLowerCase();
  const sidToken = tail[1] ?? "";
  const { teamsActions } = await import("../stores/teams");

  if (first === "on") return _teamupOn(tail);
  if (first === "off") return _teamupOff();
  if (first === "create") return _teamupCreateFromArgs(tail);
  if (first === "plans") return _listTeamPlanTemplates();

  const run = {
    list: () => teamsActions.list(),
    status: () => teamsActions.status(sidToken),
    logs: () => teamsActions.logs(sidToken, 12),
    report: () => teamsActions.report(sidToken),
    run: () => teamsActions.run(sidToken),
    pause: () => teamsActions.pause(sidToken),
    resume: () => teamsActions.resume(sidToken),
    cancel: () => teamsActions.cancel(sidToken),
    tailor: () => teamsActions.tailor(sidToken),
    engines: () => teamsActions.engines(),
    crews: () => teamsActions.crews(),
    hardware: () => teamsActions.hardware(),
  } as const;
  // Own keys only: `/teamup constructor` must not reach Object.prototype.
  if (first && Object.hasOwn(run, first)) {
    actions.openPane("work");
    await run[first as keyof typeof run]();
    return { kind: "handled" };
  }
  return _teamupFallback(first, () => {
    actions.openPane("work");
    return teamsActions.list();
  });
}

// The /teamup verb list and texts are pure, in lib/teamup-text.ts (the VS
// Code host prints them too, ADR-103 D-3).

/** Bare /teamup lists today's sessions; `help` prints the verbs; any other
 * word is an unknown sub. Both used to list silently, so '/teamup help'
 * explained nothing (stabilization 2026-10-21, slash-a#5). */
type TeamupList = () => Promise<unknown>;

async function _teamupFallback(first: string, list: TeamupList): Promise<WaveInterceptResult> {
  if (!first) {
    await list();
    return { kind: "handled" };
  }
  if (["help", "--help", "-h", "?"].includes(first)) {
    actions.sendSystemNotice(teamupHelpText());
    return { kind: "handled" };
  }
  return { kind: "error", message: teamupUnknownText(first) };
}

/**
 * ADR-092 slice 3: `/teamup on plan:<template> [name=value …]` arms a team
 * plan template; the NEXT chat message becomes its goal and runs through
 * `team.plan_run` (the bridge asks the human with the plan's card — never
 * auto-approved). The template is checked against `team.plan_templates`
 * now, so a typo is refused by name before anything is armed.
 */
async function _armTeamPlan(template: string, rest: string[]): Promise<WaveInterceptResult> {
  const name = template.trim();
  if (!name) {
    actions.sendSystemNotice(teamupText.PLAN_NAME_NEEDED);
    return { kind: "handled" };
  }
  const args: Record<string, string> = {};
  for (const token of rest) {
    const eq = token.indexOf("=");
    if (eq <= 0) {
      actions.sendSystemNotice(teamupText.planArgNotNameValueText(name, token));
      return { kind: "handled" };
    }
    args[token.slice(0, eq)] = token.slice(eq + 1);
  }
  let names: string[];
  try {
    const listed = (await getCompanionBridge().request("team.plan_templates", {}, 15_000)) as {
      templates?: Array<{ name?: string }>;
    };
    names = (listed?.templates ?? []).map((row) => String(row?.name ?? "")).filter(Boolean);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    actions.sendSystemNotice(teamupText.planTemplatesUnavailableText(name, message));
    return { kind: "handled" };
  }
  if (!names.includes(name)) {
    actions.sendSystemNotice(teamupText.planNoTemplateText(name, names));
    return { kind: "handled" };
  }
  actions.setTeamupArmedPlan(name, args);
  actions.sendSystemNotice(teamupText.planArmedText(name, args));
  return { kind: "handled" };
}

/**
 * ADR-092 slice 4: `/teamup on <crew>` for a crew that is a plan template.
 * The crew (or one of its aliases) is resolved by the bridge's
 * `teamup.crews` (the one source of the crew → template map), then armed
 * exactly as `/teamup on plan:<template>` arms it.
 */
async function _armCrewPlan(crew: string, rest: string[]): Promise<WaveInterceptResult> {
  let rows: Array<{ name?: string; template?: string | null; aliases?: string[] }>;
  try {
    const listed = (await getCompanionBridge().request("teamup.crews", {}, 15_000)) as {
      crews?: typeof rows;
    };
    rows = listed?.crews ?? [];
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    actions.sendSystemNotice(teamupText.crewsUnavailableText(crew, message));
    return { kind: "handled" };
  }
  const row = rows.find((r) => r.name === crew || (r.aliases ?? []).includes(crew));
  if (!row || !row.template) {
    actions.sendSystemNotice(teamupText.crewMustBeText(rows.map((r) => String(r.name ?? "")).filter(Boolean)));
    return { kind: "handled" };
  }
  return _armTeamPlan(row.template, rest);
}

async function _listTeamPlanTemplates(): Promise<WaveInterceptResult> {
  try {
    const listed = (await getCompanionBridge().request("team.plan_templates", {}, 15_000)) as {
      templates?: Array<{ name?: string; description?: string; params?: Record<string, { required?: boolean }> }>;
    };
    actions.sendSystemNotice(teamPlansText(listed ?? {}));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    actions.sendSystemNotice(`team.plan_templates failed: ${message}`);
  }
  return { kind: "handled" };
}

const TAB_USAGE = "usage: /tab [list|new|close [n]|next|prev|reopen|<n>]";

function listTabsNotice(): void {
  const rows = workspace.chatTabs.map(
    (t, i) =>
      `  ${i + 1}:${t.title}${t.id === workspace.activeChatTabId ? " ←" : ""}` +
      `${t.busy ? " ●" : ""}${t.needsAttention ? " !" : ""}${t.sessionId ? ` (${t.sessionId.slice(0, 8)})` : ""}`,
  );
  actions.sendSystemNotice(`chat tabs (${workspace.chatTabs.length}):\n${rows.join("\n")}`);
}

/** `/tab close [n]`: no index closes the active tab; a bad index is an error.
 *  tabClose(undefined) means "the active tab", so `/tab close 9` with three
 *  tabs used to kill the tab in use and its running turn (TUI audit r2 #39). */
async function closeTabIntercept(arg: string | undefined): Promise<WaveInterceptResult> {
  if (arg === undefined) {
    await actions.tabClose();
    return { kind: "handled" };
  }
  const idx = Number(arg);
  const target = Number.isInteger(idx) ? workspace.chatTabs[idx - 1] : undefined;
  if (!target || idx < 1) {
    return {
      kind: "error",
      message: `no tab ${arg} (open: 1..${workspace.chatTabs.length}) — usage: /tab close [n]`,
    };
  }
  await actions.tabClose(target.id);
  return { kind: "handled" };
}

/** `/tab [list|new|close [n]|next|prev|reopen|<n>]` — chat tab strip verbs. */
async function handleTabIntercept(verb: string | undefined, tokens: string[]): Promise<WaveInterceptResult> {
  if (!verb || verb === "list") {
    listTabsNotice();
    return { kind: "handled" };
  }
  if (verb === "close") return closeTabIntercept(tokens[1]);
  if (verb === "reopen") {
    await actions.tabReopenClosed();
    return { kind: "handled" };
  }
  const simple: Record<string, () => void> = {
    new: () => void actions.tabCreate(),
    next: () => actions.tabCycle(1),
    prev: () => actions.tabCycle(-1),
  };
  // Own keys only: `/tab toString` must not reach Object.prototype.
  const run = Object.hasOwn(simple, verb) ? simple[verb] : undefined;
  if (run) {
    run();
    return { kind: "handled" };
  }
  if (Number.isFinite(Number(verb))) return jumpTabIntercept(verb);
  return { kind: "error", message: TAB_USAGE };
}

/** `/tab <n>`: a bad index gets close's error — tabJump ignores a missing
 * tab, so `/tab 9`, `/tab 0` and `/tab 1.5` did nothing at all
 * (stabilization 2026-10-21, slash-b#7 / tabs-sessions#7). */
function jumpTabIntercept(arg: string): WaveInterceptResult {
  const idx = Number(arg);
  if (!Number.isInteger(idx) || !workspace.chatTabs[idx - 1] || idx < 1) {
    return { kind: "error", message: `no tab ${arg} (open: 1..${workspace.chatTabs.length}) — usage: /tab <n>` };
  }
  actions.tabJump(idx);
  return { kind: "handled" };
}

/** `/venue [status|start|stop [<id>]|sources|approvals|questions]` (ADR-046 A2;
 * questions: ADR-099 D-8). */
async function handleVenueIntercept(
  verb: string | undefined,
  tokens: string[],
): Promise<WaveInterceptResult> {
  const venue = await import("../bridge/venue-bridge");
  const { formatVenueStatusLine } = await import("../lib/venue-runtime");
  if (!verb || verb === "status") {
    const { runtime, error } = await venue.fetchVenueStatusChecked();
    return venueStatusResult(runtime ? formatVenueStatusLine(runtime) : null, error);
  }
  if (verb === "start") {
    // Starts THIS session on the server (the venue picker's Server row is
    // the one that creates a new session; TUI audit r2 #57).
    actions.openRemoteSourcePicker("current");
    return { kind: "handled" };
  }
  if (verb === "stop") {
    await venue.stopRemoteSession(tokens[1]);
    return { kind: "handled" };
  }
  if (verb === "sources") {
    actions.openRemoteSourcePicker("manage");
    return { kind: "handled" };
  }
  if (verb === "approvals") return remoteApprovalsResult(await venue.surfaceRemoteApprovalsChecked());
  if (verb === "questions") return remoteQuestionsResult(await venue.reviewRemoteQuestions());
  return { kind: "error", message: "usage: /venue [status|start|stop [<id>]|sources|approvals|questions]" };
}

/** `/venue questions`: the remote worker's pending ask_user questions, the
 * ones set aside with Esc included (ADR-099 D-8). */
function remoteQuestionsResult(outcome: RemoteQuestionsOutcome): WaveInterceptResult {
  if (outcome.kind === "error") return { kind: "error", message: `venue questions failed: ${outcome.error}` };
  if (outcome.kind === "card_open") {
    actions.sendSystemNotice("a card is already open — answer it, then /venue questions again");
  } else if (outcome.count === 0) {
    actions.sendSystemNotice("no pending remote questions");
  } else if (outcome.count > 1) {
    actions.sendSystemNotice(outcome.count + " pending remote questions — the one the worker waits on is shown");
  }
  return { kind: "handled" };
}

function remoteApprovalsResult(outcome: RemoteApprovalsOutcome): WaveInterceptResult {
  if (outcome.kind === "error") return { kind: "error", message: `venue approvals failed: ${outcome.error}` };
  if (outcome.kind === "card_open") {
    actions.sendSystemNotice("an approval card is already open — decide it, then /venue approvals again");
  } else {
    actions.sendSystemNotice(
      outcome.count === 0
        ? "no pending remote approvals"
        : outcome.count + " pending remote approval(s) — first one shown",
    );
  }
  return { kind: "handled" };
}

/** A failed lookup is an error only for a remote session; a local session
 *  needs no server record (no gateway, no session id yet), so it stays local. */
function venueStatusResult(line: string | null, error: string | null | undefined): WaveInterceptResult {
  if (error && activeTab().sessionVenue === "frontend") {
    return { kind: "error", message: `venue status failed: ${error}` };
  }
  actions.sendSystemNotice(line ?? noRuntimeNotice());
  return { kind: "handled" };
}

function noRuntimeNotice(): string {
  return activeTab().sessionVenue === "frontend"
    ? "remote runtime: none recorded for this session (start one with /venue start)"
    : "this session runs locally — /venue start runs it on the server instead";
}
