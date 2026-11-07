/**
 * F12 help overlay read-model — pure row builder so the display contract
 * stays unit-testable (same pattern as which-key.ts).
 *
 * FULL help, no truncation: every catalog command with its help text, every
 * declared subcommand row, every registered keymap row. The overlay wraps
 * these rows in a scrollbox — nothing is sliced to fit (the first revision
 * cut category rows at 86 chars and capped the keymap at 14 rows, hiding
 * half the verbs: /usage, /workflows, /loops, /rag… never appeared).
 */

import type { SlashCommandEntry } from "../commands/slash";

export type HelpRowTone = "accent" | "dim" | "normal";

export interface HelpRow {
  text: string;
  tone: HelpRowTone;
}

export interface HelpSubEntry {
  name: string;
  help: string;
}

/** Category display order — mirrors the /tour map (tour.ts GROUP_TITLES). */
const CATEGORY_ORDER = ["project", "endpoint", "companion", "ui"] as const;

/** Main chords legend — the only one the overlay ships (copy never drifts). */
export const HELP_CHORDS: { key: string; meaning: string }[] = [
  { key: "Ctrl+P", meaning: "command palette" },
  { key: "Ctrl+/ Ctrl+.", meaning: "slash picker (compose with slash-args)" },
  { key: "Ctrl+Q", meaning: "quit (confirm on second press)" },
  { key: "Esc", meaning: "close topmost layer (pane → overlay → menu)" },
  { key: "Enter", meaning: "send chat / activate menu idx / allow once (approval card)" },
  { key: "y·a·p·n·r", meaning: "approval card: once / always this session / always this project / reject with feedback / reject (Esc on the card rejects)" },
  { key: "Tab·Shift+Tab", meaning: "into a docked pane · Shift+Tab: mode chat → plan → autopilot (research: cycle graph/agents/verify)" },
  { key: "F12", meaning: "open this overlay (this one)" },
  { key: "Ctrl+C · Ctrl+D", meaning: "interrupt → clear prompt → twice exits · exit (twice, empty prompt)" },
  { key: "Esc Esc · Ctrl+R · Ctrl+S", meaning: "stop a running turn (one Esc only arms) · clear (kept in history) / rewind menu · history search · stash" },
  { key: "Ctrl+O · Ctrl+T · Alt+T", meaning: "verbose transcript · to-do checklist · thinking" },
  { key: "PgUp · PgDn", meaning: "page the transcript (Ctrl+↑ selects a message: c copy · s save to knowledge · r retry · e edit)" },
  { key: "Ctrl+B · Ctrl+L", meaning: "background the turn (/bg) · redraw" },
  { key: "Alt+W", meaning: "live DAG of the running workflow (status-bar ▶ badge) · /workflow graph [id|latest]" },
  { key: "/btw · Ctrl+Enter · ↑", meaning: "side question (no tools, not recorded) · send queued now · take back queued" },
];

/**
 * Build every overlay row: header, chords, the FULL slash catalog grouped by
 * category (one row per command with help text + one row per subcommand),
 * then the full live keymap. No row is ever dropped for length — horizontal
 * overflow stays clipped per-row (height-1 boxes), vertical room comes from
 * the overlay's scrollbox.
 */
export function buildHelpRows(opts: {
  entries: SlashCommandEntry[];
  keyRows: string[];
  projectSubs: HelpSubEntry[];
}): HelpRow[] {
  const rows: HelpRow[] = [];
  const push = (text: string, tone: HelpRowTone = "normal") => rows.push({ text, tone });

  push("help — F12 opens it · Esc closes · /help chat alias", "accent");
  push("", "dim");
  push("Main chords:", "dim");
  for (const chord of HELP_CHORDS) {
    push(`  ${chord.key.padEnd(24, " ")} ${chord.meaning}`);
  }
  push("", "dim");
  push(
    `Slash catalog (${opts.entries.length} verbs — live table, same rows as /tour map):`,
    "dim",
  );

  const byCat = new Map<string, SlashCommandEntry[]>();
  for (const entry of opts.entries) {
    const key = entry.category ?? "other";
    const bucket = byCat.get(key) ?? [];
    bucket.push(entry);
    byCat.set(key, bucket);
  }
  const orderedCats = [
    ...CATEGORY_ORDER.filter((cat) => byCat.has(cat)),
    ...[...byCat.keys()]
      .filter((cat) => !(CATEGORY_ORDER as readonly string[]).includes(cat))
      .sort(),
  ];

  for (const cat of orderedCats) {
    const entries = (byCat.get(cat) ?? []).sort((a, b) => a.name.localeCompare(b.name));
    push(`◇ ${cat} (${entries.length})`, "accent");
    for (const entry of entries) {
      // /project carries its verb table on the Bun-only PROJECT_SUBCOMMANDS
      // (never on entry.subs — the slash bar owns that grammar hardcoded).
      const subs: HelpSubEntry[] =
        entry.name === "project" ? opts.projectSubs : (entry.subs ?? []);
      // argsHint only when no sub rows follow — sub rows already carry usage.
      const usage = entry.argsHint && subs.length === 0 ? ` ${entry.argsHint}` : "";
      const aliases = entry.aliases?.length
        ? ` (alias: ${entry.aliases.map((a) => `/${a}`).join(", ")})`
        : "";
      push(`  /${entry.name}${usage} — ${entry.help}${aliases}`);
      for (const sub of subs) {
        push(`    · ${sub.name} — ${sub.help}`, "dim");
      }
    }
  }

  push("", "dim");
  push("Live keyboard map (live keymap registry):", "dim");
  for (const row of opts.keyRows) {
    push(`  ${row}`, "dim");
  }
  push("", "dim");
  push("Esc closes; errors here NEVER stand in for input — they only describe.", "dim");
  return rows;
}
