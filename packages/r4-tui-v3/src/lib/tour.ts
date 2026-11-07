/**
 * /tour — the full R4/UMRS function map.
 *
 * North-star flow intro is curated (short, stable). The function index is
 * BUILT FROM THE LIVE CATALOG (localSlashCommands) — the old hardcoded tour
 * went stale within weeks; a catalog-derived one can't.
 */

import type { SlashCommandEntry } from "../commands/slash";

interface SubEntry {
  name: string;
  help: string;
}

/** Core-flow narrative (F1–F4 from the 2026-09-15 core-flows plan). */
const CORE_FLOWS: string[] = [
  "— core flows (what r4/umrs is for) —",
  "  1. chat with registered endpoints — type a message; R4 answers via the active model",
  "  2. project-oriented knowledge build-up — every project owns a corpus + graph",
  "  3. agentic work control, local+remote — one Work view for runs/jobs/approvals",
  "  4. umrs workflow control — folded into the same Work view (executions + graph)",
  "",
];

const GROUP_TITLES: Array<{ category: string; title: string }> = [
  { category: "project", title: "project lifecycle (knowledge build)" },
  { category: "endpoint", title: "models · endpoints · operations" },
  { category: "companion", title: "session & science tools" },
  { category: "ui", title: "console · views · settings" },
];

/** Build the full tour. Every catalog command appears exactly once. */
export function buildTourLines(opts: {
  entries: SlashCommandEntry[];
  projectSubs: SubEntry[];
  endpointSubs: SubEntry[];
}): string[] {
  const lines: string[] = [...CORE_FLOWS];
  const seen = new Set<string>();
  const byCat = new Map<string, SlashCommandEntry[]>();
  for (const entry of opts.entries) {
    const bucket = byCat.get(entry.category) ?? [];
    bucket.push(entry);
    byCat.set(entry.category, bucket);
  }

  for (const { category, title } of GROUP_TITLES) {
    const entries = (byCat.get(category) ?? []).sort((a, b) => a.name.localeCompare(b.name));
    byCat.delete(category);
    if (entries.length === 0) continue;
    lines.push(`— ${title} —`);
    for (const entry of entries) {
      if (seen.has(entry.name)) continue;
      seen.add(entry.name);
      lines.push(`  /${entry.name} — ${entry.help}`);
      if (entry.name === "project") {
        for (const sub of opts.projectSubs) lines.push(`    · ${sub.name} — ${sub.help}`);
      }
      if (entry.name === "endpoint") {
        for (const sub of opts.endpointSubs) lines.push(`    · ${sub.name} — ${sub.help}`);
      }
      if (entry.subs && entry.subs.length > 0 && entry.name !== "project" && entry.name !== "endpoint") {
        lines.push(`    · ${entry.subs.map((s) => s.name).join(" · ")}`);
      }
    }
  }

  const rest = [...byCat.values()].flat().filter((e) => !seen.has(e.name));
  if (rest.length > 0) {
    lines.push("— more —");
    for (const entry of rest.sort((a, b) => a.name.localeCompare(b.name))) {
      lines.push(`  /${entry.name} — ${entry.help}`);
    }
  }

  lines.push("");
  lines.push("— everywhere —");
  lines.push("  Esc back/stop · Tab pane focus · ctrl+g transcript → c copy, s save, k knowledge · ! shell · @ file");
  lines.push("  approvals: y allow (remembers for session) · p always (project) · n reject (+feedback)");
  lines.push("  /theme <name> recolors (13 palettes, live preview) · /work opens the Work view");
  lines.push("  quit: /quit or Ctrl+Q");
  return lines;
}
