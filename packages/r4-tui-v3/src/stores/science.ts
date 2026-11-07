/**
 * Science store — ADR-031 three-plane knowledge state for the console.
 *
 * Every value here comes from a server-side read mapped in
 * `adapters/science-mappers.ts`. Nothing is computed locally: consistency
 * states are the plane reconciler's projections (Invariant 4), catalogue
 * rows are the physical-existence plane, graph content is the canonical
 * spine. Absence stays absence (`null` = "server said nothing"), never a
 * fabricated empty state.
 */

import { createStore } from "solid-js/store";
import { workspace, activeTab } from "./index";
import {
  mapCatalogRead,
  mapCatalogSearch,
  mapCatalogStats,
  mapGraphContent,
  mapGraphList,
  mapGraphProposal,
  mapLineageReport,
  mapReconcileReport,
} from "../adapters/science-mappers";
import type {
  CatalogAsset,
  CatalogReadView,
  CatalogStats,
  GraphContent,
  GraphProposal,
  GraphSummary,
  LineageReport,
  ReconcileReport,
} from "../types/science";

export interface ScienceStore {
  // Catalogue plane (§1/§4 of the knowledge-catalogue UX workstream)
  catalog: CatalogAsset[];
  catalogQuery: string;
  selectedAsset: CatalogAsset | null;
  assetRead: CatalogReadView | null;
  stats: CatalogStats | null;
  // Canonical spine (§3)
  graphs: GraphSummary[];
  selectedGraphId: string | null;
  graphContent: GraphContent | null;
  proposals: GraphProposal[];
  /** The proposal the a / r keys decide (K1.5); null = the first one. */
  selectedProposalId: string | null;
  // Consistency (§1 invariant 4 surface)
  reconcile: ReconcileReport | null;
  // Knowledge-backed search (§6)
  knowledgeQueryResults: Array<Record<string, unknown>>;
  knowledgeQueryMeta: { wired: string[]; unavailable: string[] } | null;
  // Provenance (§7)
  lineage: LineageReport | null;
  // D-39 Scientific Catalogs (named, versioned objects — NOT the data catalog)
  scientificCatalogs: ScientificCatalogRow[];
  // Lifecycle
  loading: boolean;
  error: string | null;
}

export interface ScientificCatalogRow {
  catalog_id: string;
  project_id: string;
  name: string;
  version: string;
  licence: string;
  change_note: string;
  graphs: number;
  skills: number;
  mcps: number;
  status: string;
}

/** Fresh state per call: createStore adopts (and then mutates) the object it
 * is given, so a shared initial object would make reset() a no-op. */
const initialState = (): ScienceStore => ({
  catalog: [],
  catalogQuery: "",
  selectedAsset: null,
  assetRead: null,
  stats: null,
  graphs: [],
  selectedGraphId: null,
  graphContent: null,
  proposals: [],
  selectedProposalId: null,
  reconcile: null,
  knowledgeQueryResults: [],
  knowledgeQueryMeta: null,
  lineage: null,
  scientificCatalogs: [],
  loading: false,
  error: null,
});

const [scienceStore, setScienceStore] = createStore<ScienceStore>(initialState());
export { scienceStore };

/** The proposal the a / r keys act on: the selected one, else the first
 * (K1.5, knowledge plan 2026-10-24: the keys always took `proposals[0]`). */
export function selectedProposal(): GraphProposal | undefined {
  const rows = scienceStore.proposals;
  return rows.find((row) => row.proposalId === scienceStore.selectedProposalId) ?? rows[0];
}

function platform() {
  return workspace.platform;
}

async function guard<T>(fn: () => Promise<T>): Promise<T | null> {
  setScienceStore("loading", true);
  setScienceStore("error", null);
  try {
    return await fn();
  } catch (err) {
    setScienceStore("error", err instanceof Error ? err.message : String(err));
    return null;
  } finally {
    setScienceStore("loading", false);
  }
}

function payloadError(payload: Record<string, unknown>): string | null {
  return typeof payload["error"] === "string" && payload["error"] ? (payload["error"] as string) : null;
}

function currentProjectId(): string | null {
  return activeTab().project?.id ?? null;
}

async function _loadScatRows(p: NonNullable<ReturnType<typeof platform>>): Promise<void> {
  const payload = await p.scientificCatalogs!(currentProjectId() ?? undefined);
  const err = payloadError(payload);
  if (err) {
    setScienceStore("error", err);
    return;
  }
  setScienceStore("scientificCatalogs", mapScientificCatalogs(payload));
}

function _scatStr(r: Record<string, unknown>, key: string): string {
  const v = r[key];
  return typeof v === "string" ? v : "";
}

function _scatCount(r: Record<string, unknown>, key: string): number {
  const v = r[key];
  return Array.isArray(v) ? v.length : 0;
}

function _toScatRow(r: Record<string, unknown>): ScientificCatalogRow {
  return {
    catalog_id: _scatStr(r, "catalog_id"),
    project_id: _scatStr(r, "project_id"),
    name: _scatStr(r, "name"),
    version: _scatStr(r, "version"),
    licence: _scatStr(r, "licence"),
    change_note: _scatStr(r, "change_note"),
    graphs: _scatCount(r, "member_graph_ids"),
    skills: _scatCount(r, "skill_ids"),
    mcps: _scatCount(r, "mcp_server_ids"),
    status: _scatStr(r, "status") || "active",
  };
}

function mapScientificCatalogs(payload: Record<string, unknown>): ScientificCatalogRow[] {
  const rows = (payload.catalogs as Array<Record<string, unknown>> | undefined) ?? [];
  return rows.map(_toScatRow);
}

export const scienceActions = {
  /** D-39: list Scientific Catalogs (crew-cut, count-mapped by the store). */
  async loadScientificCatalogs(): Promise<void> {
    const p = platform();
    if (!p?.scientificCatalogs) return;
    await guard(() => _loadScatRows(p));
  },

  /** Catalogue plane: search assets with server-hydrated links (§1). */
  async searchCatalog(query: string): Promise<void> {
    const p = platform();
    if (!p?.catalogSearch) return;
    await guard(async () => {
      const payload = await p.catalogSearch!(query, { limit: 50 });
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      const mapped = mapCatalogSearch(payload);
      setScienceStore("catalog", mapped.results);
      setScienceStore("catalogQuery", mapped.query);
    });
  },

  /** Catalogue plane: browse-all = ready rows ∪ pending rows (server search
   *  requires q or a filter; the merge is the client.list() contract). */
  async browseCatalog(): Promise<void> {
    const p = platform();
    if (!p?.catalogSearch) return;
    await guard(async () => {
      const merged = new Map<string, CatalogAsset>();
      for (const state of ["ready", "pending"] as const) {
        const payload = await p.catalogSearch!("", { limit: 100, state });
        const err = payloadError(payload);
        if (err) {
          setScienceStore("error", err);
          return;
        }
        for (const row of mapCatalogSearch(payload).results) {
          if (!merged.has(row.s3Key)) merged.set(row.s3Key, row);
        }
      }
      setScienceStore("catalog", [...merged.values()]);
      setScienceStore("catalogQuery", "");
    });
  },

  /** Catalogue plane: select + byte-bounded read of one asset (§4/§5). */
  async readAsset(asset: CatalogAsset, source = "auto"): Promise<void> {
    const p = platform();
    if (!p?.catalogRead) return;
    setScienceStore("selectedAsset", asset);
    await guard(async () => {
      const payload = await p.catalogRead!(asset.s3Key, { source });
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      const view = mapCatalogRead(payload);
      setScienceStore("assetRead", view);
      // Provenance drill (§7): lineage chain for the linked canonical
      // document. Empty chain is honest ("no recorded provenance").
      const documentId = view.links?.documentId;
      if (documentId) {
        void scienceActions.loadLineage(documentId);
      } else {
        setScienceStore("lineage", null);
      }
    });
  },

  selectAsset(asset: CatalogAsset | null): void {
    setScienceStore("selectedAsset", asset);
    if (asset === null) setScienceStore("assetRead", null);
  },

  /** Catalogue stats incl. downstream plane counts (§8). */
  async refreshStats(): Promise<void> {
    const p = platform();
    if (!p?.catalogStats) return;
    await guard(async () => {
      const payload = await p.catalogStats!();
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      setScienceStore("stats", mapCatalogStats(payload));
    });
  },

  /** Canonical spine: registry list for the project (§3). */
  async refreshGraphs(): Promise<void> {
    const p = platform();
    if (!p?.listGraphs) return;
    const projectId = currentProjectId() ?? undefined;
    await guard(async () => {
      const payload = await p.listGraphs!(projectId, { limit: 200 });
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      setScienceStore("graphs", mapGraphList(payload));
    });
  },

  /** Canonical spine: bounded nodes+edges for one graph (§3). */
  async loadGraphContent(graphId: string): Promise<void> {
    const p = platform();
    if (!p?.graphContent) return;
    setScienceStore("selectedGraphId", graphId);
    await guard(async () => {
      const payload = await p.graphContent!(graphId, { nodeLimit: 200, edgeLimit: 400 });
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      setScienceStore("graphContent", mapGraphContent(payload));
    });
  },

  /** Agent graph-extension proposals awaiting a human decision. */
  async refreshProposals(graphId?: string): Promise<void> {
    const p = platform();
    if (!p?.graphProposals) return;
    const gid = graphId ?? scienceStore.selectedGraphId;
    if (!gid) return;
    await guard(async () => {
      const payload = await p.graphProposals!(gid, { status: "pending" });
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      const rows = (Array.isArray(payload["proposals"]) ? payload["proposals"] : []).map(
        mapGraphProposal,
      );
      setScienceStore("proposals", rows);
      // A decided (or vanished) proposal leaves the selection: fall back to
      // the first row instead of pointing at a row that is gone.
      if (!rows.some((row) => row.proposalId === scienceStore.selectedProposalId)) {
        setScienceStore("selectedProposalId", null);
      }
    });
  },

  /** Select one proposal for the a / r keys (mouse click on its row). */
  selectProposal(proposalId: string): void {
    if (scienceStore.proposals.some((row) => row.proposalId === proposalId)) {
      setScienceStore("selectedProposalId", proposalId);
    }
  },

  /** Move the proposal selection by `delta` rows (wraps); false = no rows. */
  cycleProposal(delta: number): boolean {
    const rows = scienceStore.proposals;
    if (rows.length === 0) return false;
    const at = rows.findIndex((row) => row.proposalId === selectedProposal()?.proposalId);
    const next = (((at + delta) % rows.length) + rows.length) % rows.length;
    setScienceStore("selectedProposalId", rows[next]!.proposalId);
    return true;
  },

  /** Human decision on one proposal; refresh the inbox afterwards. */
  async decideProposal(proposalId: string, approved: boolean): Promise<void> {
    const p = platform();
    if (!p?.decideGraphProposal) return;
    await guard(async () => {
      const payload = await p.decideGraphProposal!(proposalId, approved);
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      await scienceActions.refreshProposals();
    });
  },

  /** Reconciler projection — the only consistency-state source (§1, §8). */
  async refreshReconcile(sample = 100): Promise<void> {
    const p = platform();
    if (!p?.reconcileKnowledge) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    await guard(async () => {
      const payload = await p.reconcileKnowledge!(projectId, sample);
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      setScienceStore("reconcile", mapReconcileReport(payload));
    });
  },

  /** Knowledge facade query with wired/unavailable truth (§6). */
  async runKnowledgeQuery(query: string): Promise<void> {
    const p = platform();
    if (!p?.knowledgeQuery) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    await guard(async () => {
      const payload = await p.knowledgeQuery!(projectId, query, { limit: 30 });
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      const results = Array.isArray(payload["results"]) ? (payload["results"] as Array<Record<string, unknown>>) : [];
      const wired = Array.isArray(payload["wired_backends"]) ? payload["wired_backends"].map(String) : [];
      const unavailable = Array.isArray(payload["unavailable_backends"])
        ? payload["unavailable_backends"].map((b) => {
            if (typeof b === "string") return b;
            const rec = (b ?? {}) as Record<string, unknown>;
            return String(rec["backend"] ?? rec["name"] ?? "unknown");
          })
        : [];
      setScienceStore("knowledgeQueryResults", results);
      setScienceStore("knowledgeQueryMeta", { wired, unavailable });
    });
  },

  /** Provenance chain for one artifact (§7). */
  async loadLineage(artifactId: string): Promise<void> {
    const p = platform();
    if (!p?.knowledgeLineage) return;
    const projectId = currentProjectId();
    if (!projectId) return;
    await guard(async () => {
      const payload = await p.knowledgeLineage!(artifactId, projectId, 3);
      const err = payloadError(payload);
      if (err) {
        setScienceStore("error", err);
        return;
      }
      setScienceStore("lineage", mapLineageReport(payload));
    });
  },

  /** Drop every project-scoped read (graph, proposals, catalogue, stats,
   * lineage …) — the active project changed (TUI audit r2 #55). */
  reset(): void {
    setScienceStore(initialState());
  },
};
