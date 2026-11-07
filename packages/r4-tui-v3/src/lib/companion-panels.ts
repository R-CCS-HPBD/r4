/**
 * Formatting + signal helpers for the companion research panels
 * (artifacts, science loop, knowledge base).
 * Ported from tui-js artifact-panel.mjs / science-panel.mjs / research-rail-logic.mjs.
 */

// ─── artifacts ───────────────────────────────────────────────────────────────

export interface ArtifactEntry {
  icon: string;
  label: string;
}

export function artifactIcon(artifact: string): string {
  if (artifact.startsWith("slurm:")) return "SBATCH";
  if (artifact.startsWith("diff:")) return "EDIT";
  if (artifact.startsWith("report:")) return "RPT";
  if (artifact.startsWith("figure:")) return "FIG";
  if (artifact.startsWith("job:")) return "JOB";
  if (artifact.startsWith("data:")) return "DATA";
  return "FILE";
}

export function artifactLabel(artifact: string): string {
  const colon = artifact.indexOf(":");
  if (colon > 0) {
    const kind = artifact.slice(0, colon);
    const rest = artifact.slice(colon + 1);
    switch (kind) {
      case "slurm":
        return `Slurm job ${rest}`;
      case "diff":
        return `Edited ${rest}`;
      case "report":
        return `Report ${rest}`;
      case "figure":
        return `Figure ${rest}`;
      case "job":
        return `Job ${rest}`;
      case "data":
        return `Dataset ${rest}`;
    }
  }
  return artifact;
}

export function formatArtifactEntries(artifacts: string[]): ArtifactEntry[] {
  return (artifacts ?? []).map((a) => ({ icon: artifactIcon(a), label: artifactLabel(a) }));
}

// ─── science loop ────────────────────────────────────────────────────────────

export interface ScienceStats {
  total_iterations?: number;
  cumulative_reward?: number;
  avg_reward?: number;
  avg_entropy?: number;
}

export interface ScienceIteration {
  reward_total?: number;
  hypothesis?: string;
}

export interface ScienceLoopState {
  stats?: ScienceStats;
  trajectory?: ScienceIteration[];
}

export function scienceLoopHasSignal(loop: ScienceLoopState | null | undefined): boolean {
  if (!loop) return false;
  const stats = loop.stats ?? {};
  const trajectory = loop.trajectory ?? [];
  return (stats.total_iterations ?? 0) > 0 || trajectory.length > 0;
}

/** "3 · cum 1.250 · avg 0.417 · H 0.812" — one-line science loop summary. */
export function formatScienceSummary(stats: ScienceStats): string {
  const parts = [`${stats.total_iterations ?? 0} iterations`];
  parts.push(`cum ${(stats.cumulative_reward ?? 0).toFixed(3)}`);
  parts.push(`avg ${(stats.avg_reward ?? 0).toFixed(3)}`);
  parts.push(`H ${(stats.avg_entropy ?? 0).toFixed(3)}`);
  return parts.join(" · ");
}

/** "0.813 · try a longer context window" — one trajectory line. */
export function formatTrajectoryLine(iter: ScienceIteration): string {
  const reward = (iter.reward_total ?? 0).toFixed(3);
  const hyp = String(iter.hypothesis ?? "").slice(0, 40);
  return hyp ? `${reward} · ${hyp}` : reward;
}

// ─── knowledge base ──────────────────────────────────────────────────────────

export interface KnowledgeStats {
  patterns?: number;
  anti_patterns?: number;
  lessons?: number;
}

export interface KnowledgeBaseState {
  stats?: KnowledgeStats;
  patterns?: unknown[];
  lessons?: unknown[];
}

export function knowledgeHasSignal(kb: KnowledgeBaseState | null | undefined): boolean {
  if (!kb) return false;
  const stats = kb.stats ?? {};
  const counted = (stats.patterns ?? 0) + (stats.anti_patterns ?? 0) + (stats.lessons ?? 0);
  return counted > 0 || (kb.patterns?.length ?? 0) > 0 || (kb.lessons?.length ?? 0) > 0;
}

/** "knowledge 2p/1a/3l" — compact knowledge counts line. */
export function formatKnowledgeSummary(stats: KnowledgeStats): string {
  const p = stats.patterns ?? 0;
  const a = stats.anti_patterns ?? 0;
  const l = stats.lessons ?? 0;
  return `patterns ${p} · anti ${a} · lessons ${l}`;
}
