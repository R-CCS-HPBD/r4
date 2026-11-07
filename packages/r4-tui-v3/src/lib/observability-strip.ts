/**
 * Compact observability summary for idle companion sessions.
 * Ported from companion-tui-js observability-strip.mjs.
 */

export interface AgentTreeSnapshot {
  active_count?: number;
  total_count?: number;
}

export interface ResourceSnapshot {
  cpu?: { percent?: number | null } | null;
  memory?: { percent?: number | null } | null;
  hostname?: string;
}

export function isObservabilityIdle(input: { agentTree?: AgentTreeSnapshot | null }): boolean {
  return (input.agentTree?.active_count || 0) === 0;
}

/** e.g. "agents:2/5 · CPU 31.0% · RAM 64% · data0" */
export function formatObservabilitySummary(input: {
  agentTree?: AgentTreeSnapshot | null;
  resources?: ResourceSnapshot | null;
}): string {
  const parts: string[] = [];
  const activeAgents = input.agentTree?.active_count || 0;
  const totalAgents = input.agentTree?.total_count || 0;
  if (totalAgents > activeAgents) {
    parts.push(`agents:${activeAgents}/${totalAgents}`);
  } else {
    parts.push(`agents:${activeAgents}`);
  }

  // `cpu` / `memory` arrive as null from bridges without host metrics (the
  // pty fixture, psutil-less installs): a destructuring default only covers
  // undefined, so `cpu.percent` threw inside the status bar's render and
  // aborted the chat send that triggered it (w3-tui, 2026-10-21).
  const cpu = input.resources?.cpu ?? {};
  const memory = input.resources?.memory ?? null;
  const hostname = input.resources?.hostname ?? "";
  if (cpu.percent != null) {
    parts.push(`CPU ${cpu.percent.toFixed(1)}%`);
  }
  if (memory?.percent != null) {
    parts.push(`RAM ${memory.percent}%`);
  }
  if (hostname) {
    parts.push(hostname);
  }

  return parts.join(" · ");
}
