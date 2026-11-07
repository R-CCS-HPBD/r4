/**
 * Step-graph drawing (2026-10-02): layout shape pins plus a reading check —
 * tracing the drawn lines from every box must reach exactly the steps that
 * really depend on it (no edge lost, none suggested), over hand-made tricky
 * shapes and a few hundred random DAGs.
 */
import { describe, expect, test } from "bun:test";
import { effectiveSteps, renderDag, stepLayers, type DagStep } from "../src/lib/dag-layout";

const N = 1;
const S = 2;
const E = 4;
const W = 8;
const GLYPH_DIRS: Record<string, number> = {
  "│": N | S, "─": E | W, "┌": S | E, "┐": S | W, "└": N | E, "┘": N | W,
  "├": N | S | E, "┤": N | S | W, "┬": E | W | S, "┴": E | W | N, "┼": N | S | E | W,
};
/** Moves a reader makes: down, or sideways along a bus — never up against the arrows. */
const STEP: Array<[number, number, number, number]> = [
  [S, 0, 1, N], [E, 1, 0, W], [W, -1, 0, E],
];

/**
 * Edges a reader sees: from each box, follow connected line cells down and
 * along buses to the ▼ above a box; a bus jumps a vertical that crosses it
 * ("─│─"), it does not join it.
 */
function readEdges(lines: string[]): Set<string> {
  const grid = lines.map((l) => [...l]);
  const at = (x: number, y: number): string => grid[y]?.[x] ?? " ";
  const boxes: Array<{ name: string; y: number; x0: number; x1: number }> = [];
  lines.forEach((line, y) => {
    for (const m of line.matchAll(/\[ ([^\]]+?) \]/g)) {
      boxes.push({ name: m[1]!.trim(), y, x0: m.index!, x1: m.index! + m[0].length - 1 });
    }
  });
  const boxAt = (x: number, y: number) => boxes.find((b) => b.y === y && x >= b.x0 && x <= b.x1);
  const seen = new Set<string>();
  for (const parent of boxes) {
    const queue: Array<[number, number]> = [];
    const visited = new Set<string>();
    for (let x = parent.x0; x <= parent.x1; x++) {
      if ((GLYPH_DIRS[at(x, parent.y + 1)] ?? 0) & N) queue.push([x, parent.y + 1]);
    }
    while (queue.length) {
      const [x, y] = queue.shift()!;
      const key = `${x},${y}`;
      if (visited.has(key)) continue;
      visited.add(key);
      const glyph = at(x, y);
      if (glyph === "▼") {
        const child = boxAt(x, y + 1);
        if (child) seen.add(`${parent.name}->${child.name}`);
        continue;
      }
      const dirs = GLYPH_DIRS[glyph] ?? 0;
      for (const [dir, dx, dy, back] of STEP) {
        if (!(dirs & dir)) continue;
        let nx = x + dx;
        const ny = y + dy;
        while (dy === 0 && at(nx, ny) === "│") nx += dx; // jump the crossing vertical(s)
        const next = at(nx, ny);
        if (next === "▼" ? dir === S : (GLYPH_DIRS[next] ?? 0) & back) queue.push([nx, ny]);
      }
    }
  }
  return seen;
}

function trueEdges(steps: DagStep[]): Set<string> {
  const layers = stepLayers(steps);
  const out = new Set<string>();
  for (const s of steps) {
    for (const d of s.depends_on ?? []) {
      // Back-edges of a cycle are not drawable top-down (documented).
      if (d !== s.name && (layers.get(d) ?? 0) < (layers.get(s.name) ?? 0)) out.add(`${d}->${s.name}`);
    }
  }
  return out;
}

function expectFaithful(steps: DagStep[]): string[] {
  const { lines } = renderDag(steps, { maxWidth: 1000 });
  expect([...readEdges(lines)].sort()).toEqual([...trueEdges(steps)].sort());
  return lines;
}

const dep = (name: string, ...deps: string[]): DagStep => ({ name, depends_on: deps });

describe("renderDag shapes", () => {
  test("diamond: shared fan-out and fan-in buses, children centred", () => {
    const lines = renderDag(
      [dep("collect"), dep("methods", "collect"), dep("risk", "collect"), dep("merge", "methods", "risk")],
      { maxWidth: 80 },
    ).lines;
    expect(lines).toEqual([
      "      [ collect ]",
      "           │",
      "     ┌─────┴─────┐",
      "     ▼           ▼",
      "[ methods ]   [ risk ]",
      "     │           │",
      "     └─────┬─────┘",
      "           ▼",
      "       [ merge ]",
    ]);
  });

  test("a chain of different label widths is one straight line", () => {
    const lines = renderDag([dep("ingest"), dep("clean_up_everything", "ingest"), dep("go", "clean_up_everything")], {
      maxWidth: 80,
    }).lines;
    const columns = new Set(lines.flatMap((l) => [...l].flatMap((ch, x) => ("│▼".includes(ch) ? [x] : []))));
    expect(columns.size).toBe(1);
  });

  test("a parent that feeds only some children leaves from separate columns", () => {
    // p→r, q→r, q→s: one shared bus would also suggest p→s.
    const lines = expectFaithful([dep("p"), dep("q"), dep("r", "p", "q"), dep("s", "q")]);
    expect(lines.join("\n")).not.toContain("┼");
  });

  test("wide drawings report that they do not fit", () => {
    const steps = Array.from({ length: 8 }, (_, i) => dep(`parallel_branch_${i}`));
    const drawing = renderDag(steps, { maxWidth: 100 });
    expect(drawing.fits).toBe(false);
    expect(renderDag(steps, { maxWidth: 100, labelMax: 6 }).width).toBeLessThan(drawing.width);
  });
});

describe("renderDag never suggests an edge that is not there", () => {
  const cases: Record<string, DagStep[]> = {
    "skip edge": [dep("a"), dep("b", "a"), dep("c", "b"), dep("d", "a", "c")],
    "long skips": [dep("i"), dep("c", "i"), dep("f", "c"), dep("t", "f"), dep("r", "t", "i", "c")],
    "W": [dep("p1"), dep("p2"), dep("p3"), dep("c1", "p1", "p2"), dep("c2", "p2", "p3")],
    "swap": [dep("A"), dep("B"), dep("C"), dep("X", "C", "B"), dep("Y", "A")],
    "partial fan": [dep("src"), dep("aux"), dep("k1", "src"), dep("k2", "src", "aux"), dep("k3", "src")],
    "K33 minus matching": [
      dep("a"), dep("b"), dep("c"),
      dep("x", "b", "c"), dep("y", "a", "c"), dep("z", "a", "b"),
    ],
    "cycle": [dep("a", "b"), dep("b", "a")],
  };
  for (const [name, steps] of Object.entries(cases)) {
    test(name, () => {
      expectFaithful(steps);
    });
  }

  test("random DAGs (mixed label widths and gaps)", () => {
    let seed = 20261002;
    const rand = (n: number): number => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed % n;
    };
    for (let round = 0; round < 600; round++) {
      const count = 2 + rand(9);
      const names = Array.from({ length: count }, (_, i) => `s${i}${"x".repeat(rand(8))}`);
      const steps: DagStep[] = [];
      for (let i = 0; i < count; i++) {
        const deps = new Set<string>();
        const tries = i ? rand(5) : 0;
        for (let k = 0; k < tries; k++) deps.add(names[rand(i)]!);
        steps.push(dep(names[i]!, ...deps));
      }
      const { lines } = renderDag(steps, { maxWidth: 1000, gap: 1 + rand(3) });
      expect([...readEdges(lines)].sort()).toEqual([...trueEdges(steps)].sort());
    }
  });
});

describe("effectiveSteps", () => {
  test("no step declares depends_on: the runner chains declaration order", () => {
    const { steps, linear } = effectiveSteps([{ name: "a" }, { name: "b" }, { name: "c" }]);
    expect(linear).toBe(true);
    expect(steps.map((s) => s.depends_on)).toEqual([[], ["a"], ["b"]]);
  });

  test("any declared depends_on: declared lists are the graph, the rest are roots", () => {
    const { steps, linear } = effectiveSteps([{ name: "a" }, { name: "b", depends_on: [] }, { name: "c", depends_on: ["a"] }]);
    expect(linear).toBe(false);
    expect(steps.map((s) => s.depends_on)).toEqual([[], [], ["a"]]);
  });
});
