import { describe, expect, test } from "bun:test";
import {
  artifactIcon,
  artifactLabel,
  formatArtifactEntries,
  formatKnowledgeSummary,
  formatScienceSummary,
  formatTrajectoryLine,
  knowledgeHasSignal,
  scienceLoopHasSignal,
} from "../src/lib/companion-panels";

describe("artifact entries", () => {
  test("icons by prefix", () => {
    expect(artifactIcon("slurm:12345")).toBe("SBATCH");
    expect(artifactIcon("diff:/x/a.py")).toBe("EDIT");
    expect(artifactIcon("report:r.md")).toBe("RPT");
    expect(artifactIcon("figure:f.png")).toBe("FIG");
    expect(artifactIcon("job:j1")).toBe("JOB");
    expect(artifactIcon("data:d.csv")).toBe("DATA");
    expect(artifactIcon("/plain/file.txt")).toBe("FILE");
  });
  test("labels by prefix", () => {
    expect(artifactLabel("slurm:12345")).toBe("Slurm job 12345");
    expect(artifactLabel("diff:/x/a.py")).toBe("Edited /x/a.py");
    expect(artifactLabel("/plain/file.txt")).toBe("/plain/file.txt");
  });
  test("formatArtifactEntries maps both", () => {
    expect(formatArtifactEntries(["slurm:9"])).toEqual([
      { icon: "SBATCH", label: "Slurm job 9" },
    ]);
  });
});

describe("science loop", () => {
  test("signal detection", () => {
    expect(scienceLoopHasSignal(null)).toBe(false);
    expect(scienceLoopHasSignal({ stats: { total_iterations: 0 }, trajectory: [] })).toBe(false);
    expect(scienceLoopHasSignal({ stats: { total_iterations: 2 } })).toBe(true);
    expect(scienceLoopHasSignal({ trajectory: [{ reward_total: 0.1 }] })).toBe(true);
  });
  test("summary line", () => {
    expect(
      formatScienceSummary({
        total_iterations: 3,
        cumulative_reward: 1.25,
        avg_reward: 0.4167,
        avg_entropy: 0.8123,
      }),
    ).toBe("3 iterations · cum 1.250 · avg 0.417 · H 0.812");
  });
  test("trajectory line trims hypothesis", () => {
    expect(formatTrajectoryLine({ reward_total: 0.5, hypothesis: "try bigger ctx" })).toBe(
      "0.500 · try bigger ctx",
    );
    expect(formatTrajectoryLine({ reward_total: 0.5 })).toBe("0.500");
  });
});

describe("knowledge base", () => {
  test("signal detection", () => {
    expect(knowledgeHasSignal(null)).toBe(false);
    expect(knowledgeHasSignal({ stats: { patterns: 0, anti_patterns: 0, lessons: 0 } })).toBe(false);
    expect(knowledgeHasSignal({ stats: { patterns: 1 } })).toBe(true);
    expect(knowledgeHasSignal({ patterns: [{}] })).toBe(true);
  });
  test("summary line", () => {
    expect(formatKnowledgeSummary({ patterns: 2, anti_patterns: 1, lessons: 3 })).toBe(
      "patterns 2 · anti 1 · lessons 3",
    );
  });
});
