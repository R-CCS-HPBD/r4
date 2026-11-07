/**
 * /circles slash family (ADR-086; amendment 2026-10-19 — console write verbs).
 *
 * Reads: `/circles [project]` lists the caller's circles (everywhere, or the
 * project's roster when affiliated); `/circles <project> <id>` — or the
 * explicit `members` verb — lists one circle's accounts.
 *
 * Writes ride the server's four-rung ladder (circle owner / circle admin /
 * project owner / platform admin — the rungs live in umrs api/circles.py,
 * never in this client): `enter` mints the caller's personal cell in a
 * project (casual project entry), `create` a named circle (needs standing),
 * `add`/`remove` move one account in or out of a roster. Retire stays
 * CLI-only (`r4 circle retire`) — a tombstone is lifecycle, not console fare.
 * Every failure lands as a notice with the server's message verbatim.
 */

import { getCompanionBridge } from "../bridge";
import { bridgeErrorHint } from "../bridge/jsonl-client";
import { actions, workspace } from "../stores";
import { isOfflineBackend } from "../lib/console-mode";
import type { SlashDispatchResult } from "./slash";

const USAGE =
  "circles — /circles [project] · /circles <project> <id> members · " +
  "enter <project> · create <project> <id> [name…] · " +
  "add <project> <id> <user> [--role admin] · remove <project> <id> <user>";

/** First tokens that are verbs, never project ids (a project named like one
 * stays readable through /circles list <p> parity with the CLI). */
const VERBS = new Set(["enter", "create", "add", "remove", "members"]);

type Row = Record<string, unknown>;

function tupleJoin(parts: string[]): string {
  return parts.filter(Boolean).join(" · ");
}

function circleRowLine(g: Row): string {
  const ref = g.circle_id
    ? `${String(g.project_id ?? "?")}/${String(g.circle_id)}`
    : String(g.group_id ?? g.id ?? "?");
  const name = String(g.name ?? "");
  const role = String(g.role ?? "");
  const count = typeof g.member_count === "number" ? `${g.member_count} members` : "";
  return `  ${tupleJoin([ref, name, role, count])}`;
}

function memberLine(m: Row): string {
  const userId = String(m.user_id ?? "?");
  const role = String(m.role ?? "");
  const joined = String(m.added_at ?? "");
  return `  ${tupleJoin([userId, role, joined])}`;
}

function noticeRoster(projectId: string, circleId: string, members: Row[]): void {
  if (members.length === 0) {
    actions.sendSystemNotice(`circle ${projectId}/${circleId}: no visible members`);
    return;
  }
  const lines = members.map(memberLine);
  actions.sendSystemNotice(
    `circle ${projectId}/${circleId} members (${members.length}):\n${lines.join("\n")}`,
  );
}

function fail(err: unknown): SlashDispatchResult {
  // The server's remedy rides on its own line (signed out: "/connect …").
  const hint = bridgeErrorHint(err);
  const message = err instanceof Error ? err.message : String(err);
  actions.sendSystemNotice(`circles: ${message}${hint ? `\n  → ${hint}` : ""}`);
  return { kind: "handled" };
}

function usageError(verb: string): SlashDispatchResult {
  return { kind: "error", message: `circles ${verb}: wrong arguments — ${USAGE}` };
}

/** /circles [project] — the caller's circles, everywhere or one project. */
async function circlesList(argv: string[]): Promise<SlashDispatchResult> {
  const projectId = argv[0] ?? "";
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(
    "circles.list",
    projectId ? { project_id: projectId } : {},
    15_000,
  )) as { circles?: Row[] };
  const rows = Array.isArray(payload.circles) ? payload.circles : [];
  if (rows.length === 0) {
    actions.sendSystemNotice("circles — none visible to this caller (verified identity)");
    return { kind: "handled" };
  }
  const lines = rows.map(circleRowLine);
  actions.sendSystemNotice(`circles (${rows.length}):\n${lines.join("\n")}`);
  return { kind: "handled" };
}

/** /circles <project> <id> | members <project> <id> — the circle's accounts. */
async function circlesMembers(projectId: string, circleId: string): Promise<SlashDispatchResult> {
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(
    "circles.members",
    { project_id: projectId, circle_id: circleId },
    15_000,
  )) as { members?: Row[] };
  const members = Array.isArray(payload.members) ? payload.members : [];
  noticeRoster(projectId, circleId, members);
  return { kind: "handled" };
}

/** enter <project> — the caller's personal cell (casual project entry). */
async function circlesEnter(rest: string[]): Promise<SlashDispatchResult> {
  const projectId = rest[0] ?? "";
  if (!projectId) return usageError("enter");
  const me = workspace.identityUsername;
  if (!me) {
    actions.sendSystemNotice(
      "circles enter: the caller id is unknown (no JWT decoded) — sign in first",
    );
    return { kind: "handled" };
  }
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(
    "circles.create",
    { project_id: projectId, circle_id: me },
    15_000,
  )) as { circle?: Row };
  const circle = payload.circle ?? {};
  actions.sendSystemNotice(
    `Entered ${projectId} as ${String(circle.project_id ?? projectId)}/` +
      `${String(circle.circle_id ?? me)} — your personal cell.`,
  );
  return { kind: "handled" };
}

/** create <project> <id> [name…] — a named circle (server checks standing). */
async function circlesCreate(rest: string[]): Promise<SlashDispatchResult> {
  const [projectId, circleId, ...nameParts] = rest;
  if (!projectId || !circleId) return usageError("create");
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(
    "circles.create",
    { project_id: projectId, circle_id: circleId, name: nameParts.join(" ") },
    15_000,
  )) as { circle?: Row };
  const circle = payload.circle ?? {};
  actions.sendSystemNotice(
    `Circle ${String(circle.project_id ?? projectId)}/${String(circle.circle_id ?? circleId)}` +
      ` (owner ${String(circle.owner_id ?? "?")}) ready.`,
  );
  return { kind: "handled" };
}

/** Parse the positional tail of `add`: <p> <c> <user> [--role member|admin]. */
function parseAddRest(rest: string[]): { ids: string[]; role: string } {
  const ids: string[] = [];
  let role = "member";
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === "--role") {
      role = (rest[i + 1] ?? "").toLowerCase();
      i++;
    } else {
      ids.push(rest[i]!);
    }
  }
  return { ids, role };
}

/** add <project> <id> <user> [--role admin] — put one account in. */
async function circlesAdd(rest: string[]): Promise<SlashDispatchResult> {
  const { ids, role } = parseAddRest(rest);
  const [projectId, circleId, userId] = ids;
  if (!projectId || !circleId || !userId) return usageError("add");
  if (role !== "member" && role !== "admin") return usageError("add");
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(
    "circles.member_add",
    { project_id: projectId, circle_id: circleId, user_id: userId, role },
    15_000,
  )) as { members?: Row[] };
  const members = Array.isArray(payload.members) ? payload.members : [];
  noticeRoster(projectId, circleId, members);
  return { kind: "handled" };
}

/** remove <project> <id> <user> — take one account out. */
async function circlesRemove(rest: string[]): Promise<SlashDispatchResult> {
  const [projectId, circleId, userId] = rest;
  if (!projectId || !circleId || !userId) return usageError("remove");
  const bridge = getCompanionBridge();
  const payload = (await bridge.request(
    "circles.member_remove",
    { project_id: projectId, circle_id: circleId, user_id: userId },
    15_000,
  )) as { members?: Row[] };
  const members = Array.isArray(payload.members) ? payload.members : [];
  noticeRoster(projectId, circleId, members);
  return { kind: "handled" };
}

/** ADR-086: /circles — reads are positional, writes verb-shaped (see USAGE). */
export async function handleCirclesCommand(args: string): Promise<SlashDispatchResult> {
  if (isOfflineBackend()) {
    return { kind: "error", message: "circles: requires a live backend" };
  }
  const argv = args.trim().split(/\s+/).filter(Boolean);
  const head = (argv[0] ?? "").toLowerCase();
  const rest = argv.slice(1);
  try {
    if (!VERBS.has(head)) {
      return argv.length >= 2 ? await circlesMembers(argv[0]!, argv[1]!) : await circlesList(argv);
    }
    if (head === "members") {
      return rest.length === 2 ? await circlesMembers(rest[0]!, rest[1]!) : usageError("members");
    }
    if (head === "enter") return await circlesEnter(rest);
    if (head === "create") return await circlesCreate(rest);
    if (head === "add") return await circlesAdd(rest);
    return await circlesRemove(rest);
  } catch (err) {
    return fail(err);
  }
}
