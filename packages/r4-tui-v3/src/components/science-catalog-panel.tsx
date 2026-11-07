/**
 * Science Catalog Panel — the ADR-031 catalogue plane in the TUI.
 *
 * Rows are server catalogue assets with cross-plane badges:
 *   ✓ COMPLETE · ◐ EMBEDDING_PENDING · ▲ PARTIAL · ✗ ORPHANED · … unsampled
 *   · (dot) no canonical document minted — NOT an error.
 *
 * The consistency badge is the plane reconciler's projection only
 * (Invariant 4). Selection opens the read drill: links block (document →
 * chunk nodes → vector rows) + a byte-bounded text preview. Search goes to
 * `/umrs/catalog/search`; nothing is fetched client-side beyond the store.
 */

import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { scienceActions, scienceStore } from "../stores/science";
import { workspace } from "../stores";
import { claimKeys } from "../keymap/key-claims";
import { consistencyBadge, reconcileCountsLine } from "../lib/consistency";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Preview row cap tracks the real pane width — was fixed 78. */
const catCap = () => panePanelWidth(1, 6);
import { KeyTrap, keyTrapInsertChar } from "./key-trap";
import { clampIndex, handleListKey } from "../lib/selectable-list";
import type { CatalogAsset } from "../types/science";
import type { KeyEvent } from "@opentui/core";
import { Panel } from "../ui/Panel";

const SEARCH_COLS = 40;
const PREVIEW_LINES = 4;

/** `/` opens the search row from the list (the row advertises it). */
function opensSearch(event: KeyEvent): boolean {
  return !event.ctrl && !event.meta && (event.name === "slash" || event.sequence === "/");
}

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

function badgeColor(state: string): string {
  if (state === "COMPLETE") return palette.statusOk;
  if (state === "EMBEDDING_PENDING") return palette.statusWarn;
  if (state === "PARTIAL" || state === "ORPHANED") return palette.statusErr;
  return palette.fgDim;
}

/** One catalogue row: badge + key + state/modality + links presence. */
function AssetRow(props: {
  asset: CatalogAsset;
  isSelected: () => boolean;
  onSelect: () => void;
}) {
  const badge = createMemo(() => consistencyBadge(props.asset, scienceStore.reconcile));
  const linksText = () =>
    props.asset.links
      ? `doc ${truncate(props.asset.links.documentId, 24)} · chunks ${props.asset.links.chunkNodes} · vec ${props.asset.links.vectorRows.length}`
      : "no canonical document";
  return (
    <box
      flexDirection="row"
      height={1}
      width="100%"
      overflow="hidden"
      backgroundColor={props.isSelected() ? palette.bgActive : undefined}
      onMouseDown={props.onSelect}
    >
      <text fg={props.isSelected() ? palette.fgBright : palette.fg} onMouseDown={props.onSelect}>
        {truncate(
          `${props.isSelected() ? "▸" : " "} ${badge().glyph} ${props.asset.s3Key}  ${linksText()}`,
          72,
        )}
      </text>
      <text fg={badgeColor(badge().state)} flexShrink={0} onMouseDown={props.onSelect}>
        {` ${badge().state}`}
      </text>
    </box>
  );
}

/** Expanded read drill for the selected asset (§4/§5 of the workstream). */
function AssetReadPanel() {
  const read = createMemo(() => scienceStore.assetRead);
  return (
    <Show when={read()}>
      <Panel flexDirection="column" accent={palette.accent2} tone="raised" padding={1} overflow="hidden">
        <Line fg={palette.accent2}>
          {truncate(
            `${read()!.s3Key}  bytes ${read()!.offset}+${read()!.length}/${read()!.totalSize}` +
              `${read()!.truncated ? " [truncated]" : ""}  source=${read()!.readSource}`,
            78,
          )}
        </Line>
        <For each={read()!.text.split("\n").slice(0, PREVIEW_LINES)}>
          {(line) => <Line fg={palette.fgDim}>{truncate(line || " ", catCap())}</Line>}
        </For>
        <Show when={(read()!.text.split("\n").length ?? 0) > PREVIEW_LINES}>
          <Line fg={palette.fgDim}>… (preview — `r4 data catalog read` for full bytes)</Line>
        </Show>
        <Line fg={palette.fgDim}>
          {read()!.links
            ? truncate(
                `links: document=${read()!.links!.documentId} via ${read()!.links!.linkMethod} · ` +
                  `chunks ${read()!.links!.chunkNodes} · vectors ${read()!.links!.vectorRows.length}`,
                78,
              )
            : "links: none (no canonical document minted)"}
        </Line>
        <LineagePanel />
      </Panel>
    </Show>
  );
}

/** Provenance chain (workstream §7): ancestors → artifact → descendants. */
function LineagePanel() {
  const lineage = createMemo(() => scienceStore.lineage);
  return (
    <Show when={lineage()}>
      <Line fg={palette.accent}>provenance</Line>
      <Show
        when={lineage()!.ancestors.length + lineage()!.descendants.length > 0}
        fallback={<Line fg={palette.fgDim}>  (no recorded lineage for this document)</Line>}
      >
        <For each={lineage()!.ancestors}>
          {(node) => (
            <Line fg={palette.fgDim}>
              {truncate(`  ↑ ${node.title} (${node.artifactType} · ${node.edgeKind})`, 78)}
            </Line>
          )}
        </For>
        <Line fg={palette.fg}>{`  ● ${truncate(lineage()!.artifactId, 60)}`}</Line>
        <For each={lineage()!.descendants}>
          {(node) => (
            <Line fg={palette.fgDim}>
              {truncate(`  ↓ ${node.title} (${node.artifactType} · ${node.edgeKind})`, 78)}
            </Line>
          )}
        </For>
      </Show>
    </Show>
  );
}

export function ScienceCatalogPanel() {
  const [cursor, setCursor] = createSignal(0);
  const [searchDraft, setSearchDraft] = createSignal("");
  const [searchFocused, setSearchFocused] = createSignal(false);

  const assets = createMemo(() => scienceStore.catalog);

  // Esc first leaves the search, then clears the selected asset; only a
  // bare Esc closes the pane. The keymap's sweep cannot see this local
  // state, so the panel claims Esc while it has some (TUI audit r2 #8).
  onCleanup(
    claimKeys("science-catalog", ["escape"], () => workspace.paneFocus && (searchFocused() || scienceStore.selectedAsset != null)),
  );

  createEffect(() => {
    setCursor((c) => clampIndex(c, assets().length));
  });

  const selectAsset = (index: number) => {
    const i = clampIndex(index, assets().length);
    setCursor(i);
    const asset = assets()[i];
    if (asset) void scienceActions.readAsset(asset);
  };

  const handleListKeyDown = (event: KeyEvent) => {
    if (opensSearch(event)) {
      event.preventDefault();
      setSearchFocused(true);
      return;
    }
    if (event.name === "escape") {
      if (scienceStore.selectedAsset == null) return;
      event.preventDefault();
      scienceActions.selectAsset(null);
      return;
    }
    handleListKey(event.name, {
      selected: cursor(),
      length: assets().length,
      ctrl: Boolean(event.ctrl),
      onMove: (i) => {
        event.preventDefault();
        setCursor(i);
      },
      onEnter: (i) => {
        event.preventDefault();
        selectAsset(i);
      },
    });
  };

  const handleSearchKey = (event: KeyEvent) => {
    if (event.name === "escape") {
      event.preventDefault();
      setSearchFocused(false);
      setSearchDraft("");
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      setSearchFocused(false);
      void scienceActions.searchCatalog(searchDraft().trim());
      return;
    }
    if (event.name === "backspace" || event.name === "delete") {
      event.preventDefault();
      setSearchDraft((d) => d.slice(0, -1));
      return;
    }
    if (!event.ctrl && !event.meta) {
      const ch = keyTrapInsertChar(event);
      if (!ch) return;
      event.preventDefault();
      setSearchDraft((d) => d + ch);
    }
  };

  const countsLine = createMemo(() => reconcileCountsLine(scienceStore.reconcile));

  /** §8 health strip — server numbers only; null plane counts stay "n/a". */
  const statsLine = createMemo(() => {
    const s = scienceStore.stats;
    if (!s) return null;
    const states = Object.entries(s.states)
      .map(([k, v]) => `${k} ${v}`)
      .join(" · ");
    const planes = [
      s.canonicalDocuments !== null ? `canonical docs ${s.canonicalDocuments}` : null,
      s.chunkNodes !== null ? `chunk nodes ${s.chunkNodes}` : null,
      s.vectorRows !== null ? `vector rows ${s.vectorRows}` : null,
    ]
      .filter((p) => p !== null)
      .join(" · ");
    return `${s.objectCount} objects (${states || "no state rows"})${planes ? `  ·  ${planes}` : ""}`;
  });

  return (
    <box flexDirection="column" overflow="hidden" flexGrow={1}>
      <box flexDirection="row" height={1} width="100%" overflow="hidden">
        <text fg={palette.accent} flexShrink={0}>science catalog</text>
        <text fg={palette.fgDim}>
          {`  ${assets().length} assets${scienceStore.catalogQuery ? ` · q=${scienceStore.catalogQuery}` : ""}`}
        </text>
      </box>
      <Show when={statsLine()}>
        <Line fg={palette.fgDim}>{`inventory: ${statsLine()}`}</Line>
      </Show>
      <box height={1} width="100%" overflow="hidden" onMouseDown={() => setSearchFocused(true)}>
        <text
          fg={searchFocused() ? palette.fgBright : palette.fgDim}
          onMouseDown={() => setSearchFocused(true)}
        >
          {searchFocused() ? `› ${truncate(searchDraft(), SEARCH_COLS)}█` : "/ search catalogue…"}
        </text>
        <Show when={searchFocused()}>
          <KeyTrap paneScoped focused captureText onKeyDown={handleSearchKey} />
        </Show>
      </box>
      <Show when={countsLine()}>
        <Line fg={palette.fgDim}>{`consistency: ${countsLine()}`}</Line>
      </Show>
      <Show when={scienceStore.error}>
        <Line fg={palette.statusErr}>{scienceStore.error!}</Line>
      </Show>
      <Show
        when={assets().length > 0}
        fallback={
          <Line fg={palette.fgDim}>
            (no catalogue rows — search above, or ingest assets server-side first)
          </Line>
        }
      >
        <scrollbox height={8} stickyScroll stickyStart="top" scrollY overflow="hidden" width="100%">
          <For each={assets()}>
            {(asset, i) => (
              <AssetRow asset={asset} isSelected={() => i() === cursor()} onSelect={() => selectAsset(i())} />
            )}
          </For>
        </scrollbox>
      </Show>
      {/* Mounted with zero rows too: a focused empty pane otherwise has no
          key owner and every key (including `/`) is dropped. */}
      <box height={1} width={1} overflow="hidden">
        <KeyTrap paneScoped focused={!searchFocused()} onKeyDown={handleListKeyDown} />
      </box>
      <AssetReadPanel />
    </box>
  );
}
