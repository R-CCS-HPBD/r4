/**
 * Live execution DAG pins (2026-10-20): definition topology wins, unreported
 * steps read pending (never fabricated), unwrapped declaration-order chains
 * stay honestly labeled, and an oversized drawing degrades to a one-line
 * fallback instead of a broken picture.
 */
import { describe, it, expect } from "bun:test";
import { buildExecDag } from "../src/lib/workflow-exec-graph";

const FAN_DEF = [
  { name: "collect_context" },
  { name: "methods_review", depends_on: ["collect_context"] },
  { name: "risk_review", depends_on: ["collect_context"] },
  { name: "merge_findings", depends_on: ["methods_review", "risk_review"] },
];

describe("buildExecDag", () => {
  it("draws definition topology with live status glyphs and bucket counts", () => {
    const model = buildExecDag({
      rows: [
        { name: "collect_context", status: "completed" },
        { name: "methods_review", status: "running" },
      ],
      definitionSteps: FAN_DEF,
      width: 90,
    });
    expect(model).not.toBeNull();
    // merge_findings + risk_review not yet reported → pending bucket, truthfully.
    expect(model!.header).toBe("graph (4 steps · ✔ 1 ◐ 1 ○ 2)");
    const drawing = model!.lines.join("\n");
    expect(drawing).toContain("[ ✔ collect_context ]");
    expect(drawing).toContain("[ ◐ methods_review ]");
    expect(drawing).toContain("[ ○ risk_review ]");
    expect(drawing).toContain("[ ○ merge_findings ]");
    // real fan-out/fan-in drawing, not a flat list
    expect(drawing).toContain("▼");
  });

  it("maps waiting_approval and failed into their buckets", () => {
    const model = buildExecDag({
      rows: [
        { name: "run", status: "failed" },
        { name: "gate", status: "waiting_approval" },
      ],
      definitionSteps: [{ name: "run" }, { name: "gate", depends_on: ["run"] }],
      width: 90,
    });
    expect(model!.header).toBe("graph (2 steps · ✖ 1 ◌ 1)");
    expect(model!.lines.join("\n")).toContain("[ ✖ run ]");
    expect(model!.lines.join("\n")).toContain("[ ◌ gate ]");
  });

  it("labels the linear fallback when the definition is unavailable", () => {
    const model = buildExecDag({
      rows: [
        { name: "prep", status: "completed" },
        { name: "train", status: "running" },
      ],
      definitionSteps: null,
      width: 90,
    });
    expect(model!.header).toBe("graph (2 steps · ✔ 1 ◐ 1 · linear (no definition))");
    const drawing = model!.lines.join("\n");
    expect(drawing).toContain("[ ✔ prep ]");
    expect(drawing).toContain("[ ◐ train ]");
  });

  it("labels a declared-without-depends_on definition the same honest way", () => {
    const model = buildExecDag({
      rows: [{ name: "prep", status: "completed" }],
      definitionSteps: [{ name: "prep" }, { name: "train" }],
      width: 90,
    });
    expect(model!.header).toBe("graph (2 steps · ✔ 1 ○ 1 · linear (no depends_on))");
  });

  it("declared topology draws even before any step row lands", () => {
    const model = buildExecDag({ rows: [], definitionSteps: FAN_DEF, width: 90 });
    expect(model!.header).toBe("graph (4 steps · ○ 4)");
    expect(model!.lines.join("\n")).toContain("[ ○ collect_context ]");
  });

  it("falls back honestly when the drawing cannot fit", () => {
    const wide = Array.from({ length: 6 }, (_, i) => ({ name: `root_step_${i}` }));
    const model = buildExecDag({
      rows: wide.map((s) => ({ name: s.name, status: "pending" })),
      definitionSteps: wide.map((s) => ({ name: s.name, depends_on: [] })),
      width: 30,
    });
    expect(model!.lines).toEqual(["too wide for this card — step list below has every step"]);
  });

  it("returns null when there is nothing to draw — no fabricated graph", () => {
    expect(buildExecDag({ rows: [], definitionSteps: null, width: 90 })).toBeNull();
    expect(buildExecDag({ rows: [], definitionSteps: [], width: 90 })).toBeNull();
  });
});
