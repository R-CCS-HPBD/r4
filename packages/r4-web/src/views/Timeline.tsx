/**
 * Timeline view — project activity timeline.
 *
 * Mirrors r4-tui-v3 bottom-panel timeline and research timeline patterns.
 * Shows chronological activity events with filtering by type and time range.
 */

import { useState, useMemo } from "react";
import { Shell } from "../components/Shell";

/* ── Types ─────────────────────────────────────────────────────────────── */

interface TimelineEvent {
  id: string;
  time: string;
  timestamp: number; // for sorting/filtering
  type: "provision" | "task" | "result" | "error" | "deploy" | "system" | "knowledge";
  agent: string;
  title: string;
  description: string;
  duration?: string;
}

/* ── Mock data ─────────────────────────────────────────────────────────── */

const mockEvents: TimelineEvent[] = [
  { id: "t-1", time: "Jul 15 14:32", timestamp: 1752570720000, type: "task", agent: "researcher-1", title: "Task completed: DFT validation", description: "Completed DFT validation for candidate A7 (bandgap: 2.1 eV, stable)", duration: "4h 12m" },
  { id: "t-2", time: "Jul 15 14:15", timestamp: 1752569700000, type: "error", agent: "researcher-3", title: "Task failed: RAG indexing", description: "RAG indexing failed on corpus-3 — embedding dimension mismatch", duration: "15m" },
  { id: "t-3", time: "Jul 15 13:00", timestamp: 1752565200000, type: "provision", agent: "provisioner", title: "Endpoint created: deepseek-7b-v4", description: "Provisioned deepseek-7b-v4 on gpu-a100-1, partition=gpu, time=04:00:00", duration: "3m 20s" },
  { id: "t-4", time: "Jul 15 12:30", timestamp: 1752563400000, type: "result", agent: "researcher-1", title: "Goal achieved: Top-5 candidates", description: "Identified top-5 perovskite candidates: A1, A2, A3, B1, B3", duration: "—" },
  { id: "t-5", time: "Jul 15 11:45", timestamp: 1752560700000, type: "deploy", agent: "deployer", title: "Model staged: Qwen2.5-72B", description: "Staged Qwen/Qwen2.5-72B-Instruct to /models/qwen2.5-72b (142 GB)", duration: "22m 10s" },
  { id: "t-6", time: "Jul 15 10:00", timestamp: 1752554400000, type: "knowledge", agent: "researcher-2", title: "Knowledge indexed: corpus-v3", description: "Added 89 documents to knowledge corpus, embeddings indexed", duration: "5m 30s" },
  { id: "t-7", time: "Jul 15 09:15", timestamp: 1752551700000, type: "system", agent: "system", title: "System health check passed", description: "All 6 services healthy. Gateway 12ms, UMRS 8ms, Slurm 3ms", duration: "—" },
  { id: "t-8", time: "Jul 14 22:00", timestamp: 1752505200000, type: "task", agent: "researcher-2", title: "Task running: MD simulation", description: "Running MD simulation on candidate B3 (4h estimated)", duration: "2h 30m" },
  { id: "t-9", time: "Jul 14 18:30", timestamp: 1752492600000, type: "task", agent: "researcher-1", title: "Task started: Bandgap prediction", description: "Launched GNN bandgap prediction on 500 candidates", duration: "1h 45m" },
  { id: "t-10", time: "Jul 14 16:00", timestamp: 1752483200000, type: "provision", agent: "provisioner", title: "Endpoint terminated: gpt-oss-20b", description: "Auto-terminated gpt-oss-20b after TTL=12h expired", duration: "—" },
  { id: "t-11", time: "Jul 14 14:20", timestamp: 1752477200000, type: "error", agent: "researcher-2", title: "Task failed: Crystal structure", description: "MD crash — nan energy detected at step 12847", duration: "1h 20m" },
  { id: "t-12", time: "Jul 14 12:00", timestamp: 1752469200000, type: "result", agent: "system", title: "Loop iteration #23 complete", description: "Research loop iteration #23 complete. 2 tasks succeeded, 2 failed. 3 requeued.", duration: "6h 40m" },
  { id: "t-13", time: "Jul 13 20:00", timestamp: 1752414000000, type: "knowledge", agent: "researcher-3", title: "Knowledge updated: literature", description: "Added 12 papers to literature review collection", duration: "2m 15s" },
  { id: "t-14", time: "Jul 13 16:30", timestamp: 1752401400000, type: "deploy", agent: "deployer", title: "Model loaded: deepseek-7b-v4", description: "Loaded deepseek-7b-v4 on gpu-a100-2 (load time: 45s)", duration: "45s" },
  { id: "t-15", time: "Jul 13 14:00", timestamp: 1752392400000, type: "system", agent: "system", title: "Config update: Slurm partition", description: "Added gpu-a100-3 to partition=gpu. Total nodes: 32 → 33", duration: "—" },
];

/* ── Type helpers ──────────────────────────────────────────────────────── */

function typeColor(t: string): string {
  return {
    provision: "#38bdf8",
    task: "#a78bfa",
    result: "#34d399",
    error: "#f87171",
    deploy: "#fb923c",
    system: "#64748b",
    knowledge: "#f472b6",
  }[t] ?? "#94a3b8";
}

function typeIcon(t: string): string {
  return {
    provision: "◆",
    task: "▶",
    result: "✓",
    error: "✗",
    deploy: "⚡",
    system: "⚙",
    knowledge: "📚",
  }[t] ?? "•";
}

/* ── Main view ─────────────────────────────────────────────────────────── */

export function Timeline() {
  const [filterType, setFilterType] = useState<string>("all");
  const [search, setSearch] = useState("");

  const types = useMemo(() => {
    const s = new Set(mockEvents.map((e) => e.type));
    return ["all", ...Array.from(s)];
  }, []);

  const filtered = useMemo(() => {
    return mockEvents
      .filter((e) => {
        const matchType = filterType === "all" || e.type === filterType;
        const matchSearch = !search || e.title.toLowerCase().includes(search.toLowerCase()) || e.description.toLowerCase().includes(search.toLowerCase());
        return matchType && matchSearch;
      })
      .sort((a, b) => b.timestamp - a.timestamp);
  }, [filterType, search]);

  /* Counts per type for summary bar */
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of mockEvents) {
      c[e.type] = (c[e.type] ?? 0) + 1;
    }
    return c;
  }, []);

  return (
    <Shell title="Timeline">
      {/* Summary bar */}
      <div style={styles.summaryBar}>
        {Object.entries(counts).map(([type, count]) => (
          <div key={type} style={styles.summaryItem}>
            <span style={{ color: typeColor(type), fontSize: "0.7rem", textTransform: "capitalize" as const }}>
              {typeIcon(type)} {type}
            </span>
            <span style={styles.summaryValue}>{count}</span>
          </div>
        ))}
      </div>

      {/* Search + filter */}
      <div style={styles.toolbar}>
        <input
          style={styles.searchInput}
          placeholder="Filter events..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div style={styles.filterRow}>
          {types.map((t) => (
            <button
              key={t}
              style={{
                ...styles.filterBtn,
                ...(filterType === t ? styles.filterBtnActive : {}),
              }}
              onClick={() => setFilterType(t)}
            >
              {t === "all" ? "All" : `${typeIcon(t)} ${t}`}
            </button>
          ))}
        </div>
      </div>

      {/* Timeline */}
      <div style={styles.timeline}>
        {filtered.map((e, i) => (
          <div key={e.id} style={styles.eventItem}>
            {/* Timeline line */}
            <div style={styles.timelineLine}>
              <div
                style={{
                  ...styles.timelineDot,
                  backgroundColor: typeColor(e.type),
                }}
              />
              {i < filtered.length - 1 && <div style={styles.timelineConnector} />}
            </div>

            {/* Event content */}
            <div style={styles.eventContent}>
              <div style={styles.eventHeader}>
                <span style={styles.eventTime}>{e.time}</span>
                <span
                  style={{
                    ...styles.eventType,
                    color: typeColor(e.type),
                    borderColor: `${typeColor(e.type)}40`,
                    backgroundColor: `${typeColor(e.type)}15`,
                  }}
                >
                  {typeIcon(e.type)} {e.type}
                </span>
                {e.duration && <span style={styles.eventDuration}>⏱ {e.duration}</span>}
              </div>
              <div style={styles.eventTitle}>{e.title}</div>
              <div style={styles.eventDesc}>{e.description}</div>
              <div style={styles.eventAgent}>by @{e.agent}</div>
            </div>
          </div>
        ))}
      </div>
    </Shell>
  );
}

/* ── Styles ────────────────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  summaryBar: {
    display: "flex",
    gap: "1rem",
    marginBottom: "1rem",
    flexWrap: "wrap" as const,
  },
  summaryItem: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    gap: "0.125rem",
  },
  summaryValue: {
    fontSize: "1.25rem",
    fontWeight: 700,
    color: "#e2e8f0",
  },
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
  timeline: {
    position: "relative" as const,
    paddingLeft: "2rem",
  },
  eventItem: {
    display: "flex",
    gap: "1rem",
    marginBottom: "0.25rem",
  },
  timelineLine: {
    position: "relative" as const,
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    width: "12px",
    flexShrink: 0,
  },
  timelineDot: {
    width: "12px",
    height: "12px",
    borderRadius: "50%",
    flexShrink: 0,
  },
  timelineConnector: {
    width: "2px",
    flex: 1,
    backgroundColor: "#334155",
  },
  eventContent: {
    flex: 1,
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "0.75rem 1rem",
  },
  eventHeader: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    marginBottom: "0.375rem",
    flexWrap: "wrap" as const,
  },
  eventTime: {
    fontSize: "0.75rem",
    color: "#64748b",
  },
  eventType: {
    fontSize: "0.65rem",
    fontWeight: 700,
    padding: "0.125rem 0.5rem",
    borderRadius: "9999px",
    textTransform: "uppercase" as const,
  },
  eventDuration: {
    fontSize: "0.7rem",
    color: "#64748b",
  },
  eventTitle: {
    fontSize: "0.875rem",
    fontWeight: 600,
    color: "#f1f5f9",
    marginBottom: "0.25rem",
  },
  eventDesc: {
    fontSize: "0.8rem",
    color: "#94a3b8",
    marginBottom: "0.25rem",
  },
  eventAgent: {
    fontSize: "0.75rem",
    color: "#38bdf8",
  },
};
