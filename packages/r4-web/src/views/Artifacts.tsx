/**
 * Artifacts view — project artifacts listing with search/filter.
 *
 * Mirrors r4-tui-v3 knowledge objects (KnowledgeObject), evidence, and
 * publication patterns. Provides search, category filter, and artifact detail.
 */

import { useState, useMemo } from "react";
import { Shell } from "../components/Shell";

/* ── Types ─────────────────────────────────────────────────────────────── */

interface Artifact {
  id: string;
  name: string;
  kind: "file" | "metric" | "table" | "plot" | "document" | "model" | "index";
  size: string;
  modified: string;
  status: "ready" | "indexing" | "stale" | "error" | "empty";
  tags: string[];
  description: string;
}

/* ── Mock data ─────────────────────────────────────────────────────────── */

const mockArtifacts: Artifact[] = [
  { id: "a-1", name: "corpus-perovskite-v2.pdf", kind: "document", size: "4.2 MB", modified: "2h ago", status: "ready", tags: ["rag", "corpus"], description: "Main knowledge corpus for perovskite research" },
  { id: "a-2", name: "dft-results-candidates.csv", kind: "table", size: "1.8 MB", modified: "3h ago", status: "ready", tags: ["dft", "results"], description: "DFT calculation results for 147 candidates" },
  { id: "a-3", name: "bandgap-gnn-predictions.pkl", kind: "file", size: "8.4 MB", modified: "5h ago", status: "ready", tags: ["ml", "predictions"], description: "GNN bandgap predictions for 500 candidates" },
  { id: "a-4", name: "energy-band-plot.png", kind: "plot", size: "256 KB", modified: "6h ago", status: "ready", tags: ["visualization"], description: "Energy band structure plot for top-5 candidates" },
  { id: "a-5", name: "rag-index-v3", kind: "index", size: "12.1 MB", modified: "8h ago", status: "ready", tags: ["rag", "index"], description: "RAG vector index with pgvector embeddings" },
  { id: "a-6", name: "qwen2.5-72b-instruct", kind: "model", size: "142 GB", modified: "1d ago", status: "ready", tags: ["llm", "model"], description: "Qwen2.5 72B Instruct model, pre-staged" },
  { id: "a-7", name: "md-simulation-B3.log", kind: "file", size: "340 KB", modified: "1d ago", status: "ready", tags: ["md", "simulation"], description: "Molecular dynamics simulation log for candidate B3" },
  { id: "a-8", name: "stability-analysis-report.json", kind: "document", size: "28 KB", modified: "2d ago", status: "ready", tags: ["analysis", "stability"], description: "JSON report: stability analysis under humidity stress" },
  { id: "a-9", name: "training-data-v4", kind: "table", size: "45 MB", modified: "3d ago", status: "indexing", tags: ["ml", "training"], description: "GNN training dataset — currently indexing" },
  { id: "a-10", name: "crystal-structures", kind: "file", size: "2.1 MB", modified: "4d ago", status: "stale", tags: ["crystal", "structures"], description: "Crystal structure files (POSCAR) — needs update" },
  { id: "a-11", name: "errata-dft-run-42.log", kind: "file", size: "12 KB", modified: "5d ago", status: "error", tags: ["dft", "error"], description: "DFT run #42 error log — calculation diverged" },
  { id: "a-12", name: "literature-review-q4.txt", kind: "document", size: "89 KB", modified: "1w ago", status: "empty", tags: ["literature", "review"], description: "Q4 literature review — needs content" },
];

/* ── Status helpers ────────────────────────────────────────────────────── */

function kindIcon(kind: string): string {
  return { file: "📄", metric: "📊", table: "📋", plot: "📈", document: "📑", model: "🧠", index: "🗃️" }[kind] ?? "📄";
}

function statusColor(s: string): string {
  return { ready: "#34d399", indexing: "#38bdf8", stale: "#fbbf24", error: "#f87171", empty: "#64748b" }[s] ?? "#94a3b8";
}

function statusIcon(s: string): string {
  return { ready: "●", indexing: "⟳", stale: "▲", error: "✗", empty: "○" }[s] ?? "●";
}

/* ── Main view ─────────────────────────────────────────────────────────── */

export function Artifacts() {
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<string>("all");

  const kinds = useMemo(() => {
    const s = new Set(mockArtifacts.map((a) => a.kind));
    return ["all", ...Array.from(s)];
  }, []);

  const filtered = useMemo(() => {
    return mockArtifacts.filter((a) => {
      const matchSearch = !search || a.name.toLowerCase().includes(search.toLowerCase()) || a.description.toLowerCase().includes(search.toLowerCase()) || a.tags.some((t) => t.includes(search.toLowerCase()));
      const matchKind = kindFilter === "all" || a.kind === kindFilter;
      return matchSearch && matchKind;
    });
  }, [search, kindFilter]);

  return (
    <Shell title="Artifacts">
      {/* Search + filter bar */}
      <div style={styles.toolbar}>
        <input
          style={styles.searchInput}
          placeholder="Search artifacts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div style={styles.filterRow}>
          {kinds.map((k) => (
            <button
              key={k}
              style={{
                ...styles.filterBtn,
                ...(kindFilter === k ? styles.filterBtnActive : {}),
              }}
              onClick={() => setKindFilter(k)}
            >
              {k === "all" ? "All" : kindIcon(k)} {k}
            </button>
          ))}
        </div>
      </div>

      {/* Results count */}
      <div style={styles.resultCount}>
        {filtered.length} artifact{filtered.length !== 1 ? "s" : ""}
      </div>

      {/* Artifact list */}
      <div style={styles.list}>
        {filtered.map((a) => (
          <div key={a.id} style={styles.artifactItem}>
            <div style={styles.artifactHeader}>
              <span style={styles.artifactIcon}>{kindIcon(a.kind)}</span>
              <div style={styles.artifactInfo}>
                <span style={styles.artifactName}>{a.name}</span>
                <span style={styles.artifactMeta}>
                  {a.size} · {a.modified} · {a.kind}
                </span>
              </div>
              <span style={{ color: statusColor(a.status), fontWeight: 600 }}>
                {statusIcon(a.status)} {a.status}
              </span>
            </div>
            <div style={styles.artifactDesc}>{a.description}</div>
            <div style={styles.tags}>
              {a.tags.map((t) => (
                <span key={t} style={styles.tag}>
                  {t}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Shell>
  );
}

/* ── Styles ────────────────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  toolbar: {
    marginBottom: "1rem",
  },
  searchInput: {
    width: "100%",
    padding: "0.625rem 1rem",
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "8px",
    color: "#e2e8f0",
    fontSize: "0.875rem",
    outline: "none",
    marginBottom: "0.5rem",
  },
  filterRow: {
    display: "flex",
    gap: "0.375rem",
    flexWrap: "wrap" as const,
  },
  filterBtn: {
    padding: "0.375rem 0.75rem",
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "6px",
    color: "#94a3b8",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  filterBtnActive: {
    backgroundColor: "#334155",
    color: "#f1f5f9",
    borderColor: "#38bdf8",
  },
  resultCount: {
    fontSize: "0.75rem",
    color: "#64748b",
    marginBottom: "0.75rem",
  },
  list: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.5rem",
  },
  artifactItem: {
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "0.75rem 1rem",
  },
  artifactHeader: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
  },
  artifactIcon: {
    fontSize: "1.25rem",
  },
  artifactInfo: {
    flex: 1,
    minWidth: 0,
  },
  artifactName: {
    display: "block",
    fontWeight: 600,
    fontSize: "0.875rem",
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  artifactMeta: {
    display: "block",
    fontSize: "0.75rem",
    color: "#64748b",
  },
  artifactDesc: {
    fontSize: "0.8rem",
    color: "#94a3b8",
    marginTop: "0.375rem",
  },
  tags: {
    display: "flex",
    gap: "0.375rem",
    marginTop: "0.375rem",
    flexWrap: "wrap" as const,
  },
  tag: {
    fontSize: "0.65rem",
    padding: "0.125rem 0.5rem",
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "9999px",
    color: "#94a3b8",
  },
};
