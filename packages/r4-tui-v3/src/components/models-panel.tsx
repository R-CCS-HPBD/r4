/**
 * Grouped, scrollable Models panel: rows grouped per source with collapsible
 * headers + availability markers; full detail lines preserved; public-source
 * groups separated from platform groups.
 */

import { createMemo, createSignal } from "solid-js";
import { workspace } from "../stores";
import { palette } from "../themes";
import type { UiEndpoint, UiModel } from "../types";
import { PanelList, type PanelListScroll, type PanelSection } from "./panel-list";
import { publicSourceName } from "../lib/endpoint-label";

type EntryState = "ready" | "provisioning" | "other";

interface SourceSectionModels {
  name: string;
  isPublic: boolean;
  entries: Array<{ key: string; model: string; state: EntryState }>;
}

function marker(state: EntryState): { glyph: string; fg: string } {
  if (state === "ready") return { glyph: "●", fg: palette.statusOk };
  if (state === "provisioning") return { glyph: "◐", fg: palette.statusWarn };
  return { glyph: "○", fg: palette.fgDim };
}

function endpointState(status: UiEndpoint["status"]): EntryState {
  if (status === "ready") return "ready";
  return status === "provisioning" ? "provisioning" : "other";
}

function sectionFor(map: Map<string, SourceSectionModels>, name: string, isPublic: boolean): SourceSectionModels {
  const section = map.get(name) ?? { name, isPublic, entries: [] };
  section.isPublic ||= isPublic;
  map.set(name, section);
  return section;
}

/** One row per model name in a source (multi-job flat rows elsewhere). */
function addEntry(section: SourceSectionModels, model: string, state: EntryState): void {
  if (section.entries.some((e) => e.model === model)) return;
  section.entries.push({ key: `${section.name}:${model}`, model, state });
}

/** A configured public source's row (`instance_id: "public"`) opens that
 * source's section, never an "unknown" entry (wave 82). With an active
 * source (single-source shape) the row names that source's own default
 * model (`source_model`), routable, so listed as available. A legacy row
 * with no `_source` names `cfg.model`, possibly a profile default nobody
 * serves (see slash-pickers `pickableEndpoint`), so it is not listed. */
function addPublicRow(map: Map<string, SourceSectionModels>, ep: UiEndpoint): void {
  const section = sectionFor(map, publicSourceName(ep), true);
  if (ep.sourceName && ep.model && ep.model !== "unknown") addEntry(section, ep.model, "ready");
}

/** Platform sources: live state from the endpoint feed. */
function addEndpointRows(map: Map<string, SourceSectionModels>, endpoints: readonly UiEndpoint[]): void {
  for (const ep of endpoints) {
    if (ep.isPublic) addPublicRow(map, ep);
    else addEntry(sectionFor(map, ep.sourceName ?? "unknown", false), ep.model, endpointState(ep.status));
  }
}

/** The public section a kind-less row belongs to. With one source
 * registered, `r4 model list --json` emits `{name, source: <provider
 * label>}` with no `kind` (`_emit_model_list_json`); the label ("public",
 * "openai", …) is not the source name, so the row goes to the section of
 * that name or else to the ONE public section. Several public sections
 * only occur in the aggregated listing, whose rows always carry `kind`. */
function kindlessPublicSection(map: Map<string, SourceSectionModels>, source: string): SourceSectionModels | undefined {
  const named = map.get(source);
  if (named?.isPublic) return named;
  const publics = [...map.values()].filter((s) => s.isPublic);
  return publics.length === 1 ? publics[0] : undefined;
}

/** Where a `workspace.models` row is listed: a `kind: "public"` row under
 * its own source; a kind-less non-platform row per `kindlessPublicSection`;
 * platform rows nowhere (endpoint feed, b4646d8d). */
function publicModelSection(map: Map<string, SourceSectionModels>, m: UiModel): SourceSectionModels | undefined {
  if (m.kind === "public") return m.source ? sectionFor(map, m.source, true) : undefined;
  if (m.kind || !m.source || m.source === "platform") return undefined;
  return kindlessPublicSection(map, m.source);
}

/** Public sources: the models `r4 model list` names for THAT source,
 * routable as model@source, so available. Filtered by source — the
 * backend-wide list put every model under every source (b4646d8d). */
function addPublicModels(map: Map<string, SourceSectionModels>, models: readonly UiModel[]): void {
  for (const m of models) {
    const section = publicModelSection(map, m);
    if (section) addEntry(section, m.name, "ready");
  }
}

/** Per-source sections, platform first, then public. */
export function modelSections(
  endpoints: readonly UiEndpoint[],
  models: readonly UiModel[],
): SourceSectionModels[] {
  const map = new Map<string, SourceSectionModels>();
  addEndpointRows(map, endpoints);
  addPublicModels(map, models);
  const all = [...map.values()];
  return [...all.filter((s) => !s.isPublic), ...all.filter((s) => s.isPublic)];
}

function emptyItem(section: SourceSectionModels) {
  const label = section.isPublic ? "(no models listed)" : "(no live endpoints reported)";
  return { key: `${section.name}:_empty`, label, fg: palette.fgDim };
}

function sectionItems(section: SourceSectionModels) {
  if (section.entries.length === 0) return [emptyItem(section)];
  return section.entries.map((row) => {
    const m = marker(row.state);
    return { key: row.key, label: `${m.glyph} ${row.model}`, fg: m.fg };
  });
}

function sectionTitle(section: SourceSectionModels): string {
  const kind = section.isPublic ? " · public" : "";
  return `${section.name}${kind} (${section.entries.length})`;
}

/** `keyboard={false}`: the host pane's trap owns the keys and scrolls the
 * list through `onScrollApi` (Infrastructure). */
export function ModelsPanel(props: { keyboard?: boolean; onScrollApi?: (api: PanelListScroll) => void } = {}) {
  const [collapsed, setCollapsed] = createSignal<Record<string, boolean>>({});
  const toggle = (name: string) =>
    setCollapsed((state) => ({ ...state, [name]: !state[name] }));

  const sections = createMemo<PanelSection[]>(() =>
    modelSections(workspace.endpoints, workspace.models).map((section) => ({
      title: sectionTitle(section),
      collapsed: Boolean(collapsed()[section.name]),
      onToggle: () => toggle(section.name),
      items: sectionItems(section),
    })),
  );

  return sections().length > 0 ? (
    <PanelList sections={sections()} maxRows={14} keyboard={props.keyboard} onScrollApi={props.onScrollApi} />
  ) : (
    <box height={1}>
      <text fg={palette.fgDim}>No endpoint data yet — /endpoint up first.</text>
    </box>
  );
}
