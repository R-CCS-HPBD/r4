/**
 * The venue picker and the remote source picker as native QuickPicks (ADR-103 VP-3b part 2; the
 * console's remote-source-picker overlay). Rows show the server's badge
 * verbatim (pushed / stale / tombstoned / not pushed / server unreachable).
 */

import * as vscode from "vscode";

import type { VenuePickerOption } from "../../../packages/r4-tui-v3/src/lib/session-filters";
import type { VenueChoice } from "../../../packages/r4-tui-v3/src/types";
import type { PromptRow } from "./chat-verbs";
import type { ModelRow } from "./model-mode";
import type { SourcePick } from "./venue-host";

/** `/models`: the session model; the current one is marked, failed sources named. */
export async function pickModel(ask: { models: ModelRow[]; current: string; problems: string[] }): Promise<string | null> {
  const items = ask.models.map((m) => ({
    label: m.name,
    description: [m.name === ask.current ? "current" : "", m.label && m.label !== m.name ? m.label : ""].filter(Boolean).join(" · "),
  }));
  const placeHolder = ask.problems.length ? `not listed: ${ask.problems.join("; ")}` : undefined;
  const picked = await vscode.window.showQuickPick(items, { title: "R4: session model", placeHolder, ignoreFocusOut: true });
  return picked?.label ?? null;
}

/** `/rewind`: an earlier prompt, newest first; it returns to the composer. */
export async function pickPrompt(rows: PromptRow[]): Promise<string | null> {
  const items = rows.map((row) => ({ label: row.text.split("\n")[0]!.slice(0, 80) || "(empty)", detail: row.turnId, turnId: row.turnId }));
  const picked = await vscode.window.showQuickPick(items, { title: "R4: rewind to which prompt? (sending replaces the conversation from there)", ignoreFocusOut: true });
  return picked?.turnId ?? null;
}

/** Where a new session runs (the console's venue picker): a disabled row
 * stays listed with its reason; picking it says why (the host). */
export async function pickVenue(options: VenuePickerOption[]): Promise<VenueChoice | null> {
  const items = options.map((o) => ({
    label: o.label,
    description: o.disabled ? o.disabledReason : "",
    detail: o.consequence,
    choice: o.choice,
  }));
  const picked = await vscode.window.showQuickPick(items, { title: "R4: where should this session run?", ignoreFocusOut: true });
  return picked?.choice ?? null;
}

export async function pickRemoteSource(ask: { title: string; rows: SourcePick[] }): Promise<string | null> {
  const items = ask.rows.map((row) => ({
    label: row.name,
    description: row.badge,
    detail: row.detail,
  }));
  const picked = await vscode.window.showQuickPick(items, { title: `R4: ${ask.title}`, ignoreFocusOut: true, matchOnDetail: true });
  return picked?.label ?? null;
}
