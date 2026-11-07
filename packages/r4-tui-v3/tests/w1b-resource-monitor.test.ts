import { describe, expect, test } from "bun:test";
import {
  colorFor,
  fmtGpuMem,
  fmtRamGB,
  formatGpuRow,
  formatResourceRows,
  type HostResources,
} from "../src/lib/resource-monitor";

describe("resource-monitor", () => {
  test("colorFor bands", () => {
    expect(colorFor(50)).toBe("green");
    expect(colorFor(80)).toBe("yellow");
    expect(colorFor(97)).toBe("red");
    expect(colorFor(null)).toBe("green");
  });

  test("fmtGpuMem treats input as MiB (nvidia-smi units)", () => {
    expect(fmtGpuMem(null)).toBe("?");
    expect(fmtGpuMem(512)).toBe("512MB");
    expect(fmtGpuMem(12288)).toBe("12GB");
    expect(fmtGpuMem(81920)).toBe("80GB");
  });

  test("fmtRamGB treats input as GB (psutil units)", () => {
    expect(fmtRamGB(undefined)).toBe("?");
    expect(fmtRamGB(31.5)).toBe("31.5GB");
    expect(fmtRamGB(2048)).toBe("2.0TB");
  });

  test("formatGpuRow", () => {
    expect(
      formatGpuRow({ index: 0, utilization: 42, memory_used: 12288, memory_total: 81920, temperature: 61 }),
    ).toBe("GPU0 · 42% · mem 12GB/80GB · 61C");
    expect(formatGpuRow({ index: 1 })).toBe("GPU1");
  });

  test("formatResourceRows composes gpu/cpu/ram/jobs", () => {
    const res: HostResources = {
      gpu: [{ index: 0, utilization: 10 }],
      cpu: { percent: 31, count: 16 },
      memory: { percent: 64, used: 100.0, total: 156.0 },
      slurm_jobs: [{ job_id: "123", name: "train", status: "RUNNING", time: "01:00:00" }],
      hostname: "data0",
    };
    const rows = formatResourceRows(res);
    expect(rows).toEqual([
      "GPU0 · 10%",
      "CPU 31.0% (16 cores)",
      "RAM 64% (100GB/156GB)",
      "job 123 train · RUNNING · 01:00:00",
    ]);
    expect(formatResourceRows(null)).toEqual([]);
  });
});
