#!/usr/bin/env node
/**
 * Console ↔ VS Code parity matrix (ADR-103 D-4).
 *
 * Source:    vscode-extension/parity/console-parity.json (hand-classified rows)
 * Generated: vscode-extension/PARITY.md (the readable view)
 *
 * Console functions come from files the console keeps current:
 *   - slash rows: packages/r4-tui-v3/src/commands/catalog.manifest.json
 *     (console-owned verbs) and packages/r4-tui-v3/tests/fixtures/
 *     python-slash-registry.json (the bridge's registry + catalog);
 *   - pane rows: packages/r4-tui-v3/src/routes/*.tsx plus the three views
 *     that are not routes (overview, logs, term);
 *   - interaction rows (cards, chips, keys): curated in the JSON itself.
 *
 *   node scripts/parity-matrix.mjs --write   add new console rows as `missing`
 *                                            (wave "triage"), drop rows the
 *                                            console no longer has, rewrite PARITY.md
 *   node scripts/parity-matrix.mjs --check   exit 1 on drift or invalid rows
 */

import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const EXT_ROOT = resolve(HERE, "..");
export const REPO_ROOT = resolve(EXT_ROOT, "..");
export const MATRIX_PATH = join(EXT_ROOT, "parity", "console-parity.json");
export const PARITY_MD_PATH = join(EXT_ROOT, "PARITY.md");

const MANIFEST = "packages/r4-tui-v3/src/commands/catalog.manifest.json";
const REGISTRY = "packages/r4-tui-v3/tests/fixtures/python-slash-registry.json";
const ROUTES = "packages/r4-tui-v3/src/routes";
const NON_ROUTE_PANES = ["overview", "logs", "term"];

export const STATUSES = ["done", "partial", "native", "n/a", "missing"];
const GENERATED_KINDS = new Set(["slash", "pane"]);

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

/** Slash rows: console-owned manifest rows (minus slashRow:false sub-verbs)
 * plus every registry and catalog name the bridge serves. */
function slashSources(repoRoot) {
  const sources = new Map();
  const add = (name, source) => {
    const prev = sources.get(name);
    sources.set(name, prev && prev !== source ? "both" : source);
  };
  for (const row of readJson(join(repoRoot, MANIFEST)).rows) {
    if (row.slashRow === false) continue;
    add(row.name, "console");
  }
  const fixture = readJson(join(repoRoot, REGISTRY));
  const bridgeNames = new Set([...fixture.registry, ...fixture.catalog].map((r) => r.name));
  for (const name of bridgeNames) add(name, "bridge");
  return sources;
}

/** The console function ids the matrix must classify (slash + pane). */
export function consoleRows(repoRoot = REPO_ROOT) {
  const rows = new Map();
  for (const [name, source] of slashSources(repoRoot)) {
    rows.set(`slash:/${name}`, { kind: "slash", source });
  }
  const routes = readdirSync(join(repoRoot, ROUTES))
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => f.replace(/\.tsx$/, ""));
  for (const pane of [...routes, ...NON_ROUTE_PANES]) {
    rows.set(`pane:${pane}`, { kind: "pane", source: "console" });
  }
  return rows;
}

/** Add new generated rows, drop generated rows the console lost. */
export function syncMatrix(matrix, generated) {
  const added = [];
  const removed = [];
  const kept = [];
  for (const row of matrix.rows) {
    if (GENERATED_KINDS.has(row.kind) && !generated.has(row.id)) {
      removed.push(row.id);
      continue;
    }
    const gen = generated.get(row.id);
    kept.push(gen ? { ...row, source: gen.source } : row);
  }
  const known = new Set(kept.map((r) => r.id));
  for (const [id, gen] of generated) {
    if (known.has(id)) continue;
    added.push(id);
    kept.push({ id, kind: gen.kind, source: gen.source, status: "missing", wave: "triage" });
  }
  kept.sort(compareRows);
  return { matrix: { ...matrix, rows: kept }, added, removed };
}

const KIND_ORDER = { pane: 0, slash: 1, interaction: 2 };
function compareRows(a, b) {
  return (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || a.id.localeCompare(b.id);
}

function rowProblems(row, repoRoot) {
  const out = [];
  if (!STATUSES.includes(row.status)) out.push(`${row.id}: unknown status "${row.status}"`);
  if (row.status === "missing" && !row.wave) out.push(`${row.id}: missing row needs a wave`);
  if (["done", "partial"].includes(row.status) && !row.evidence) {
    out.push(`${row.id}: ${row.status} row needs evidence`);
  }
  if (["partial", "native", "n/a"].includes(row.status) && !row.note) {
    out.push(`${row.id}: ${row.status} row needs a note`);
  }
  if (row.evidence && !existsSync(join(repoRoot, row.evidence))) {
    out.push(`${row.id}: evidence ${row.evidence} does not exist`);
  }
  return out;
}

/** Every problem with the matrix as it stands (empty = valid). */
export function validateMatrix(matrix, repoRoot = REPO_ROOT) {
  const problems = [];
  const seen = new Set();
  for (const row of matrix.rows) {
    if (seen.has(row.id)) problems.push(`${row.id}: duplicate row`);
    seen.add(row.id);
    problems.push(...rowProblems(row, repoRoot));
  }
  const missing = matrix.rows.filter((r) => r.status === "missing").length;
  if (missing > matrix.missing_max) {
    problems.push(`missing rows ${missing} > missing_max ${matrix.missing_max} (the ceiling only goes down)`);
  }
  return problems;
}

export function countBy(rows, key) {
  const counts = {};
  for (const row of rows) counts[row[key]] = (counts[row[key]] ?? 0) + 1;
  return counts;
}

function cell(text) {
  return String(text ?? "").replace(/\|/g, "\\|");
}

/** PARITY.md, generated from the matrix. */
export function renderMarkdown(matrix) {
  const lines = [
    "# Console ↔ VS Code parity",
    "",
    "Generated by `node scripts/parity-matrix.mjs --write` from",
    "[`parity/console-parity.json`](parity/console-parity.json); do not edit by hand.",
    "Decision: [ADR-103](../docs/adr/103-vscode-console-parity.md).",
    "",
    `Missing ceiling: **${matrix.missing_max}** (only goes down).`,
    "",
    "| Kind | " + STATUSES.join(" | ") + " | total |",
    "|---|" + STATUSES.map(() => "---:").join("|") + "|---:|",
  ];
  for (const kind of ["pane", "slash", "interaction"]) {
    const rows = matrix.rows.filter((r) => r.kind === kind);
    const counts = countBy(rows, "status");
    lines.push(`| ${kind} | ${STATUSES.map((s) => counts[s] ?? 0).join(" | ")} | ${rows.length} |`);
  }
  const all = countBy(matrix.rows, "status");
  lines.push(`| **all** | ${STATUSES.map((s) => all[s] ?? 0).join(" | ")} | ${matrix.rows.length} |`);
  for (const kind of ["pane", "slash", "interaction"]) {
    lines.push("", `## ${kind}`, "", "| Row | Source | Status | Wave | Evidence / note |", "|---|---|---|---|---|");
    for (const row of matrix.rows.filter((r) => r.kind === kind)) {
      const detail = [row.evidence ? `\`${row.evidence}\`` : "", row.note ?? ""].filter(Boolean).join(" — ");
      lines.push(`| \`${row.id}\` | ${cell(row.source)} | ${row.status} | ${cell(row.wave ?? "")} | ${cell(detail)} |`);
    }
  }
  return lines.join("\n") + "\n";
}

export function loadMatrix(path = MATRIX_PATH) {
  return readJson(path);
}

function main(argv) {
  const write = argv.includes("--write");
  const { matrix, added, removed } = syncMatrix(loadMatrix(), consoleRows());
  const problems = validateMatrix(matrix);
  const markdown = renderMarkdown(matrix);
  if (write) {
    writeFileSync(MATRIX_PATH, JSON.stringify(matrix, null, 2) + "\n");
    writeFileSync(PARITY_MD_PATH, markdown);
  } else {
    if (added.length) problems.push(`unclassified console rows: ${added.join(", ")} (run --write, then classify)`);
    if (removed.length) problems.push(`rows the console no longer has: ${removed.join(", ")} (run --write)`);
    const current = existsSync(PARITY_MD_PATH) ? readFileSync(PARITY_MD_PATH, "utf8") : "";
    if (current !== markdown) problems.push("PARITY.md is stale (run --write)");
  }
  const counts = countBy(matrix.rows, "status");
  console.log(`parity: ${matrix.rows.length} rows — ${STATUSES.map((s) => `${s} ${counts[s] ?? 0}`).join(", ")}`);
  for (const p of problems) console.error(`parity: ${p}`);
  return problems.length ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)));
}
