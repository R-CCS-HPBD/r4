/**
 * What a workflow needs at trigger time (2026-10-02): the run parameters a
 * registered package reveals, each with where it came from —
 *   schema   — the registered parameter_schema (JSON Schema: type, default,
 *              description/title, enum; the server enforces `required`)
 *   template — `{{ params.x }}` in a step config (LLMStep renders it against
 *              the step config, which the run's parameters fill)
 *   builtin  — the image a root VisionStep/OCRStep reads (`image_param`)
 *   code     — `config.get("x", default)` / `config["x"]` reads in the
 *              registered step code
 * — and the exact `/workflow trigger` line that runs it.
 *
 * The runner merges a run's parameters into every step's config as defaults
 * (umrs workflow_runner `_assemble_step_config`), so a key the step's own
 * config already sets is not a run parameter for that step, and the reserved
 * routing/credential keys never are. `project_id` is always filled from the
 * run's project and `input_files` from `--input-file`: both are flagged, not
 * listed. Everything outside the schema is inferred — the card says so.
 */
import type { WorkflowDefinitionDetail, WorkflowDefStep } from "./workflow-def-types";
import { workflowRefLabel } from "./workflow-versions";

export type ParamSource = "schema" | "template" | "builtin" | "code";

export interface WorkflowParam {
  name: string;
  type?: string;
  /** True when a run must supply a value (schema-required, or no default). */
  required: boolean;
  default?: unknown;
  description?: string;
  choices?: unknown[];
  sources: ParamSource[];
  /** Steps that read it (or the file, when no step's entrypoint names it). */
  usedBy: string[];
}

export interface WorkflowRunSpec {
  /** Required first, then optional; declaration order within each group. */
  params: WorkflowParam[];
  /** Step code reads `input_files` — the run should carry `--input-file`. */
  readsInputFiles: boolean;
  /** A step reads `project_id` — filled from the active project (or `--project`). */
  readsProject: boolean;
}

/** Keys a trigger parameter can never set (umrs `_PARAM_RESERVED_KEYS`) + runner-owned. */
const NOT_PARAMS = new Set(["api_base", "api_key", "headers", "_routing", "image_param", "step_name"]);
/** Filled by the platform / flags, never typed as `--param`. */
const AUTO_KEYS = new Set(["project_id", "input_files"]);

const TEMPLATE_PARAM_RE = /\{\{\s*params\.([A-Za-z_]\w*)/g;
const CODE_GET_RE = /\b(?:config|params|parameters)\.get\(\s*["']([A-Za-z_]\w*)["']\s*(,)?/g;
const CODE_INDEX_RE = /\b(?:config|params|parameters)\[\s*["']([A-Za-z_]\w*)["']\s*\]/g;
const IMAGE_STEP_RE = /^umrs\.pipeline_sdk\.templates\.(?:vision_step:VisionStep|ocr_step:OCRStep)$/;

type Draft = {
  name: string;
  type?: string;
  default?: unknown;
  description?: string;
  choices?: unknown[];
  sources: Set<ParamSource>;
  usedBy: Set<string>;
  schemaRequired: boolean;
  /** A read that has no fallback (template ref, `config["k"]`, root image). */
  needsValue: boolean;
};

function draftFor(map: Map<string, Draft>, name: string): Draft {
  let d = map.get(name);
  if (!d) {
    d = { name, sources: new Set(), usedBy: new Set(), schemaRequired: false, needsValue: false };
    map.set(name, d);
  }
  return d;
}

function stepConfig(step: WorkflowDefStep): Record<string, unknown> {
  return step.config && typeof step.config === "object" ? step.config : {};
}

/** Python literal in a `.get("k", <default>)` call → a JS value (best effort). */
export function parsePyDefault(raw: string): unknown {
  const text = raw.trim();
  if (text === "None") return null;
  if (text === "True") return true;
  if (text === "False") return false;
  if (/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(text)) return Number(text);
  const quoted = /^(['"])(.*)\1$/.exec(text);
  if (quoted) return quoted[2];
  return text; // an expression — shown verbatim
}

/** Text of the call argument starting at `from`, up to the `)` that closes the call. */
function callArgument(source: string, from: number): string {
  let depth = 0;
  let quote: string | null = null;
  for (let i = from; i < source.length; i++) {
    const ch = source[i]!;
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if ("([{".includes(ch)) depth++;
    else if (")]}".includes(ch)) {
      if (depth === 0) return source.slice(from, i);
      depth--;
    } else if (ch === "," && depth === 0) return source.slice(from, i);
  }
  return source.slice(from);
}

/** Steps whose entrypoint module is this file (`a.b:Cls` → `…a/b.py`). */
function stepsForFile(path: string, steps: WorkflowDefStep[]): WorkflowDefStep[] {
  return steps.filter((s) => {
    const module = (s.entrypoint ?? "").split(":")[0] ?? "";
    if (!module) return false;
    const rel = module.replace(/\./g, "/");
    return [`${rel}.py`, `${rel}/__init__.py`].some((tail) => path === tail || path.endsWith(`/${tail}`));
  });
}

function addSchema(map: Map<string, Draft>, d: WorkflowDefinitionDetail): void {
  const props = (d.parameter_schema?.properties ?? {}) as Record<string, unknown>;
  const required = new Set(d.parameter_schema?.required ?? []);
  for (const [name, raw] of Object.entries(props)) {
    const p = draftFor(map, name);
    p.sources.add("schema");
    const s = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    if (typeof s.type === "string") p.type = s.type;
    if ("default" in s) p.default = s.default;
    if (typeof s.description === "string") p.description = s.description;
    else if (typeof s.title === "string") p.description = s.title;
    if (Array.isArray(s.enum)) p.choices = s.enum;
    if (required.has(name)) p.schemaRequired = true;
  }
}

function addTemplates(map: Map<string, Draft>, steps: WorkflowDefStep[]): void {
  for (const step of steps) {
    const config = stepConfig(step);
    for (const m of JSON.stringify(config).matchAll(TEMPLATE_PARAM_RE)) {
      const name = m[1]!;
      if (name in config) continue; // the step's own config answers it
      const p = draftFor(map, name);
      p.sources.add("template");
      p.usedBy.add(step.name);
      p.needsValue = true; // renders empty without a value
    }
  }
}

function addBuiltins(map: Map<string, Draft>, steps: WorkflowDefStep[]): void {
  for (const step of steps) {
    if (!IMAGE_STEP_RE.test(step.entrypoint ?? "")) continue;
    const config = stepConfig(step);
    const key = typeof config.image_param === "string" && config.image_param ? config.image_param : "image";
    if (key in config) continue;
    const p = draftFor(map, key);
    p.sources.add("builtin");
    p.usedBy.add(step.name);
    p.description ??= "image to analyse (object key, URL or path)";
    // A root vision step has no parent output to fall back to.
    if (!step.depends_on || step.depends_on.length === 0) p.needsValue = true;
  }
}

interface CodeRead {
  name: string;
  /** Raw default expression; undefined = none given. */
  fallback?: string;
  /** `config["k"]` — raises without a value. */
  strict: boolean;
}

function codeReads(content: string): CodeRead[] {
  const reads: CodeRead[] = [];
  for (const m of content.matchAll(CODE_GET_RE)) {
    const fallback = m[2] ? callArgument(content, m.index! + m[0].length).trim() : undefined;
    reads.push({ name: m[1]!, fallback: fallback || undefined, strict: false });
  }
  for (const m of content.matchAll(CODE_INDEX_RE)) reads.push({ name: m[1]!, strict: true });
  return reads;
}

function addCode(map: Map<string, Draft>, d: WorkflowDefinitionDetail, spec: WorkflowRunSpec): void {
  const steps = d.steps ?? [];
  for (const file of d.files ?? []) {
    if (!file.path.endsWith(".py")) continue;
    const owners = stepsForFile(file.path, steps);
    for (const read of codeReads(file.content)) {
      if (read.name === "project_id") spec.readsProject = true;
      if (read.name === "input_files") spec.readsInputFiles = true;
      if (NOT_PARAMS.has(read.name) || AUTO_KEYS.has(read.name) || read.name.startsWith("_")) continue;
      // Steps this read can receive a run value in: the file's own steps that
      // do not set the key; a helper file counts only when no step sets it.
      const open = owners.length
        ? owners.filter((s) => !(read.name in stepConfig(s))).map((s) => s.name)
        : steps.some((s) => read.name in stepConfig(s))
          ? []
          : [file.path];
      if (open.length === 0) continue;
      const p = draftFor(map, read.name);
      p.sources.add("code");
      for (const where of open) p.usedBy.add(where);
      if (read.fallback !== undefined && p.default === undefined) p.default = parsePyDefault(read.fallback);
      if (read.strict) p.needsValue = true;
    }
  }
}

function finish(p: Draft, stepOrder: Map<string, number>): WorkflowParam {
  const hasDefault = p.default !== undefined;
  // The schema is authoritative where it speaks; otherwise a read with no
  // fallback and no default anywhere needs a value.
  const required = p.schemaRequired || (!p.sources.has("schema") && p.needsValue && !hasDefault);
  // Steps in declaration order, then helper files by path.
  const usedBy = [...p.usedBy].sort(
    (a, b) => (stepOrder.get(a) ?? Infinity) - (stepOrder.get(b) ?? Infinity) || a.localeCompare(b),
  );
  const out: WorkflowParam = { name: p.name, required, sources: [...p.sources], usedBy };
  if (p.type !== undefined) out.type = p.type;
  if (hasDefault) out.default = p.default;
  if (p.description !== undefined) out.description = p.description;
  if (p.choices !== undefined) out.choices = p.choices;
  return out;
}

/** Everything a run of `d` can carry, gathered from its registered package. */
export function workflowRunSpec(d: WorkflowDefinitionDetail): WorkflowRunSpec {
  const map = new Map<string, Draft>();
  const spec: WorkflowRunSpec = { params: [], readsInputFiles: false, readsProject: false };
  addSchema(map, d);
  addTemplates(map, d.steps ?? []);
  addBuiltins(map, d.steps ?? []);
  addCode(map, d, spec);
  for (const name of AUTO_KEYS) {
    if (!map.delete(name)) continue; // a flag, even when the schema lists it
    if (name === "project_id") spec.readsProject = true;
    else spec.readsInputFiles = true;
  }
  const stepOrder = new Map((d.steps ?? []).map((s, i) => [s.name, i]));
  const params = [...map.values()].map((p) => finish(p, stepOrder));
  spec.params = [...params.filter((p) => p.required), ...params.filter((p) => !p.required)];
  return spec;
}

/** A value as it goes on a `--param k=v` token (quoted when it has spaces). */
export function paramValueToken(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return /[\s"']/.test(text) ? JSON.stringify(text) : text;
}

/** Types `--param` (always text) cannot carry — those go in `--params-json`. */
export function needsJson(p: WorkflowParam): boolean {
  return p.type === "array" || p.type === "object";
}

/**
 * The trigger line the composer gets: every required parameter as
 * `--param k=` (the value to type); optional ones keep their defaults.
 * A `version` makes it `name@version` (ADR-113 D-3): the run is the
 * version whose parameters the card listed.
 */
export function triggerCommand(workflowName: string, params: WorkflowParam[], version?: string): string {
  const parts = [`/workflow trigger ${workflowRefLabel(workflowName, version)}`];
  for (const p of params) {
    if (p.required && !needsJson(p)) parts.push(`--param ${p.name}=`);
  }
  const json = params.filter((p) => p.required && needsJson(p));
  if (json.length) parts.push(`--params-json '{${json.map((p) => `"${p.name}": ${p.type === "array" ? "[]" : "{}"}`).join(", ")}}'`);
  return parts.join(" ");
}

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

const SOURCE_LABEL: Record<Exclude<ParamSource, "schema">, string> = {
  template: "prompt",
  builtin: "image input",
  code: "read in code",
};

/** One table row: `name*  type  = default  — description  (one of a|b)  [where]`. */
export function describeParam(p: WorkflowParam): string {
  const bits = [`${p.name}${p.required ? "*" : ""}`];
  if (p.type) bits.push(p.type);
  if (p.default !== undefined) bits.push(`= ${clip(paramValueToken(p.default), 40)}`);
  let row = bits.join("  ");
  if (p.description) row += `  — ${p.description}`;
  if (p.choices && p.choices.length) row += `  (one of ${p.choices.map((c) => paramValueToken(c)).join("|")})`;
  if (!p.sources.includes("schema")) {
    const how = p.sources.map((s) => SOURCE_LABEL[s as Exclude<ParamSource, "schema">]).join(", ");
    row += `  [${how}${p.usedBy.length ? ` · ${p.usedBy.join(", ")}` : ""}]`;
  }
  return row;
}
