/**
 * D2: fold slash verbs into the command palette.
 * slash.ts ⇄ commands/index.ts have an import cycle, so the merge happens
 * palette-side through this pure builder (deps injected for tests).
 */

import type { Command } from "../types";
import { canonicalizeSlashAlias, needsArgPrompt, type SlashCommandEntry } from "../commands/slash";
import { MANIFEST_ROWS, rowNames } from "../commands/catalog-manifest";

export interface SlashPaletteDeps {
  dispatch: (line: string) => Promise<unknown> | void;
  insertDraft: (text: string) => void;
}

/**
 * Map slash verbs to palette commands.
 * takesArgs verbs seed the composer draft (`/name `) instead of running
 * arg-less; hidden verbs are dropped unless includeHidden is set.
 */
export function buildSlashPaletteCommands(
  entries: SlashCommandEntry[],
  deps: SlashPaletteDeps,
  options?: { includeHidden?: boolean },
): Command[] {
  return entries
    .filter((entry) => options?.includeHidden === true || entry.hidden !== true)
    .map((entry) => ({
      id: `slash:${entry.name}`,
      namespace: `slash/${entry.category}`,
      title: `/${entry.name}${entry.takesArgs ? " …" : ""} — ${entry.help}`,
      run: () => {
        // Same bare-dispatch grammar as the slash bar: takesArgs alone must NOT
        // trap Enter into a draft fill — picker parents (/theme, /endpoint)
        // declare subcommands and run bare with ONE Enter (the 2-Enter report).
        if (needsArgPrompt(entry, "")) {
          deps.insertDraft(`/${entry.name} `);
          return;
        }
        void deps.dispatch(`/${entry.name}`);
      },
    }));
}

/** Palette query convention: a leading `!` reveals hidden entries. */
export function paletteQueryMode(query: string): { includeHidden: boolean; search: string } {
  if (query.startsWith("!")) {
    return { includeHidden: true, search: query.slice(1).trim() };
  }
  return { includeHidden: false, search: query.trim() };
}

/**
 * When the palette query already carries the command AND its args
 * ("mode chat", "sessions resume abc123"), return the full slash line to
 * dispatch VERBATIM — the args must not be dropped by walking the selected
 * row's no-arg run(). Returns null when the query matches the name only.
 */
export function paletteTypedLine(query: string, commandName: string): string | null {
  const typed = query.trim().replace(/^\//, "").trim();
  if (!typed) return null;
  const parts = typed.split(/\s+/);
  if (parts[0] !== commandName || parts.length < 2) return null;
  // Normalize runs of whitespace (parsers split on \s+ anyway, and the
  // status/turn lines look sloppy otherwise).
  return `/${parts.join(" ")}`;
}

/**
 * Verbatim dispatch for a query that already IS a full slash line with
 * args ("login eyJ…", "/source add http://…") — resolved against the whole
 * catalog (name or alias), NOT the selected row.
 *
 * Needed because arg payloads routinely kill fuzzy matching: a pasted JWT
 * leaves zero matching rows, so Enter had nothing to run and the token was
 * never recognised as a command (user report 2026-09-11). When this hits,
 * the args pass through untouched.
 */
export function paletteDirectLine(
  query: string,
  entries: SlashCommandEntry[],
): string | null {
  const typed = query.trim().replace(/^\//, "").trim();
  if (!typed) return null;
  const parts = typed.split(/\s+/);
  if (parts.length < 2) return null;
  const name = parts[0]!.toLowerCase();
  const rest = parts.slice(1).join(" ");
  // Local-row aliases whose canonical form also rewrites the args
  // (`resume <id>` → `/sessions resume <id>`), not just the name.
  const canon = canonicalizeSlashAlias({ name, args: rest }, "");
  if (canon.parsed.name !== name) return canon.line;
  const hit = catalogEntryFor(name, entries);
  if (hit) return `/${hit.name} ${rest}`;
  return manifestKnows(name) ? `/${name} ${rest}` : null;
}

/** The catalog row a typed name or alias names exactly, if any. */
function catalogEntryFor(name: string, entries: SlashCommandEntry[]): SlashCommandEntry | undefined {
  return entries.find(
    (e) =>
      e.name.toLowerCase() === name ||
      (e.aliases ?? []).some((a) => a.toLowerCase() === name),
  );
}

/** True when the manifest owns `name` — this covers the row-less commands
 * (slashRow false: /undo, /compact, /session, /graph, /workflow), which
 * have real handlers but no catalog row, so a fuzzy row walk never finds
 * them (stabilization 2026-10-21: typed /undo ran /bg, /graph ran /lineage). */
function manifestKnows(name: string): boolean {
  return MANIFEST_ROWS.some((row) => rowNames(row).includes(name));
}

/** What Enter in the palette runs: a slash line to dispatch, or a row id. */
export type PaletteEnter = { line: string } | { id: string } | null;

/**
 * Enter in the palette. An exact typed command always runs as typed — it is
 * never swapped for the top fuzzy row, which is a different command
 * whenever the typed one has no row (stabilization 2026-10-21, slash-c#0 /
 * chat#5). Order: a full line with args → an exact bare name (its own row
 * when listed, else the verbatim line) → the selected fuzzy row → with zero
 * rows, the verbatim line, so the dispatcher can say "Unknown slash
 * command" instead of Enter doing nothing.
 */
export function paletteEnterAction(
  query: string,
  entries: SlashCommandEntry[],
  rowIds: readonly string[],
  selectedId: string | undefined,
): PaletteEnter {
  const direct = paletteDirectLine(query, entries);
  if (direct) return { line: direct };
  const exact = paletteExactName(query, entries);
  if (exact) return rowIds.includes(`slash:${exact}`) ? { id: `slash:${exact}` } : { line: `/${exact}` };
  if (selectedId) return { id: selectedId };
  const search = paletteQueryMode(query).search.replace(/^\//, "").trim();
  return search ? { line: `/${search}` } : null;
}

/** A bare query that names a slash command exactly (row name, alias or a
 * manifest command): the catalog row's name, else the typed name. */
function paletteExactName(query: string, entries: SlashCommandEntry[]): string | null {
  const name = query.trim().replace(/^\//, "").trim().toLowerCase();
  if (!name || /\s/.test(name)) return null;
  const hit = catalogEntryFor(name, entries);
  if (hit) return hit.name;
  return manifestKnows(name) ? name : null;
}
