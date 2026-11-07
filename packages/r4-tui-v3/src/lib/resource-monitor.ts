/**
 * Host resource snapshot model + formatting (ported from tui-js resource-monitor.mjs).
 * Data comes from the Companion bridge `get_resources` RPC.
 */

export interface GpuInfo {
  index: number;
  utilization?: number;
  memory_used?: number;
  memory_total?: number;
  temperature?: number;
}

export interface SlurmJobInfo {
  job_id: string;
  status?: string;
  name?: string;
  time?: string;
  partition?: string;
  nodes?: string;
}

export interface HostResources {
  gpu?: GpuInfo[];
  cpu?: { percent?: number | null; count?: number };
  memory?: { total?: number; used?: number; percent?: number } | null;
  slurm_jobs?: SlurmJobInfo[];
  hostname?: string;
}

export type Severity = "green" | "yellow" | "red";

export function colorFor(pct: number | null | undefined, warn = 80, crit = 95): Severity {
  const v = pct ?? 0;
  if (v >= crit) return "red";
  if (v >= warn) return "yellow";
  return "green";
}

/**
 * GPU memory formatter. nvidia-smi reports MiB — NOT the same unit as RAM
 * (GB, see Python resource_monitor._get_memory_usage). tui-js labeled both
 * through one formatter and mislabeled GPU rows as GB/TB; keep units explicit.
 */
export function fmtGpuMem(mib: number | null | undefined): string {
  if (mib == null) return "?";
  if (mib >= 1048576) return `${(mib / 1048576).toFixed(1)}TB`;
  if (mib >= 1024) return `${Math.round(mib / 1024)}GB`;
  return `${Math.round(mib)}MB`;
}

/** RAM formatter: Python reports GB already. */
export function fmtRamGB(gb: number | null | undefined): string {
  if (gb == null) return "?";
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)}TB`;
  return `${gb}GB`;
}

/** One GPU row, e.g. "GPU0 42% · mem 12GB/80GB · 61C". */
export function formatGpuRow(gpu: GpuInfo): string {
  const parts = [`GPU${gpu.index}`];
  if (gpu.utilization != null) parts.push(`${Math.round(gpu.utilization)}%`);
  if (gpu.memory_used != null) {
    const total = gpu.memory_total != null ? `/${fmtGpuMem(gpu.memory_total)}` : "";
    parts.push(`mem ${fmtGpuMem(gpu.memory_used)}${total}`);
  }
  if (gpu.temperature != null) parts.push(`${Math.round(gpu.temperature)}C`);
  return parts.join(" · ");
}

/** Compact rows for the resources view (empty array → nothing to show). */
export function formatResourceRows(resources: HostResources | null | undefined): string[] {
  if (!resources) return [];
  const rows: string[] = [];
  for (const gpu of resources.gpu ?? []) {
    rows.push(formatGpuRow(gpu));
  }
  if (resources.cpu?.percent != null) {
    const count = resources.cpu.count != null ? ` (${resources.cpu.count} cores)` : "";
    rows.push(`CPU ${resources.cpu.percent.toFixed(1)}%${count}`);
  }
  if (resources.memory?.percent != null) {
    const used = resources.memory.used != null ? fmtRamGB(resources.memory.used) : "?";
    const total = resources.memory.total != null ? fmtRamGB(resources.memory.total) : "?";
    rows.push(`RAM ${resources.memory.percent}% (${used}/${total})`);
  }
  for (const job of resources.slurm_jobs ?? []) {
    const bits = [job.status ?? "", job.time ?? "", job.partition ?? ""].filter(Boolean);
    rows.push(`job ${job.job_id} ${job.name ?? ""}${bits.length ? ` · ${bits.join(" · ")}` : ""}`.trim());
  }
  return rows;
}
