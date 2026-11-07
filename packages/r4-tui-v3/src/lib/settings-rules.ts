/**
 * `/settings rules` and `/settings matrix` (ADR-104 D-1, D-3).
 *
 * Rules are the companion's (one G14 loader, three sources, deny-first):
 * this console lists and removes them through two bridge verbs and never
 * reads or writes a rule file itself.
 *
 *   permission_rules.list   {}            → {project_id, rules: [...]}
 *   permission_rules.remove {index: n}    → the same shape after removal
 *                                           (error when n is not removable)
 */

import { actions, activeTab } from "../stores";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import { isOfflineBackend } from "./console-mode";
import { modeBadge, MODE_MATRIX_LINES } from "./mode-badge";

export type RuleBehavior = "allow" | "ask" | "deny";
export type RuleSource = "user" | "user_project" | "project";

export interface PermissionRuleRow {
  index: number;
  behavior: RuleBehavior;
  rule: string;
  source: RuleSource;
  removable: boolean;
}

export interface PermissionRulesListing {
  projectId: string;
  rules: PermissionRuleRow[];
}

type Outcome = { kind: "handled" } | { kind: "error"; message: string };

const SOURCE_LABEL: Record<string, string> = {
  user: "user (~/.config/r4/permissions.json)",
  user_project: "user, this project",
  project: "workspace .r4/permissions.json (checked in)",
};

const RPC_TIMEOUT_MS = 15_000;

function asRuleRow(raw: unknown): PermissionRuleRow | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.index !== "number" || typeof r.rule !== "string") return null;
  return {
    index: r.index,
    behavior: String(r.behavior ?? "allow") as RuleBehavior,
    rule: r.rule,
    source: String(r.source ?? "user") as RuleSource,
    removable: r.removable === true,
  };
}

/** Narrow a bridge answer to a listing (unknown shapes read as empty). */
export function parseRulesListing(raw: unknown): PermissionRulesListing {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const rows = Array.isArray(r.rules) ? r.rules.map(asRuleRow).filter((x): x is PermissionRuleRow => x !== null) : [];
  return { projectId: typeof r.project_id === "string" ? r.project_id : "", rules: rows };
}

/** The listing as notice lines: index, behavior, rule, where it lives. */
export function formatRulesListing(listing: PermissionRulesListing): string {
  const head = `permission rules${listing.projectId ? ` (project ${listing.projectId})` : ""} — deny first, evaluated by the companion:`;
  if (listing.rules.length === 0) {
    return `${head}\n  (none — add standing rules in ~/.config/r4/permissions.json; an approval card's "a" lasts this session)`;
  }
  const lines = listing.rules.map((r) => {
    const fixed = r.removable ? "" : " · not removable here";
    return `  ${r.index}. ${r.behavior.padEnd(5)} ${r.rule} — ${SOURCE_LABEL[r.source] ?? r.source}${fixed}`;
  });
  return [head, ...lines, "usage: /settings rules rm <n> · /settings rules clear (removable rules only)"].join("\n");
}

async function callRules(
  method: "permission_rules.list" | "permission_rules.remove",
  params: Record<string, unknown>,
): Promise<PermissionRulesListing> {
  return parseRulesListing(await rpc(getCompanionBridge(), method, params, RPC_TIMEOUT_MS));
}

/** `rm <n>`: n must be a whole number (rm abc used to report success). */
export function parseRuleIndex(token: string | undefined): number | null {
  if (!token || !/^\d+$/.test(token)) return null;
  return Number(token);
}

async function removeRule(token: string | undefined): Promise<Outcome> {
  const index = parseRuleIndex(token);
  if (index === null) {
    return { kind: "error", message: `invalid rule index: ${token ?? "(none)"} — use a number from /settings rules` };
  }
  const after = await callRules("permission_rules.remove", { index });
  actions.sendSystemNotice(`removed rule #${index} — ${after.rules.length} rule(s) remain\n${formatRulesListing(after)}`);
  return { kind: "handled" };
}

/** `clear`: remove every removable rule, highest index first (indexes of
 * the rules below stay valid). */
async function clearRules(): Promise<Outcome> {
  const listing = await callRules("permission_rules.list", {});
  const removable = listing.rules.filter((r) => r.removable).map((r) => r.index).sort((a, b) => b - a);
  let last = listing;
  for (const index of removable) last = await callRules("permission_rules.remove", { index });
  actions.sendSystemNotice(`removed ${removable.length} rule(s)\n${formatRulesListing(last)}`);
  return { kind: "handled" };
}

async function rulesVerb(tokens: string[]): Promise<Outcome> {
  const sub = (tokens[1] ?? "").toLowerCase();
  if (sub === "rm") return removeRule(tokens[2]);
  if (sub === "clear") return clearRules();
  actions.sendSystemNotice(formatRulesListing(await callRules("permission_rules.list", {})));
  return { kind: "handled" };
}

/** `/settings rules [rm <n>|clear]` (tokens[0] is "rules"). */
export async function handleSettingsRules(tokens: string[]): Promise<Outcome> {
  if (isOfflineBackend()) return { kind: "error", message: "permission rules live in the companion — needs a live backend" };
  try {
    return await rulesVerb(tokens);
  } catch (err) {
    return { kind: "error", message: `rules: ${err instanceof Error ? err.message : String(err)}` };
  }
}

/** `/settings matrix`: what each mode lets run (ADR-104 D-1 table). */
export function settingsMatrixNotice(): { kind: "handled" } {
  const current = modeBadge(activeTab().interactionMode, activeTab().trustSource);
  actions.sendSystemNotice([...MODE_MATRIX_LINES, `current: ${current}`].join("\n"));
  return { kind: "handled" };
}
