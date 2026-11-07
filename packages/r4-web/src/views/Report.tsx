/**
 * Report view — project report with findings, analysis results.
 *
 * Mirrors r4-tui-v3 research workspace panels: mission control metrics,
 * verification chat results, explainability panel, and readiness assessment.
 */

import { Shell } from "../components/Shell";

/* ── Types ─────────────────────────────────────────────────────────────── */

interface Finding {
  id: string;
  severity: "critical" | "warning" | "info";
  category: string;
  title: string;
  description: string;
  evidence: string;
}

interface MetricResult {
  label: string;
  value: string;
  score?: number;
  status: "pass" | "warn" | "fail";
}

interface ChatResult {
  id: string;
  question: string;
  answer: string;
  confidence: number;
}

interface ReadinessItem {
  check: string;
  status: "pass" | "fail" | "pending";
  detail: string;
}

/* ── Mock data ─────────────────────────────────────────────────────────── */

const mockFindings: Finding[] = [
  {
    id: "f-1",
    severity: "critical",
    category: "Performance",
    title: "GPU memory overflow on batch size 32",
    description: "vLLM OOM when batch_size=32 on A100-40GB. Reduce to 16.",
    evidence: "Worker logs: cudaMalloc failed (0x70000)",
  },
  {
    id: "f-2",
    severity: "warning",
    category: "Data Quality",
    title: "RAG corpus has 23% duplicate embeddings",
    description: "Embedding space shows significant cluster overlap in cosine similarity > 0.95.",
    evidence: "342 / 1489 documents within 0.01 of a neighbor",
  },
  {
    id: "f-3",
    severity: "warning",
    category: "Security",
    title: "EgressGuard not enforcing domain allowlist",
    description: "MCP server custom_mcp.py bypasses proxy layer.",
    evidence: "Direct socket connection detected in traffic capture",
  },
  {
    id: "f-4",
    severity: "info",
    category: "Optimization",
    title: "Model loading time exceeds 120s threshold",
    description: "72B model takes 180s from cold start. Prefetch recommended.",
    evidence: "Prometheus: umrs_model_load_seconds_count > 120",
  },
  {
    id: "f-5",
    severity: "info",
    category: "Reliability",
    title: "Slurm job requeue rate at 8%",
    description: "Requeue rate within acceptable bounds but trending up.",
    evidence: "Last 7 days: 127 / 1580 jobs requeued",
  },
];

const mockMetrics: MetricResult[] = [
  { label: "Inference latency (p50)", value: "142ms", score: 95, status: "pass" },
  { label: "Inference latency (p99)", value: "1.2s", score: 88, status: "pass" },
  { label: "Throughput (req/s)", value: "47", score: 92, status: "pass" },
  { label: "Error rate", value: "0.3%", score: 98, status: "pass" },
  { label: "RAG recall@10", value: "0.78", score: 65, status: "warn" },
  { label: "Task success rate", value: "91%", score: 82, status: "pass" },
  { label: "Agent self-heal rate", value: "67%", score: 55, status: "warn" },
  { label: "Data quality score", value: "0.82", score: 72, status: "warn" },
];

const mockChatResults: ChatResult[] = [
  {
    id: "cr-1",
    question: "Why did task-4 fail?",
    answer: "Task-4 (Stability analysis) failed due to a resource contention on gpu-a100-2. The competing task-7 (MD simulation) held 4 GPUs. Auto-requeue succeeded on second attempt after gpu-a100-2 freed 2 GPUs.",
    confidence: 0.92,
  },
  {
    id: "cr-2",
    question: "What is the research loop state?",
    answer: "Loop is in active iteration #23. Current goal: DFT validation (goal-2). 3 of 5 candidates validated. Next step: bandgap prediction via GNN model. Plan suggests prioritizing candidate A9 due to highest predicted stability.",
    confidence: 0.87,
  },
  {
    id: "cr-3",
    question: "Recommend next research direction",
    answer: "Based on convergence patterns in iterations 18-23, the research loop should explore the high-bandgap region (Eg > 2.0eV). Suggested actions: (1) retrain GNN on updated validation set, (2) propose 3 new candidates in target region, (3) allocate additional GPU resources for batch DFT.",
    confidence: 0.74,
  },
];

const mockReadiness: ReadinessItem[] = [
  { check: "Gateway connectivity", status: "pass", detail: "HTTP 200 on /health (12ms)" },
  { check: "UMRS API", status: "pass", detail: "All 47 endpoints responsive" },
  { check: "Slurm scheduler", status: "pass", detail: "32 nodes online, 28 available" },
  { check: "Postgres", status: "pass", detail: "Replication lag: 12ms, connections: 24/100" },
  { check: "MinIO S3", status: "pass", detail: "Bucket r4-data: 2.3TB, 847 objects" },
  { check: "Redis", status: "pass", detail: "Memory: 128MB/512MB, hit rate: 97.2%" },
  { check: "SSL certificates", status: "pending", detail: "Self-hosted tier — not applicable" },
  { check: "EgressGuard", status: "fail", detail: "Domain allowlist not enforced for custom MCP" },
];

/* ── Status helpers ────────────────────────────────────────────────────── */

function severityColor(s: string): string {
  return { critical: "#f87171", warning: "#fbbf24", info: "#38bdf8" }[s] ?? "#94a3b8";
}

function severityIcon(s: string): string {
  return { critical: "⬛", warning: "▲", info: "●" }[s] ?? "●";
}

function statusColor(s: string): string {
  return { pass: "#34d399", warn: "#fbbf24", fail: "#f87171", pending: "#64748b" }[s] ?? "#94a3b8";
}

function statusIcon(s: string): string {
  return { pass: "✓", fail: "✗", pending: "—", warn: "▲" }[s] ?? "—";
}

/* ── Components ────────────────────────────────────────────────────────── */

function MetricScores({ metrics }: { metrics: MetricResult[] }) {
  return (
    <div style={styles.metricList}>
      {metrics.map((m) => (
        <div key={m.label} style={styles.metricRow}>
          <span style={styles.metricName}>{m.label}</span>
          <span style={styles.metricValue}>{m.value}</span>
          {m.score !== undefined && (
            <>
              <div style={styles.metricBarOuter}>
                <div
                  style={{
                    ...styles.metricBarInner,
                    width: `${m.score}%`,
                    backgroundColor: m.score >= 80 ? "#34d399" : m.score >= 60 ? "#fbbf24" : "#f87171",
                  }}
                />
              </div>
              <span style={{ color: statusColor(m.status), minWidth: "3ch", textAlign: "right" as const }}>
                {m.score}%
              </span>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

function FindingsList({ findings }: { findings: Finding[] }) {
  const critical = findings.filter((f) => f.severity === "critical");
  const warnings = findings.filter((f) => f.severity === "warning");
  const infos = findings.filter((f) => f.severity === "info");

  return (
    <div style={styles.findingsContainer}>
      {critical.length > 0 && (
        <div style={styles.findingSection}>
          <h4 style={{ ...styles.sectionTitle, color: "#f87171" }}>
            {severityIcon("critical")} Critical ({critical.length})
          </h4>
          {critical.map((f) => <FindingCard key={f.id} finding={f} />)}
        </div>
      )}
      {warnings.length > 0 && (
        <div style={styles.findingSection}>
          <h4 style={{ ...styles.sectionTitle, color: "#fbbf24" }}>
            {severityIcon("warning")} Warnings ({warnings.length})
          </h4>
          {warnings.map((f) => <FindingCard key={f.id} finding={f} />)}
        </div>
      )}
      {infos.length > 0 && (
        <div style={styles.findingSection}>
          <h4 style={{ ...styles.sectionTitle, color: "#38bdf8" }}>
            {severityIcon("info")} Informational ({infos.length})
          </h4>
          {infos.map((f) => <FindingCard key={f.id} finding={f} />)}
        </div>
      )}
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <div style={styles.findingCard}>
      <div style={styles.findingHeader}>
        <span style={{ color: severityColor(finding.severity), fontWeight: 700 }}>
          {severityIcon(finding.severity)} {finding.title}
        </span>
        <span style={findingBadgeStyle(finding.severity)}>{finding.severity}</span>
      </div>
      <div style={styles.findingCategory}>{finding.category}</div>
      <div style={styles.findingDesc}>{finding.description}</div>
      <div style={styles.findingEvidence}>
        <strong>Evidence: </strong>{finding.evidence}
      </div>
    </div>
  );
}

function ChatResults({ results }: { results: ChatResult[] }) {
  return (
    <div style={styles.chatContainer}>
      {results.map((r) => (
        <div key={r.id} style={styles.chatItem}>
          <div style={styles.chatQuestion}>Q: {r.question}</div>
          <div style={styles.chatAnswer}>A: {r.answer}</div>
          <div style={styles.chatConfidence}>
            Confidence:{" "}
            <span style={{ color: r.confidence >= 0.8 ? "#34d399" : r.confidence >= 0.6 ? "#fbbf24" : "#f87171" }}>
              {(r.confidence * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ReadinessPanel({ items }: { items: ReadinessItem[] }) {
  return (
    <div style={styles.tableContainer}>
      <h3 style={styles.panelTitle}>Readiness Assessment</h3>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Check</th>
            <th style={styles.th}>Status</th>
            <th style={styles.th}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.check}>
              <td style={styles.td}>{i.check}</td>
              <td style={{ ...styles.td, color: statusColor(i.status), fontWeight: 600 }}>
                {statusIcon(i.status)} {i.status}
              </td>
              <td style={{ ...styles.td, color: "#94a3b8", fontSize: "0.8rem" }}>{i.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Main view ─────────────────────────────────────────────────────────── */

export function Report() {
  return (
    <Shell title="Report">
      {/* Summary bar */}
      <div style={styles.summaryBar}>
        <div style={styles.summaryItem}>
          <span style={styles.summaryLabel}>Total Findings</span>
          <span style={{ ...styles.summaryValue, color: "#fbbf24" }}>{mockFindings.length}</span>
        </div>
        <div style={styles.summaryItem}>
          <span style={styles.summaryLabel}>Critical</span>
          <span style={{ ...styles.summaryValue, color: "#f87171" }}>
            {mockFindings.filter((f) => f.severity === "critical").length}
          </span>
        </div>
        <div style={styles.summaryItem}>
          <span style={styles.summaryLabel}>Warnings</span>
          <span style={{ ...styles.summaryValue, color: "#fbbf24" }}>
            {mockFindings.filter((f) => f.severity === "warning").length}
          </span>
        </div>
        <div style={styles.summaryItem}>
          <span style={styles.summaryLabel}>Checks Pass</span>
          <span style={{ ...styles.summaryValue, color: "#34d399" }}>
            {mockReadiness.filter((r) => r.status === "pass").length}/{mockReadiness.length}
          </span>
        </div>
      </div>

      {/* Metrics scores */}
      <div style={styles.panel}>
        <h3 style={styles.panelTitle}>Performance & Quality Metrics</h3>
        <MetricScores metrics={mockMetrics} />
      </div>

      {/* Two-column */}
      <div style={styles.twoCol}>
        <ReadinessPanel items={mockReadiness} />
        <ChatResults results={mockChatResults} />
      </div>

      {/* Findings */}
      <div style={styles.panel}>
        <h3 style={styles.panelTitle}>Findings & Analysis</h3>
        <FindingsList findings={mockFindings} />
      </div>
    </Shell>
  );
}

/* ── Styles ────────────────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  twoCol: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "1.5rem",
    marginBottom: "1.5rem",
  },
  summaryBar: {
    display: "flex",
    gap: "1.5rem",
    marginBottom: "1.5rem",
    flexWrap: "wrap" as const,
  },
  summaryItem: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.25rem",
  },
  summaryLabel: {
    fontSize: "0.7rem",
    color: "#64748b",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  summaryValue: {
    fontSize: "1.5rem",
    fontWeight: 700,
  },
  panel: {
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "1rem",
    marginBottom: "1.5rem",
  },
  panelTitle: {
    fontSize: "0.875rem",
    fontWeight: 600,
    color: "#94a3b8",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "0.75rem",
  },
  metricList: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.5rem",
  },
  metricRow: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
  },
  metricName: {
    minWidth: "20ch",
    fontSize: "0.875rem",
  },
  metricValue: {
    minWidth: "4ch",
    textAlign: "right" as const,
    fontSize: "0.875rem",
    fontWeight: 600,
  },
  metricBarOuter: {
    flex: 1,
    height: "6px",
    backgroundColor: "#334155",
    borderRadius: "3px",
    overflow: "hidden",
  },
  metricBarInner: {
    height: "100%",
    borderRadius: "3px",
  },
  findingsContainer: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "1rem",
  },
  findingSection: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.5rem",
  },
  sectionTitle: {
    fontSize: "0.8rem",
    fontWeight: 700,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "0.25rem",
  },
  findingCard: {
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "6px",
    padding: "0.75rem",
  },
  findingHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "0.25rem",
  },
  findingCategory: {
    fontSize: "0.75rem",
    color: "#64748b",
    marginBottom: "0.25rem",
  },
  findingDesc: {
    fontSize: "0.875rem",
    color: "#cbd5e1",
    marginBottom: "0.25rem",
  },
  findingEvidence: {
    fontSize: "0.75rem",
    color: "#64748b",
    fontFamily: "monospace",
  },
  tableContainer: {
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "1rem",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
  },
  th: {
    textAlign: "left" as const,
    padding: "0.5rem",
    borderBottom: "1px solid #334155",
    fontSize: "0.75rem",
    color: "#94a3b8",
    textTransform: "uppercase" as const,
  },
  td: {
    padding: "0.5rem",
    fontSize: "0.875rem",
  },
  chatContainer: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.75rem",
  },
  chatItem: {
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "6px",
    padding: "0.75rem",
  },
  chatQuestion: {
    fontSize: "0.875rem",
    fontWeight: 600,
    color: "#38bdf8",
    marginBottom: "0.375rem",
  },
  chatAnswer: {
    fontSize: "0.875rem",
    color: "#cbd5e1",
    marginBottom: "0.375rem",
  },
  chatConfidence: {
    fontSize: "0.75rem",
    color: "#94a3b8",
  },
};

function findingBadgeStyle(severity: string): React.CSSProperties {
  return {
    fontSize: "0.65rem",
    fontWeight: 700,
    textTransform: "uppercase",
    padding: "0.125rem 0.5rem",
    borderRadius: "9999px",
    backgroundColor: `${severityColor(severity)}20`,
    color: severityColor(severity),
  };
}
