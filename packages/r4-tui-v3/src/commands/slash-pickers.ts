/**
 * Interactive slash pickers for commands that need a selection in the Bun TUI.
 * Mirrors companion-tui-js slash-routing + list_up_models / list_stoppable_endpoints.
 */

import { workspace, activeTab, actions } from "../stores";
import type { UiEndpoint, UiModel } from "../types";
import { getCompanionBridge } from "../bridge";
import { bridgeErrorHint } from "../bridge/jsonl-client";
import { isOfflineBackend } from "../lib/console-mode";
import { applyTheme, currentThemeName, listThemes } from "../themes";
import { parseSlashLine, tokenizeSlashArgs } from "./slash";

function bridgeEndpointStatus(status?: string): UiEndpoint["status"] {
  switch (status) {
    case "ready":
    case "provisioning":
    case "degraded":
    case "failed":
    case "stopped":
      return status;
    default:
      return "degraded";
  }
}

export interface SlashPickerItem {
  /** Value appended to commandPrefix when dispatching (index or id). */
  key: string;
  label: string;
  /** Full model id for set_model dispatch. */
  modelName?: string;
  /** Optional group key (source name) for section headers. */
  group?: string;
}

export interface SlashPickerRequest {
  title: string;
  commandPrefix: string;
  items: SlashPickerItem[];
  mode?: "slash" | "set_model";
  /** Fired whenever the highlighted row moves (before Enter) — live preview. */
  onHighlight?: (item: SlashPickerItem) => void;
  /** Fired on Esc/close-without-Enter — e.g. restore a previewed theme. */
  onCancel?: () => void;
}

const ENDPOINT_UP_SUBCOMMANDS = new Set(["up", "start", "provision"]);
const ENDPOINT_DOWN_SUBCOMMANDS = new Set(["down", "stop", "delete", "deprovision"]);
const UP_FLAGS = new Set(["--new", "--force", "--no-reuse"]);
// Mirrors r4/tui/commands.py _DOWN_TERMINAL_STATUSES (the bridge-error
// fallback must offer the rows the companion would; TUI audit 2026-10-17).
const DOWN_TERMINAL = new Set(["failed", "terminated", "expired", "deprovisioned"]);

function isListPickerTrigger(arg: string | undefined): boolean {
  if (!arg) return true;
  const verb = arg.toLowerCase();
  return verb === "list" || verb === "ls";
}

/** When set, open the registry provision picker (companion contract). */
export function parseUpPickerIntent(line: string): { requestNew: boolean } | null {
  const parsed = parseSlashLine(line);
  if (!parsed) return null;

  let rest: string[];
  if (parsed.name === "up") {
    rest = tokenizeSlashArgs(parsed.args);
  } else if (parsed.name === "endpoint") {
    const tokens = tokenizeSlashArgs(parsed.args);
    const sub = tokens[0]?.toLowerCase() ?? "";
    if (!ENDPOINT_UP_SUBCOMMANDS.has(sub)) return null;
    rest = tokens.slice(1);
  } else {
    return null;
  }

  let requestNew = false;
  let selector: string | undefined;
  for (const token of rest) {
    if (UP_FLAGS.has(token)) {
      requestNew = true;
      continue;
    }
    selector = token;
    break;
  }
  if (selector && !isListPickerTrigger(selector)) return null;
  return { requestNew };
}

/** When set, open the endpoint teardown picker (companion contract). */
export function parseDownPickerIntent(line: string): { force: boolean } | null {
  const parsed = parseSlashLine(line);
  if (!parsed) return null;

  let rest: string[];
  if (parsed.name === "down") {
    rest = tokenizeSlashArgs(parsed.args);
  } else if (parsed.name === "endpoint") {
    const tokens = tokenizeSlashArgs(parsed.args);
    const sub = tokens[0]?.toLowerCase() ?? "";
    if (!ENDPOINT_DOWN_SUBCOMMANDS.has(sub)) return null;
    rest = tokens.slice(1);
  } else {
    return null;
  }

  let force = false;
  let selector: string | undefined;
  for (const token of rest) {
    if (token === "--force") {
      force = true;
      continue;
    }
    selector = token;
    break;
  }
  if (selector && !isListPickerTrigger(selector)) return null;
  return { force };
}

/** @deprecated Use parseUpPickerIntent / parseDownPickerIntent. */
export function parseEndpointInvocation(
  line: string,
): { sub: string; args: string[] } | null {
  const up = parseUpPickerIntent(line);
  if (up) return { sub: "up", args: up.requestNew ? ["--new"] : [] };
  const down = parseDownPickerIntent(line);
  if (down) return { sub: "down", args: down.force ? ["--force"] : [] };
  const parsed = parseSlashLine(line);
  if (!parsed || parsed.name !== "endpoint") return null;
  const tokens = tokenizeSlashArgs(parsed.args);
  if (tokens.length === 0) return { sub: "help", args: [] };
  return { sub: tokens[0]!.toLowerCase(), args: tokens.slice(1) };
}

function registryModelItems(
  models: Array<{ name: string; label?: string }>,
): SlashPickerItem[] {
  return models.map((model, index) => ({
    key: String(index + 1),
    label: model.label ?? `[${index + 1}] ${model.name}`,
  }));
}

function workspaceModelItems(models: UiModel[]): SlashPickerItem[] {
  return models.map((model, index) => ({
    key: String(index + 1),
    label: `[${index + 1}] ${model.name}${model.status ? ` (${model.status})` : ""}`,
  }));
}

function stoppableEndpoints(endpoints: UiEndpoint[]): UiEndpoint[] {
  // A configured public source is not a provisioned endpoint: `/endpoint
  // down` offered it as "unknown @ <url> (stopped)" (wave 82).
  return endpoints.filter((ep) => {
    const status = (ep.status ?? "").toLowerCase();
    return !ep.isPublic && status && !DOWN_TERMINAL.has(status);
  });
}

/** The key is the instance id: `/endpoint down <N>` resolved N against a
 * freshly fetched list that can shift, stopping the wrong endpoint
 * (TUI audit 2026-10-17). [N] stays in the label for display only. */
function endpointDownItems(endpoints: UiEndpoint[]): SlashPickerItem[] {
  return endpoints.map((ep, index) => {
    const host = ep.host && ep.port ? `${ep.host}:${ep.port}` : ep.url || ep.id;
    return {
      key: ep.id,
      label: `[${index + 1}] ${ep.model} @ ${host} (${ep.status})`,
    };
  });
}

function upPickerFromModels(
  models: Array<{ name: string; label?: string }>,
  requestNew: boolean,
): SlashPickerRequest | null {
  if (models.length === 0) return null;
  const prefix = requestNew ? "/endpoint up --new " : "/endpoint up ";
  return {
    title: requestNew
      ? "Provision new endpoint — pick a registry model"
      : "Provision endpoint — pick a registry model",
    commandPrefix: prefix,
    items: registryModelItems(models),
  };
}

/** Why the last endpoint up/down picker found nothing to list ("" = it
 * listed fine). A failed gateway call (signed out, key rejected) used to
 * read as an empty registry / no endpoints and send the user back to
 * /endpoint up (stabilization 2026-10-21, slash-a#0). */
let endpointPickerFailureText = "";

export function endpointPickerFailure(): string {
  return endpointPickerFailureText;
}

/** A rejected bridge request as one line: its message, then its hint. */
function bridgeFailureText(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const hint = bridgeErrorHint(err);
  return hint ? `${message} → ${hint}` : message;
}

async function fetchRegistryModels(): Promise<Array<{ name: string; label?: string }>> {
  if (isOfflineBackend()) {
    return workspace.models.map((m) => ({ name: m.name, label: `[offline] ${m.name}` }));
  }
  try {
    const bridge = getCompanionBridge();
    const result = (await bridge.request("list_up_models", {}, 30_000)) as {
      models?: Array<{ name: string; label?: string }>;
    };
    return result.models ?? [];
  } catch (err) {
    endpointPickerFailureText = `/endpoint up: could not list registry models — ${bridgeFailureText(err)}`;
    return workspace.models.map((m) => ({ name: m.name }));
  }
}

async function fetchStoppableEndpoints(): Promise<UiEndpoint[]> {
  if (isOfflineBackend()) {
    return stoppableEndpoints(workspace.endpoints);
  }
  try {
    const bridge = getCompanionBridge();
    const result = (await bridge.request("list_stoppable_endpoints", {}, 30_000)) as {
      endpoints?: Array<{ id: string; label?: string; status?: string }>;
    };
    return (result.endpoints ?? []).map((ep) => ({
      id: ep.id,
      model: ep.label?.split("@")[0]?.trim() ?? ep.id,
      servedModelName: ep.id,
      status: bridgeEndpointStatus(ep.status),
      url: "",
      tier: "user" as const,
      backend: "unknown",
      host: "",
      port: 0,
      createdAt: new Date().toISOString(),
    }));
  } catch (err) {
    endpointPickerFailureText = `/endpoint down: could not list endpoints — ${bridgeFailureText(err)}`;
    return stoppableEndpoints(workspace.endpoints);
  }
}

/** The gateway's own failure when list_models came back empty: the bridge
 * swallows it there (an unreachable gateway must not hide public sources),
 * so ask the same endpoint listing /endpoint down uses, which reports it. */
async function gatewayListProblem(): Promise<string[]> {
  try {
    await getCompanionBridge().request("list_stoppable_endpoints", {}, 30_000);
    return [];
  } catch (err) {
    return [`gateway: ${bridgeFailureText(err)}`];
  }
}

/** Bare /models|/model|/models list → interactive picker (Companion parity). */
export function parseModelsPickerIntent(line: string): boolean {
  const parsed = parseSlashLine(line);
  if (!parsed) return false;
  if (parsed.name !== "models" && parsed.name !== "model") return false;
  const tokens = tokenizeSlashArgs(parsed.args);
  const verb = tokens[0]?.toLowerCase();
  if (verb === "default") return false;
  if (!verb || verb === "list" || verb === "ls") return true;
  return false;
}

function inventoryModelOptions(): Array<{ name: string; label?: string }> {
  return workspace.models.map((m) => ({ name: m.name }));
}

/** Prefer bridge list_models; merge inventory names if the RPC list is thinner. */
function mergeModelOptions(bridgeModels: ModelOption[]): ModelOption[] {
  const byName = new Map<string, ModelOption>();
  for (const m of bridgeModels) {
    if (m.name) byName.set(m.name, m);
  }
  for (const m of inventoryModelOptions()) {
    if (m.name && !byName.has(m.name)) byName.set(m.name, m);
  }
  return [...byName.values()];
}

/** P10-C3 chips for the /models picker; absent = no chips. G8: the catalog
 * is an authenticated fact, so the console asks nothing itself — the rows
 * come from the r4 CLI (later wave); never blocks the picker. */
async function fetchVisibilityChips(): Promise<Map<string, string>> {
  const { authenticatedCatalogRows, visibilityByName } = await import("../lib/models-catalog");
  return visibilityByName(await authenticatedCatalogRows());
}

interface ProvisionedModels {
  models: ModelOption[];
  current: string;
  /** Registered sources whose /models probe failed, named — an empty picker
   * must say "rikyu: API key rejected …", not bare "no provisioned models"
   * (2026-10-20 field report). */
  problems: string[];
}

async function fetchProvisionedModels(): Promise<ProvisionedModels> {
  if (isOfflineBackend()) {
    return {
      models: workspace.models.map((m) => ({ name: m.name, label: `[offline] ${m.name}` })),
      current: activeTab().currentModel ?? "",
      problems: [],
    };
  }
  try {
    const bridge = getCompanionBridge();
    const result = (await bridge.request("list_models", {}, 30_000)) as {
      models?: ModelOption[];
      current?: string;
      problems?: Array<{ source?: string; error?: string }>;
    };
    const models = mergeModelOptions(result.models ?? []);
    const problems = (result.problems ?? [])
      .map((p) => [p.source, p.error].filter(Boolean).join(": "))
      .filter((text) => text.length > 0);
    return {
      models,
      current: result.current ?? activeTab().currentModel ?? "",
      problems: models.length === 0 && problems.length === 0 ? await gatewayListProblem() : problems,
    };
  } catch (err) {
    return {
      models: inventoryModelOptions(),
      current: activeTab().currentModel ?? "",
      problems: [bridgeFailureText(err)],
    };
  }
}

let lastModelsProblems: string[] = [];

/** Probe failures of the latest /models picker fetch (empty picker notices). */
export function modelsProbeProblems(): string[] {
  return lastModelsProblems;
}

type PickState = "ready" | "provisioning" | "other";

interface ModelPickEntry {
  name: string;
  label?: string;
  sourceName?: string;
  state: PickState;
}

type ModelOption = { name: string; label?: string; status?: string };

/**
 * A CLI endpoint row that names a model this gateway serves. Not the
 * configured public endpoint (`instance_id: "public"`): it carries no
 * model (the mapper's "unknown") or the profile's built-in default
 * (`rivault-gpt-oss-static`), which nobody serves — both became a pickable
 * "◐ provisioning" row under "unknown", highlighted first, and chat then
 * died "not provisioned → /endpoint up" (stabilization 2026-10-21,
 * connect#2, slash-a#1, resize-render#8, chat#6). A public source's models
 * come from list_models as routable `model@source` rows instead.
 */
function pickableEndpoint(ep: UiEndpoint): boolean {
  return ep.id !== "public" && Boolean(ep.model) && ep.model !== "unknown";
}

function endpointPickState(status: UiEndpoint["status"]): PickState {
  if (status === "ready") return "ready";
  return status === "provisioning" ? "provisioning" : "other";
}

/** A list_models row's state: a probed source answers "available". */
function optionPickState(status: string | undefined): PickState {
  return status && ["available", "ready", "running"].includes(status) ? "ready" : "other";
}

function addPick(bySource: Map<string, ModelPickEntry[]>, entry: ModelPickEntry & { sourceName: string }): void {
  const bucket = bySource.get(entry.sourceName) ?? [];
  if (!bucket.some((e) => e.name === entry.name)) bucket.push(entry);
  bySource.set(entry.sourceName, bucket);
}

function endpointPicks(): Map<string, ModelPickEntry[]> {
  const bySource = new Map<string, ModelPickEntry[]>();
  for (const ep of workspace.endpoints.filter(pickableEndpoint)) {
    addPick(bySource, { name: ep.model, sourceName: ep.sourceName ?? "unknown", state: endpointPickState(ep.status) });
  }
  return bySource;
}

function pickedAlready(bySource: Map<string, ModelPickEntry[]>, name: string): boolean {
  const base = name.split("@")[0];
  return [...bySource.values()].some((bucket) => bucket.some((e) => e.name === name || e.name === base));
}

/** Group pick options by source (from the live endpoint feed); each item
 * carries the model name for set_model. */
function modelsPickerFromOptions(
  models: ModelOption[],
  current: string,
  visibilityChips?: ReadonlyMap<string, string>,
): SlashPickerRequest | null {
  const bySource = endpointPicks();
  // Bare inventory rows (`r4 model list` is unqualified, e.g. `kimi-k3`)
  // duplicate qualified bridge rows (`kimi-k3@rikyu`) with an IDENTICAL
  // label (labels strip the source suffix) — picking the bare twin sends a
  // source-less name that routes to the platform gateway and dies with
  // "Model '<name>' is not provisioned" (user report 2026-09-29). Skip a
  // bare row when a qualified twin exists; keep it only when no unroutable
  // duplicate does.
  const qualifiedBares = new Set(models.filter((m) => m.name.includes("@")).map((m) => m.name.split("@")[0]));
  // Any /models row not present in endpoints: model names labeled
  // `model@source` (e.g. qwen3.6-35b@openrouter) group under the suffix
  // source — consistent with upstream label conventions. Bare names land in
  // the `other` bucket (unknown source).
  for (const model of models) {
    if (!model.name.includes("@") && qualifiedBares.has(model.name)) continue;
    if (pickedAlready(bySource, model.name)) continue;
    const suffix = model.name.split("@").slice(1).join("@").trim();
    addPick(bySource, { name: model.name, sourceName: suffix || "other", state: optionPickState(model.status) });
  }
  const items = currentFirst([...bySource.entries()], current).flatMap(([src, entries]) =>
    entries.map((entry) => modelPickItem(src, entry, current, visibilityChips)),
  );
  if (items.length === 0) return null;
  return {
    title: "Choose a model (grouped per source · ● ready ◐ provisioning ○ other)",
    commandPrefix: "/models ",
    mode: "set_model",
    items,
  };
}

const PICK_MARKER: Record<PickState, string> = { ready: "●", provisioning: "◐", other: "○" };

function modelPickItem(
  src: string,
  entry: ModelPickEntry,
  current: string,
  visibilityChips?: ReadonlyMap<string, string>,
): SlashPickerItem {
  const chipTag = visibilityChips?.get(entry.name) === "grant" ? "[shared] " : "";
  const suffix = entry.name === current ? " ← current" : "";
  return {
    key: entry.name,
    modelName: entry.name,
    group: src,
    label: `${PICK_MARKER[entry.state]} ${chipTag}${entry.name.split("@")[0] ?? entry.name}${suffix}`,
  };
}

/** The current model's group first, the current model first in it: the
 * picker opens on row 0, which must be the model in use, not whatever
 * sorted first (connect#2). */
function currentFirst(
  groups: Array<[string, ModelPickEntry[]]>,
  current: string,
): Array<[string, ModelPickEntry[]]> {
  const at = groups.findIndex(([, entries]) => entries.some((e) => e.name === current));
  if (!current || at < 0) return groups;
  const [src, entries] = groups[at]!;
  const own: [string, ModelPickEntry[]] = [src, [...entries].sort((x, y) => Number(y.name === current) - Number(x.name === current))];
  return [own, ...groups.slice(0, at), ...groups.slice(at + 1)];
}

/** Bare `/theme` opens the theme picker (args route to the handler instead). */
export function parseThemePickerIntent(line: string): boolean {
  const parsed = parseSlashLine(line);
  return Boolean(parsed && parsed.name === "theme" && parsed.args.trim() === "");
}

/** The theme active before a picker preview, while that preview (applied
 * un-persisted) is still the live theme. */
let themePreview: { origin: string; previewed: string } | null = null;

/**
 * The theme `/theme <name>` replaces, as the user knows it: when the theme
 * being replaced is only a picker preview, the one active before the picker
 * opened. The picker's Enter dispatches `/theme <name>` after the preview
 * already applied <name>, so the receipt read "(already active)" for a
 * theme that had just changed and persisted (stabilization 2026-10-21,
 * slash-a#6 / panes-overlays#10). Consumed once.
 */
export function takeThemePreviewOrigin(replaced: string): string {
  const preview = themePreview;
  themePreview = null;
  return preview && preview.previewed === replaced ? preview.origin : replaced;
}

/**
 * Theme picker — local, no bridge fetch; Enter dispatches `/theme <name>`.
 * Live preview: moving the highlight applies the theme un-persisted; Esc
 * restores the theme that was active when the picker opened.
 */
export function themePicker(): SlashPickerRequest {
  const previous = currentThemeName();
  const request: SlashPickerRequest = {
    title: "Choose a color theme (↑↓ previews · Enter keeps · Esc restores)",
    commandPrefix: "/theme ",
    onHighlight: (item) => {
      // Preview: rewrite palette + bump the epoch. The overlay sits INSIDE the
      // epoch-keyed remount (painted after the Shell — no background overwrite),
      // and its selection survives in the store; no manual re-render poke needed.
      applyTheme(item.key, { persist: false });
      themePreview = { origin: previous, previewed: currentThemeName() };
    },
    onCancel: () => {
      themePreview = null;
      if (currentThemeName() !== previous) applyTheme(previous, { persist: false });
    },
    items: listThemes().map((theme) => ({
      key: theme.name,
      label: `◆ ${theme.name} — ${theme.description}${theme.name === previous ? " ← current" : ""}`,
    })),
  };
  return request;
}

/** Build a picker request when a slash command needs interactive selection. */
export async function resolveSlashPicker(line: string): Promise<SlashPickerRequest | null> {
  if (parseThemePickerIntent(line)) {
    return themePicker();
  }

  if (parseModelsPickerIntent(line)) {
    const { models, current, problems } = await fetchProvisionedModels();
    const chips = await fetchVisibilityChips();
    lastModelsProblems = problems;
    const request = modelsPickerFromOptions(models, current, chips);
    if (request && problems.length > 0) {
      actions.sendSystemNotice(`some sources failed their probe — ${problems.join("; ")}`);
    }
    return request;
  }

  endpointPickerFailureText = "";
  const upIntent = parseUpPickerIntent(line);
  if (upIntent) {
    const models = await fetchRegistryModels();
    return upPickerFromModels(models, upIntent.requestNew);
  }

  const downIntent = parseDownPickerIntent(line);
  if (downIntent) {
    const stoppable = await fetchStoppableEndpoints();
    if (stoppable.length === 0) return null;
    const prefix = downIntent.force ? "/endpoint down --force " : "/endpoint down ";
    return {
      title: "Stop endpoint — pick one",
      commandPrefix: prefix,
      items: endpointDownItems(stoppable),
    };
  }

  return null;
}
