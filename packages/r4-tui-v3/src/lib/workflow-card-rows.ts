/**
 * Rows of the workflow-definition card (2026-10-02): everything a user needs
 * to run a registered workflow, top to bottom —
 *   meta        name, about, tags, resources, requirements, triggers, source
 *   how to run  the exact `/workflow trigger` line (r puts it in the composer)
 *   parameters  each run parameter: type, default, description, where read
 *   graph       box-and-arrow drawing of the steps the runner executes
 *   steps       per step: what it waits for, entrypoint, config, needs
 *   code        registered source files, collapsed until asked
 * One flat list of one-line rows so the card scrolls as a document and an
 * expanded file can be scrolled to by index. Pure — the overlay renders it.
 */
import type { WorkflowDefinitionDetail, WorkflowDefStep } from "./workflow-def-types";
import { effectiveSteps, renderDag } from "./dag-layout";
import { describeParam, needsJson, triggerCommand, workflowRunSpec, type WorkflowParam } from "./workflow-params";
import {
  codeUnavailable,
  defaultVersionOf,
  serverListsVersions,
  shortDigest,
  versionListLabel,
} from "./workflow-versions";

export type CardTone = "fg" | "dim" | "accent" | "bright";

export interface CardRow {
  text: string;
  tone: CardTone;
  /** Set on a code file's header row (toggle target). */
  file?: string;
  /** The code file row under the cursor. */
  selected?: boolean;
}

export interface CardOptions {
  /** Columns available for a row (graph lines included). */
  width: number;
  /** Expanded code file path (null = all collapsed). */
  expanded: string | null;
  /** Index of the selected code file. */
  cursor: number;
}

/** Most lines of one expanded file shown in the card. */
export const CODE_LINES_MAX = 400;

const INDENT = "  ";

function kv(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, Math.max(1, max - 1))}…`;
}

/** The `tags` line: version, scope ("shared" for a workflow shared with the
 * caller, which keeps its owner's scope "user"), owner, execution mode. */
function metaTags(d: WorkflowDefinitionDetail): string[] {
  const scope = d.shared ? "shared" : d.scope;
  const tags = [d.version ? `v${d.version}` : "", scope ?? "", d.user_id ? `owner ${d.user_id}` : ""];
  tags.push(d.execution_mode ?? "");
  return tags.filter(Boolean);
}

/** The `resources` line: top-level resources, then the `extra` map folded in. */
function resourceParts(resources: Record<string, unknown>): string[] {
  const present = ([k, v]: [string, unknown]) => k !== "extra" && v !== undefined && v !== "";
  const res = Object.entries(resources).filter(present).map(([k, v]) => `${k}=${kv(v)}`);
  const extra = resources.extra;
  if (!extra || typeof extra !== "object") return res;
  for (const [k, v] of Object.entries(extra as Record<string, unknown>)) {
    if (v !== undefined && v !== "") res.push(`${k}=${kv(v)}`);
  }
  return res;
}

function metaRows(d: WorkflowDefinitionDetail): CardRow[] {
  const rows: string[] = [`workflow · ${d.workflow_name}`];
  if (d.description) rows.push(`about     · ${d.description}`);
  const tags = metaTags(d);
  if (tags.length) rows.push(`tags      · ${tags.join(" · ")}`);
  const res = resourceParts(d.resources ?? {});
  if (res.length) rows.push(`resources · ${res.join(" · ")}`);
  if (d.requirements?.length) rows.push(`requires  · ${d.requirements.join(" · ")}`);
  if (d.triggers?.length) rows.push(`triggers  · ${d.triggers.length} subscription(s)`);
  if (d.source_path) rows.push(`source    · ${d.source_path}`);
  return [...rows.map((text) => ({ text, tone: "fg" as const })), ...versionRows(d)];
}

/** ADR-113: every registered version (v in the card picks one), the code
 * digest, and a version whose files are gone marked before anything else. */
function versionRows(d: WorkflowDefinitionDetail): CardRow[] {
  const rows: CardRow[] = [];
  const versions = d.versions ?? [];
  if (versions.length) {
    const pick = versions.length > 1 ? "  — v / V picks another" : "";
    rows.push({ text: `versions  · ${versionListLabel(versions, defaultVersionOf(versions), d.version)}${pick}`, tone: "fg" });
  }
  if (d.source_sha256) rows.push({ text: `digest    · ${shortDigest(d.source_sha256)}`, tone: "dim" });
  if (codeUnavailable(d)) {
    rows.push({ text: "code      · unavailable — this version's files are gone; running it fails", tone: "bright" });
  }
  return rows;
}

/** The version a trigger line pins: the one the card shows, when the server
 * lists versions (an older server takes the bare name, as before). */
export function cardVersion(d: WorkflowDefinitionDetail): string | undefined {
  return serverListsVersions(d) && d.version ? d.version : undefined;
}

const TYPED = new Set(["integer", "number", "boolean"]);

function howToRunRows(d: WorkflowDefinitionDetail, params: WorkflowParam[], spec: ReturnType<typeof workflowRunSpec>): CardRow[] {
  const rows: CardRow[] = [
    { text: "how to run", tone: "accent" },
    { text: INDENT + triggerCommand(d.workflow_name, params, cardVersion(d)), tone: "bright" },
  ];
  const required = params.filter((p) => p.required);
  if (required.length) {
    rows.push({ text: `${INDENT}r  puts this line in the composer: fill each value after "=", then Enter`, tone: "dim" });
    rows.push({ text: `${INDENT}   quote a value with spaces: --param ${required[0]!.name}="two words"`, tone: "dim" });
  } else {
    rows.push({ text: `${INDENT}r  puts this line in the composer · --param k=v overrides a default`, tone: "dim" });
  }
  if (params.some((p) => p.type !== undefined && TYPED.has(p.type)) || params.some(needsJson)) {
    rows.push({
      text: `${INDENT}--param sends text · numbers, booleans, lists: --params-json '{"k": 1}'`,
      tone: "dim",
    });
  }
  if (spec.readsInputFiles) rows.push({ text: `${INDENT}reads input files: add --input-file KEY (repeatable)`, tone: "dim" });
  if (spec.readsProject) {
    rows.push({ text: `${INDENT}project_id comes from your active project (--project ID for another)`, tone: "dim" });
  }
  rows.push({ text: `${INDENT}more: --input-file KEY · --project ID · --wait (until it finishes)`, tone: "dim" });
  return rows;
}

function paramRows(params: WorkflowParam[]): CardRow[] {
  const rows: CardRow[] = [{ text: `parameters (${params.length})${params.length ? "  * = needs a value" : ""}`, tone: "accent" }];
  if (!params.length) {
    rows.push({ text: `${INDENT}none declared, templated or read in code — it runs as is`, tone: "dim" });
    return rows;
  }
  for (const p of params) rows.push({ text: INDENT + describeParam(p), tone: "fg" });
  if (params.some((p) => !p.sources.includes("schema"))) {
    rows.push({
      text: `${INDENT}[…] = read from step prompts or code, not declared in a schema (best guess)`,
      tone: "dim",
    });
  }
  return rows;
}

/** Labels shrink until the drawing fits; null when even short labels do not. */
function drawGraph(steps: WorkflowDefStep[], width: number) {
  for (const labelMax of [28, 18, 12]) {
    const drawing = renderDag(steps, { maxWidth: width, labelMax });
    if (drawing.fits) return drawing;
  }
  return null;
}

function graphRows(d: WorkflowDefinitionDetail, width: number): CardRow[] {
  const declared = d.steps ?? [];
  if (!declared.length) return [];
  const { steps, linear } = effectiveSteps(declared);
  const rows: CardRow[] = [
    { text: `graph (${declared.length} steps)${linear ? " · no depends_on: runs in declaration order" : ""}`, tone: "accent" },
  ];
  const drawing = drawGraph(steps, width - INDENT.length);
  if (drawing) for (const line of drawing.lines) rows.push({ text: INDENT + line, tone: "fg" });
  else rows.push({ text: `${INDENT}too wide for this terminal — the step list below has every edge`, tone: "dim" });
  return rows;
}

function configSummary(config: Record<string, unknown>, width: number): string {
  const entries = Object.entries(config).filter(([k]) => !k.startsWith("_"));
  const shown = entries.slice(0, 6).map(([k, v]) => `${k}=${clip(kv(v), 32)}`);
  const more = entries.length > shown.length ? ` · +${entries.length - shown.length} more` : "";
  return clip(`config · ${shown.join(" · ")}${more}`, Math.max(20, width));
}

function stepRows(d: WorkflowDefinitionDetail, width: number): CardRow[] {
  const declared = d.steps ?? [];
  if (!declared.length) return [];
  const deps = new Map(effectiveSteps(declared).steps.map((s) => [s.name, s.depends_on ?? []]));
  const rows: CardRow[] = [{ text: "steps", tone: "accent" }];
  for (const step of declared) {
    const after = deps.get(step.name) ?? [];
    rows.push({ text: `${INDENT}● ${step.name}${after.length ? `  (after ${after.join(", ")})` : ""}`, tone: "fg" });
    if (step.entrypoint) rows.push({ text: `${INDENT}  ↳ ${step.entrypoint}`, tone: "dim" });
    const config = step.config && typeof step.config === "object" ? step.config : {};
    if (Object.keys(config).length) rows.push({ text: `${INDENT}    ${configSummary(config, width - 6)}`, tone: "dim" });
    const needs = [...new Set([step.model_type, ...(step.requires ?? [])])].filter((v): v is string => Boolean(v));
    if (needs.length) rows.push({ text: `${INDENT}    needs · ${needs.join(" · ")}`, tone: "dim" });
  }
  return rows;
}

function codeRows(d: WorkflowDefinitionDetail, opts: CardOptions): CardRow[] {
  const files = d.files ?? [];
  const rows: CardRow[] = [
    { text: `code (${files.length} files${d.files_truncated ? " · server-capped" : ""})`, tone: "accent" },
  ];
  if (!files.length) {
    rows.push({ text: `${INDENT}no registered source files (missing source dir)`, tone: "dim" });
    return rows;
  }
  const cursor = Math.min(opts.cursor, files.length - 1);
  files.forEach((file, index) => {
    const open = opts.expanded === file.path;
    rows.push({
      text: `${INDENT}${open ? "▾" : "▸"} ${file.path} · ${file.lines} lines · ${(file.bytes / 1024).toFixed(1)}K${file.truncated ? " · capped" : ""}`,
      tone: index === cursor ? "bright" : "fg",
      file: file.path,
      selected: index === cursor,
    });
    if (!open) return;
    const lines = file.content.split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    for (const line of lines.slice(0, CODE_LINES_MAX)) rows.push({ text: `${INDENT}    ${line}`, tone: "dim" });
    if (lines.length > CODE_LINES_MAX) {
      rows.push({ text: `${INDENT}    … ${lines.length - CODE_LINES_MAX} more lines`, tone: "dim" });
    }
  });
  return rows;
}

const BLANK: CardRow = { text: "", tone: "fg" };

/** The card body for one workflow package. */
export function workflowCardRows(d: WorkflowDefinitionDetail, opts: CardOptions): CardRow[] {
  const spec = workflowRunSpec(d);
  const sections = [
    metaRows(d),
    howToRunRows(d, spec.params, spec),
    paramRows(spec.params),
    graphRows(d, opts.width),
    stepRows(d, opts.width),
    codeRows(d, opts),
  ].filter((rows) => rows.length);
  return sections.flatMap((rows, i) => (i ? [BLANK, ...rows] : rows));
}

/** The trigger line `r` (and the work pane's `t`) put in the composer. */
export function cardTriggerCommand(d: WorkflowDefinitionDetail): { command: string; required: number } {
  const { params } = workflowRunSpec(d);
  return {
    command: triggerCommand(d.workflow_name, params, cardVersion(d)),
    required: params.filter((p) => p.required).length,
  };
}
