/**
 * Overview view — dashboard with project status, recent activity, metrics.
 *
 * Mirrors r4-tui-v3 dashboard (DashboardRoute) and research workspace
 * (ResearchRoute) data patterns: project, agents, goals, tasks, endpoints,
 * HPC connection, models, jobs, skills.
 */

import { Shell } from "../components/Shell";
import type { ResearchProject } from "../../../r4-tui-v3/src/types";

/* ── Types ─────────────────────────────────────────────────────────────── */

interface MetricCard {
  label: string;
  value: string | number;
  color: string;
}

interface StatusRow {
  component: string;
  state: string;
  color: string;
}

interface ActivityEntry {
  id: string;
  time: string;
  agent: string;
  action: string;
  details: string;
}

/* ── Mock data ─────────────────────────────────────────────────────────── */

const mockProject: ResearchProject = {
  id: "proj-001",
  name: "Material Discovery Pipeline",
  description: "Autonomous research loop for perovskite solar cell optimization",
  status: "active",
  goalIds: ["goal-1", "goal-2"],
};

const mockMetrics: MetricCard[] = [
  { label: "Agents", value: "2/5", color: "#38bdf8" },
  { label: "Goals", value: "3", color: "#a78bfa" },
  { label: "Tasks", value: "7 pending", color: "#fbbf24" },
  { label: "Endpoints", value: "4", color: "#34d399" },
  { label: "HPC", value: "connected", color: "#34d399" },
  { label: "Models", value: "6", color: "#38bdf8" },
  { label: "Jobs", value: "12", color: "#38bdf8" },
  { label: "Skills", value: "8", color: "#38bdf8" },
  { label: "Knowledge", value: "142 docs", color: "#f472b6" },
];

const mockStatus: StatusRow[] = [
  { component: "Gateway", state: "connected", color: "#34d399" },
  { component: "UMRS", state: "connected", color: "#34d399" },
  { component: "HPC (Slurm)", state: "connected", color: "#34d399" },
  { component: "Postgres", state: "connected", color: "#34d399" },
  { component: "MinIO S3", state: "connected", color: "#34d399" },
  { component: "Redis", state: "connected", color: "#34d399" },
];

const mockActivity: ActivityEntry[] = [
  { id: "act-1", time: "2m ago", agent: "researcher-1", action: "Task completed", details: "Completed goal-2 task: DFT validation of candidate A7" },
  { id: "act-2", time: "15m ago", agent: "researcher-2", action: "Task running", details: "Running MD simulation on candidate B3 (4h estimated)" },
  { id: "act-3", time: "32m ago", agent: "researcher-1", action: "Goal achieved", details: "Achieved goal-1: Identify top-5 perovskite candidates" },
  { id: "act-4", time: "1h ago", agent: "provisioner", action: "Endpoint created", details: "Created endpoint deepseek-7b-v4 on gpu-a100-1" },
  { id: "act-5", time: "2h ago", agent: "system", action: "Model staged", details: "Staged Qwen/Qwen2.5-72B-Instruct to /models/qwen2.5-72b" },
  { id: "act-6", time: "3h ago", agent: "researcher-3", action: "Task failed", details: "Failed: RAG indexing error on corpus-3 (retries: 1/3)" },
];

const mockGoals = [
  { id: "goal-1", title: "Identify top-5 perovskite candidates", status: "achieved" },
  { id: "goal-2", title: "DFT validation of top candidates", status: "active" },
  { id: "goal-3", title: "Experimental synthesis protocol", status: "proposed" },
];

const mockTasks = [
  { id: "task-1", title: "DFT validation of candidate A7", status: "completed", agent: "researcher-1" },
  { id: "task-2", title: "MD simulation on candidate B3", status: "running", agent: "researcher-2" },
  { id: "task-3", title: "Bandgap prediction via GNN", status: "pending", agent: "researcher-1" },
  { id: "task-4", title: "Stability analysis under humidity", status: "pending", agent: "researcher-3" },
  { id: "task-5", title: "Crystal structure optimization", status: "failed", agent: "researcher-2" },
];

/* ── Status helpers ────────────────────────────────────────────────────── */

function statusColor(state: string): string {
  const map: Record<string, string> = {
    connected: "#34d399",
    connecting: "#fbbf24",
    degraded: "#fb923c",
    disconnected: "#f87171",
    active: "#34d399",
    achieved: "#34d399",
    proposed: "#fbbf24",
    running: "#38bdf8",
    pending: "#a5a3c2",
    completed: "#34d399",
    failed: "#f87171",
    cancelled: "#a5a3c2",
    idle: "#a5a3c2",
    thinking: "#38bdf8",
    executing: "#34d399",
  };
  return map[state] ?? "#a5a3c2";
}

function statusLabel(state: string): string {
  return state.charAt(0).toUpperCase() + state.slice(1);
}

/* ── Components ────────────────────────────────────────────────────────── */

function MetricCards({ cards }: { cards: MetricCard[] }) {
  return (
    <div style={styles.cardGrid}>
      {cards.map((c) => (
        <div key={c.label} style={styles.metricCard}>
          <div style={styles.metricLabel}>{c.label}</div>
          <div style={{ ...styles.metricValue, color: c.color }}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

function StatusTable({ rows }: { rows: StatusRow[] }) {
  return (
    <div style={styles.tableContainer}>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Component</th>
            <th style={styles.th}>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.component}>
              <td style={styles.td}>{r.component}</td>
              <td style={styles.td}>
                <span style={{ color: r.color, fontWeight: 600 }}>
                  ● {r.state}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActivityFeed({ entries }: { entries: ActivityEntry[] }) {
  return (
    <div style={styles.activityFeed}>
      {entries.map((a) => (
        <div key={a.id} style={styles.activityItem}>
          <div style={styles.activityHeader}>
            <span style={styles.activityTime}>{a.time}</span>
            <span style={styles.activityAgent}>{a.agent}</span>
            <span style={styles.activityAction}>{a.action}</span>
          </div>
          <div style={styles.activityDetails}>{a.details}</div>
        </div>
      ))}
    </div>
  );
}

function GoalsPanel({ goals }: { goals: typeof mockGoals }) {
  return (
    <div style={styles.panel}>
      <h3 style={styles.panelTitle}>Goals</h3>
      {goals.map((g) => (
        <div key={g.id} style={styles.goalItem}>
          <span style={{ color: statusColor(g.status) }}>
            {g.status === "achieved" ? "✓" : g.status === "active" ? "⟳" : "○"}
          </span>
          <span style={styles.goalText}>{g.title}</span>
          <span style={styles.goalStatus}>{statusLabel(g.status)}</span>
        </div>
      ))}
    </div>
  );
}

function TasksPanel({ tasks }: { tasks: typeof mockTasks }) {
  return (
    <div style={styles.panel}>
      <h3 style={styles.panelTitle}>Tasks</h3>
      {tasks.map((t) => (
        <div key={t.id} style={styles.taskItem}>
          <span style={{ color: statusColor(t.status) }}>
            {t.status === "completed" ? "✓" : t.status === "running" ? "⟳" : t.status === "failed" ? "✗" : "○"}
          </span>
          <span style={styles.taskText}>{t.title}</span>
          <span style={styles.taskAgent}>@{t.agent}</span>
          <span style={{ ...styles.taskStatus, color: statusColor(t.status) }}>
            {statusLabel(t.status)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Main view ─────────────────────────────────────────────────────────── */

export function Overview() {
  return (
    <Shell title="Overview">
      {/* Project header */}
      <div style={styles.projectHeader}>
        <h2 style={styles.projectName}>{mockProject.name}</h2>
        <span style={statusBadgeStyle(statusColor(mockProject.status))}>
          {statusLabel(mockProject.status)}
        </span>
      </div>
      <p style={styles.projectDesc}>{mockProject.description}</p>

      {/* Metric cards */}
      <MetricCards cards={mockMetrics} />

      {/* Two-column: status table + activity feed */}
      <div style={styles.twoCol}>
        <StatusTable rows={mockStatus} />
        <ActivityFeed entries={mockActivity} />
      </div>

      {/* Two-column: goals + tasks */}
      <div style={styles.twoCol}>
        <GoalsPanel goals={mockGoals} />
        <TasksPanel tasks={mockTasks} />
      </div>
    </Shell>
  );
}

/* ── Styles ────────────────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
    gap: "0.75rem",
    marginBottom: "2rem",
  },
  metricCard: {
    backgroundColor: "#1a1826",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "0.75rem 1rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.25rem",
  },
  metricLabel: {
    fontSize: "0.75rem",
    color: "#a5a3c2",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  metricValue: {
    fontSize: "1.25rem",
    fontWeight: 700,
  },
  twoCol: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "1.5rem",
    marginBottom: "1.5rem",
  },
  projectHeader: {
    display: "flex",
    alignItems: "center",
    gap: "1rem",
    marginBottom: "0.25rem",
  },
  projectName: {
    fontSize: "1.5rem",
    fontWeight: 700,
    margin: 0,
  },
  projectDesc: {
    color: "#a5a3c2",
    marginBottom: "1.5rem",
  },
  tableContainer: {
    backgroundColor: "#1a1826",
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
    padding: "0.5rem 0.75rem",
    borderBottom: "1px solid #334155",
    fontSize: "0.75rem",
    color: "#a5a3c2",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  td: {
    padding: "0.5rem 0.75rem",
    fontSize: "0.875rem",
  },
  activityFeed: {
    backgroundColor: "#1a1826",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "1rem",
    maxHeight: "320px",
    overflowY: "auto" as const,
  },
  activityItem: {
    padding: "0.5rem 0",
    borderBottom: "1px solid #1e293b",
  },
  activityHeader: {
    display: "flex",
    gap: "0.75rem",
    fontSize: "0.75rem",
    marginBottom: "0.25rem",
  },
  activityTime: {
    color: "#6b698c",
    minWidth: "3em",
  },
  activityAgent: {
    color: "#38bdf8",
    fontWeight: 600,
  },
  activityAction: {
    color: "#a5a3c2",
  },
  activityDetails: {
    fontSize: "0.875rem",
    color: "#cbd5e1",
  },
  panel: {
    backgroundColor: "#1a1826",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "1rem",
  },
  panelTitle: {
    fontSize: "0.875rem",
    fontWeight: 600,
    color: "#a5a3c2",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "0.75rem",
  },
  goalItem: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
    padding: "0.375rem 0",
    fontSize: "0.875rem",
  },
  goalText: {
    flex: 1,
  },
  goalStatus: {
    fontSize: "0.75rem",
    color: "#6b698c",
    textTransform: "uppercase" as const,
  },
  taskItem: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
    padding: "0.375rem 0",
    fontSize: "0.875rem",
  },
  taskText: {
    flex: 1,
    minWidth: 0,
  },
  taskAgent: {
    color: "#38bdf8",
    fontSize: "0.75rem",
  },
  taskStatus: {
    fontSize: "0.75rem",
    fontWeight: 600,
  },
};

function statusBadgeStyle(color: string): React.CSSProperties {
  return {
    backgroundColor: `${color}20`,
    color,
    border: `1px solid ${color}40`,
    borderRadius: "9999px",
    padding: "0.125rem 0.75rem",
    fontSize: "0.75rem",
    fontWeight: 600,
    textTransform: "uppercase",
  };
}
