/**
 * /catalog pane — ADR-031 three-plane surface.
 *
 * Top: the science catalog (server catalogue assets with consistency badges
 * and read drill) — this is the route's primary surface. Bottom: the
 * workspace-assets section (skill/MCP/session/code-change kinds from local
 * stores) kept where it always was, relabeled so "catalog" no longer means
 * two different things on one pane.
 *
 * Consistency glyph vocabulary (server-derived, never client-computed):
 *   ✓ COMPLETE · ◐ EMBEDDING_PENDING (non-error) · ▲ PARTIAL · ✗ ORPHANED
 *   ·  no canonical mint · … minted but unsampled
 */

import { For, Show, onMount } from "solid-js";
import { workspace } from "../stores";
import { scienceActions, scienceStore } from "../stores/science";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { catalogRows, catalogKindLabel, PENDING_KINDS, type CatalogKind, type CatalogRow } from "../lib/catalog-rows";
import { toggleSkill } from "../bridge/catalog-bridge";
import { ScienceCatalogPanel } from "../components/science-catalog-panel";

const KIND_ORDER: CatalogKind[] = ["skill", "mcp_server", "code_change", "session"];

function groupRows(rows: CatalogRow[]): Array<{ kind: CatalogKind; rows: CatalogRow[] }> {
  return KIND_ORDER.map((kind) => ({
    kind,
    rows: rows.filter((r) => r.kind === kind),
  })).filter((g) => g.rows.length > 0);
}

export function CatalogRoute() {
  onMount(() => {
    const platform = workspace.platform;
    if (scienceStore.catalog.length === 0 && !scienceStore.loading && platform?.catalogSearch) {
      void scienceActions.browseCatalog();
    }
    void scienceActions.refreshReconcile();
    void scienceActions.refreshStats();
    // D-39: Scientific Catalogs ride their own family (never the data catalog).
    if (workspace.platform?.scientificCatalogs) {
      void scienceActions.loadScientificCatalogs();
    }
  });

  const rows = () =>
    catalogRows({
      skills: workspace.skills,
      mcpServers: workspace.mcpServers,
      changeSets: workspace.changeSets,
      sessions: workspace.sessionsList,
    });

  return (
    <box flexDirection="column" overflow="hidden" flexGrow={1} padding={1}>
      <ScienceCatalogPanel />
      <Show when={scienceStore.scientificCatalogs.length > 0}>
        <box height={1} width="100%" overflow="hidden" marginTop={1}>
          <text fg={palette.fgDim}>Scientific Catalogs (D-39 — named, versioned knowledge objects)</text>
        </box>
        <For each={scienceStore.scientificCatalogs.slice(0, 6)}>
          {(row) => (
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.fg}>
                {truncate(
                  `  ${row.catalog_id} · ${row.name} v${row.version} · graphs=${row.graphs} skills=${row.skills} mcp=${row.mcps} · ${row.licence || "—"}${row.change_note ? ` · ${row.change_note}` : ""}`,
                  100,
                )}
              </text>
            </box>
          )}
        </For>
      </Show>
      <box height={1} width="100%" overflow="hidden" marginTop={1}>
        <text fg={palette.fgDim}>workspace assets (local stores — skills / MCP / sessions / code)</text>
      </box>
      <Show when={rows().length > 0}>
        <For each={groupRows(rows())}>
          {(group) => (
            <box flexDirection="column" marginBottom={0} overflow="hidden">
              <For each={group.rows.slice(0, 3)}>
                {(row) => (
                  <box height={1} width="100%" overflow="hidden" onMouseDown={() => toggleSkill(row)}>
                    <text fg={palette.fg}>
                      {truncate(`  ${catalogKindLabel(group.kind)} · ${row.verb === "toggle" ? "◐ " : "  "}${row.id}`, 64)}
                    </text>
                  </box>
                )}
              </For>
            </box>
          )}
        </For>
      </Show>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>{`absent kinds (server catalog/snapshot): ${PENDING_KINDS.join(" · ")}`}</text>
      </box>
    </box>
  );
}
