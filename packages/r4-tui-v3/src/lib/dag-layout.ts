/**
 * Layered box-and-arrow drawing of a workflow's step DAG (2026-10-02).
 *
 *            [ collect_context ]
 *                     │
 *            ┌────────┴──────────┐
 *            ▼                   ▼
 *    [ methods_review ]   [ risk_review ]
 *            │                   │
 *            └────────┬──────────┘
 *                     ▼
 *             [ merge_findings ]
 *
 * Layers = longest dependency chain; an edge that skips layers runs through
 * a pass-through column in every layer between; nodes in a layer are ordered
 * by their parents' positions. Between two layers, parents that all feed the
 * same children share one bus (that is exactly what a shared line says); any
 * other connected set gets one bus per child, a parent feeding several of
 * those leaves from separate columns of its box, and buses fill rows so that
 * none touch. Lines of one connection merge into junctions; where two
 * connections cross, the vertical is drawn whole and the bus jumps it ("─│─"),
 * so a junction always means a real edge. Pure text — the card renders one
 * `<text>` per line and falls back to the step list when it is too wide.
 */

export interface DagStep {
  name: string;
  depends_on?: string[] | null;
}

/**
 * The edges the runner executes (umrs `DAGScheduler`): when no step declares
 * `depends_on`, every step waits for the one declared before it; otherwise
 * the declared lists are the graph and a step without one is a root.
 */
export function effectiveSteps(steps: DagStep[]): { steps: DagStep[]; linear: boolean } {
  if (steps.some((s) => Array.isArray(s.depends_on))) {
    return { steps: steps.map((s) => ({ name: s.name, depends_on: s.depends_on ?? [] })), linear: false };
  }
  return {
    steps: steps.map((s, i) => ({ name: s.name, depends_on: i === 0 ? [] : [steps[i - 1]!.name] })),
    linear: steps.length > 1,
  };
}

interface LayoutNode {
  id: string;
  label: string;
  virtual: boolean;
  layer: number;
  x: number; // left column
  w: number; // width in cells
  center: number;
}

interface Edge {
  from: string;
  to: string;
}

const N = 1;
const S = 2;
const E = 4;
const W = 8;
const JUNCTION: Record<number, string> = {
  [N | S]: "│", [E | W]: "─", [N]: "│", [S]: "│", [E]: "─", [W]: "─",
  [S | E]: "┌", [S | W]: "┐", [N | E]: "└", [N | W]: "┘",
  [N | S | E]: "├", [N | S | W]: "┤", [E | W | S]: "┬", [E | W | N]: "┴",
  [N | S | E | W]: "┼",
};

/** Longest-chain depth per step (cycles and unknown deps count as roots). */
export function stepLayers(steps: DagStep[]): Map<string, number> {
  const byName = new Map(steps.map((s) => [s.name, s]));
  const memo = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (name: string): number => {
    const known = memo.get(name);
    if (known !== undefined) return known;
    if (visiting.has(name)) return 0;
    const deps = (byName.get(name)?.depends_on ?? []).filter((d) => byName.has(d));
    if (deps.length === 0) {
      memo.set(name, 0);
      return 0;
    }
    visiting.add(name);
    const d = 1 + Math.max(...deps.map(depth));
    visiting.delete(name);
    memo.set(name, d);
    return d;
  };
  for (const s of steps) depth(s.name);
  // Renumber so layers are consecutive (a cycle can leave a depth unused).
  const used = [...new Set(memo.values())].sort((a, b) => a - b);
  const rank = new Map(used.map((d, i) => [d, i]));
  for (const [name, d] of memo) memo.set(name, rank.get(d)!);
  return memo;
}

function truncateLabel(name: string, max: number): string {
  return name.length <= max ? name : `${name.slice(0, Math.max(1, max - 1))}…`;
}

/** Real nodes + pass-through nodes for edges that skip layers. `labelFor`
 * decorates a real node's label (e.g. a live status glyph); edges still key
 * on the raw step name. */
function buildGraph(steps: DagStep[], labelMax: number, labelFor?: (name: string) => string) {
  const layers = stepLayers(steps);
  const known = new Set(steps.map((s) => s.name));
  const nodes = new Map<string, LayoutNode>();
  const blank = (id: string, label: string, virtual: boolean, layer: number): LayoutNode => ({
    id, label, virtual, layer, x: 0, w: virtual ? 1 : label.length, center: 0,
  });
  for (const s of steps) {
    const text = labelFor ? labelFor(s.name) : s.name;
    nodes.set(s.name, blank(s.name, `[ ${truncateLabel(text, labelMax)} ]`, false, layers.get(s.name) ?? 0));
  }
  const edges: Edge[] = [];
  for (const s of steps) {
    for (const dep of new Set(s.depends_on ?? [])) {
      if (!known.has(dep) || dep === s.name) continue;
      const from = layers.get(dep) ?? 0;
      const to = layers.get(s.name) ?? 0;
      if (to <= from) continue; // cycle back-edge: not drawable top-down
      let prev = dep;
      for (let layer = from + 1; layer < to; layer++) {
        const id = `${dep}→${s.name}@${layer}`;
        nodes.set(id, blank(id, "│", true, layer));
        edges.push({ from: prev, to: id });
        prev = id;
      }
      edges.push({ from: prev, to: s.name });
    }
  }
  // A box needs a column per exit, two apart, when its children are drawn
  // on separate buses: widen a short label that feeds many.
  const outDegree = new Map<string, number>();
  for (const e of edges) outDegree.set(e.from, (outDegree.get(e.from) ?? 0) + 1);
  for (const n of nodes.values()) {
    const need = 2 * (outDegree.get(n.id) ?? 0) - 1;
    if (n.virtual || need <= n.w) continue;
    const name = n.label.slice(2, -2);
    const pad = need - n.w;
    n.label = `[ ${" ".repeat(Math.floor(pad / 2))}${name}${" ".repeat(Math.ceil(pad / 2))} ]`;
    n.w = n.label.length;
  }
  return { nodes, edges };
}

/** Rows of node ids, ordered by the mean position of each node's parents. */
function orderLayers(nodes: Map<string, LayoutNode>, edges: Edge[], steps: DagStep[]): string[][] {
  const depth = Math.max(0, ...[...nodes.values()].map((n) => n.layer));
  const rows: string[][] = Array.from({ length: depth + 1 }, () => []);
  const stepIndex = new Map(steps.map((s, i) => [s.name, i]));
  const initial = [...nodes.values()].sort(
    (a, b) => (stepIndex.get(a.id) ?? Infinity) - (stepIndex.get(b.id) ?? Infinity),
  );
  for (const n of initial) rows[n.layer]!.push(n.id);
  const parents = new Map<string, string[]>();
  for (const e of edges) parents.set(e.to, [...(parents.get(e.to) ?? []), e.from]);
  for (let layer = 1; layer <= depth; layer++) {
    const above = new Map(rows[layer - 1]!.map((id, i) => [id, i]));
    const keyOf = (id: string, fallback: number): number => {
      const ps = (parents.get(id) ?? []).map((p) => above.get(p)).filter((i): i is number => i !== undefined);
      return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : fallback;
    };
    rows[layer] = rows[layer]!
      .map((id, i) => ({ id, key: keyOf(id, i) }))
      .sort((a, b) => a.key - b.key)
      .map((r) => r.id);
  }
  return rows;
}

/**
 * Isotonic least squares (pool adjacent violators): the non-decreasing
 * sequence closest to `targets`.
 */
function isotonic(targets: number[]): number[] {
  const blocks: Array<{ sum: number; n: number }> = [];
  for (const t of targets) {
    blocks.push({ sum: t, n: 1 });
    while (blocks.length > 1) {
      const last = blocks[blocks.length - 1]!;
      const prev = blocks[blocks.length - 2]!;
      if (prev.sum / prev.n <= last.sum / last.n) break;
      prev.sum += last.sum;
      prev.n += last.n;
      blocks.pop();
    }
  }
  return blocks.flatMap((b) => Array<number>(b.n).fill(b.sum / b.n));
}

/**
 * Assign x positions top-down: the first layer packs left to right; every
 * later node wants its centre under the mean of its parents' centres, and
 * the layer takes the positions closest to those wants that keep its order
 * and `gap` columns between boxes. A chain — pass-through columns included —
 * stays one straight line and a fan-out centres under its parent. Returns
 * the drawing width.
 */
function placeColumns(rows: string[][], nodes: Map<string, LayoutNode>, edges: Edge[], gap: number): number {
  const parents = new Map<string, LayoutNode[]>();
  for (const e of edges) parents.set(e.to, [...(parents.get(e.to) ?? []), nodes.get(e.from)!]);
  for (const ids of rows) {
    const layerNodes = ids.map((id) => nodes.get(id)!);
    const offsets: number[] = [];
    let offset = 0;
    for (const n of layerNodes) {
      offsets.push(offset);
      offset += n.w + gap;
    }
    // Wants in "offset space": left edge minus the packed offset, so the
    // order/gap constraint becomes a plain non-decreasing one.
    const wants = layerNodes.map((n, i) => {
      const ps = (parents.get(n.id) ?? []).filter((p) => p.layer === n.layer - 1);
      if (!ps.length) return null;
      const centre = ps.reduce((sum, p) => sum + p.center, 0) / ps.length;
      return centre - Math.floor((n.w - 1) / 2) - offsets[i]!;
    });
    const known = wants.filter((w): w is number => w !== null);
    // A node without a parent in the layer above sticks to its neighbours.
    const filled = wants.map((w, i) => w ?? wants.slice(0, i).reverse().find((v) => v !== null) ?? known[0] ?? 0);
    isotonic(filled).forEach((y, i) => {
      const n = layerNodes[i]!;
      n.x = Math.round(y) + offsets[i]!;
      n.center = n.x + Math.floor((n.w - 1) / 2);
    });
  }
  const all = [...nodes.values()];
  const shift = -Math.min(0, ...all.map((n) => n.x));
  for (const n of all) {
    n.x += shift;
    n.center += shift;
  }
  return Math.max(1, ...all.map((n) => n.x + n.w));
}

/** Edges of one gap grouped into connected components (shared parent or child). */
function edgeGroups(gapEdges: Edge[]): Edge[][] {
  const root = new Map<string, string>();
  const find = (x: string): string => {
    let r = x;
    while (root.get(r) !== r) r = root.get(r)!;
    root.set(x, r);
    return r;
  };
  for (const e of gapEdges) {
    for (const id of [`u:${e.from}`, `l:${e.to}`]) if (!root.has(id)) root.set(id, id);
    root.set(find(`u:${e.from}`), find(`l:${e.to}`));
  }
  const groups = new Map<string, Edge[]>();
  for (const e of gapEdges) {
    const key = find(`u:${e.from}`);
    groups.set(key, [...(groups.get(key) ?? []), e]);
  }
  return [...groups.values()];
}

/** One drawn connection in a gap: parents → children over one bus row, or a straight drop. */
interface Net {
  id: number;
  parents: LayoutNode[];
  children: LayoutNode[];
  /** Exit column on each parent (parent id → column). */
  ports: Map<string, number>;
  /** Entry column on each child (child id → column; its centre unless taken). */
  drops: Map<string, number>;
  lo: number;
  hi: number;
  /** Bus row within the gap; undefined = straight drop (one column). */
  row?: number;
}

/**
 * A group in which every parent feeds every child shares one bus — drawing it
 * shared is the truth. Any other group gets one bus per child, so a line
 * never suggests an edge that does not exist.
 */
function gapNets(groups: Edge[][], nodes: Map<string, LayoutNode>): Net[] {
  const nets: Net[] = [];
  const add = (from: string[], to: string[]): void => {
    nets.push({
      id: nets.length + 1,
      parents: from.map((id) => nodes.get(id)!),
      children: to.map((id) => nodes.get(id)!),
      ports: new Map(),
      drops: new Map(to.map((id) => [id, nodes.get(id)!.center])),
      lo: 0,
      hi: 0,
    });
  };
  for (const group of groups) {
    const ups = [...new Set(group.map((e) => e.from))];
    const lows = [...new Set(group.map((e) => e.to))];
    if (group.length === ups.length * lows.length) add(ups, lows);
    else for (const low of lows) add(group.filter((e) => e.to === low).map((e) => e.from), [low]);
  }
  return nets;
}

/** Nearest free column of `p`'s box to `want` (ties toward `toward`), 2 apart if possible. */
function pickColumn(
  p: LayoutNode,
  want: number,
  toward: number,
  taken: number[],
  blocked: (x: number) => boolean,
): number {
  const target = Math.min(p.x + p.w - 1, Math.max(p.x, want));
  const cols = Array.from({ length: p.w }, (_, i) => p.x + i).sort(
    (a, b) => Math.abs(a - target) - Math.abs(b - target) || Math.abs(a - toward) - Math.abs(b - toward),
  );
  for (const spacing of [2, 1]) {
    const hit = cols.find((x) => !blocked(x) && taken.every((t) => Math.abs(t - x) >= spacing));
    if (hit !== undefined) return hit;
  }
  return cols.find((x) => !taken.includes(x)) ?? target; // a 1-cell pass-through: share
}

/**
 * Columns from `allowed` (ascending), one per want and in the wants' order,
 * minimising the total distance to the wants with at least `gap` between
 * neighbours (dynamic programme; null when they do not fit).
 */
function orderedColumns(allowed: number[], wants: number[], gap: number): number[] | null {
  const k = wants.length;
  const m = allowed.length;
  if (k === 0) return [];
  if (m < k) return null;
  const cost: number[][] = wants.map(() => new Array<number>(m).fill(Infinity));
  const from: number[][] = wants.map(() => new Array<number>(m).fill(-1));
  for (let j = 0; j < m; j++) cost[0]![j] = Math.abs(allowed[j]! - wants[0]!);
  for (let i = 1; i < k; i++) {
    for (let j = 0; j < m; j++) {
      for (let jp = 0; jp < j; jp++) {
        if (allowed[j]! - allowed[jp]! < gap || cost[i - 1]![jp] === Infinity) continue;
        const c = cost[i - 1]![jp]! + Math.abs(allowed[j]! - wants[i]!);
        if (c < cost[i]![j]!) {
          cost[i]![j] = c;
          from[i]![j] = jp;
        }
      }
    }
  }
  let j = cost[k - 1]!.reduce((best, c, idx) => (c < cost[k - 1]![best]! ? idx : best), 0);
  if (cost[k - 1]![j] === Infinity) return null;
  const out = new Array<number>(k);
  for (let i = k - 1; i >= 0; i--) {
    out[i] = allowed[j]!;
    j = from[i]![j]!;
  }
  return out;
}

const aim = (net: Net): number => {
  const xs = [...net.drops.values()];
  return xs.reduce((sum, x) => sum + x, 0) / xs.length;
};

/** Exit columns for a parent feeding several nets (see assignPorts). */
function spreadPorts(p: LayoutNode, own: Net[], blocked: (net: Net, x: number) => boolean): Map<Net, number> {
  const box = Array.from({ length: p.w }, (_, i) => p.x + i);
  const placed = new Map<Net, number>();
  // A net whose child line enters right below this box leaves straight above it.
  for (const net of own) {
    const below = [...net.drops.values()].find((x) => box.includes(x) && ![...placed.values()].includes(x));
    if (below !== undefined) placed.set(net, below);
  }
  const rest = own.filter((n) => !placed.has(n)).sort((a, b) => aim(a) - aim(b));
  const fixed = [...placed.values()];
  for (const gap of [2, 1]) {
    const allowed = box.filter((x) => fixed.every((f) => Math.abs(f - x) >= gap));
    const free = allowed.filter((x) => rest.every((net) => !blocked(net, x)));
    const cols = orderedColumns(free, rest.map(aim), gap) ?? orderedColumns(allowed, rest.map(aim), gap);
    if (cols) {
      rest.forEach((net, i) => placed.set(net, cols[i]!));
      return placed;
    }
  }
  // A box narrower than its exits (a 1-cell pass-through never is): share.
  for (const net of rest) placed.set(net, p.center);
  return placed;
}

/**
 * Exit columns: a parent feeding one net leaves from its centre; one feeding
 * several leaves straight above any child that sits right below it and from
 * separate columns, in its children's left-to-right order, for the rest. A
 * column where another net's line enters its child is avoided, so two
 * connections never share a vertical stretch.
 */
function assignPorts(nets: Net[]): void {
  const dropOwner = new Map<number, Net>();
  for (const net of nets) for (const x of net.drops.values()) dropOwner.set(x, net);
  const blocked = (net: Net, x: number): boolean => (dropOwner.get(x) ?? net) !== net;
  const feeds = new Map<LayoutNode, Net[]>();
  for (const net of nets) for (const p of net.parents) feeds.set(p, [...(feeds.get(p) ?? []), net]);
  for (const [p, own] of feeds) {
    if (own.length === 1) {
      const net = own[0]!;
      net.ports.set(p.id, pickColumn(p, p.center, aim(net), [], (x) => blocked(net, x)));
      continue;
    }
    for (const [net, col] of spreadPorts(p, own, blocked)) net.ports.set(p.id, col);
  }
}

/**
 * Entry columns: a child whose centre is another net's exit column (a 1-cell
 * pass-through cannot move off it) takes the nearest free column of its own
 * box instead; then every net's bus span is fixed.
 */
function settleDrops(nets: Net[], nodes: Map<string, LayoutNode>): void {
  const portOwner = new Map<number, Net>();
  for (const net of nets) for (const x of net.ports.values()) portOwner.set(x, net);
  for (const net of nets) {
    for (const [id, x] of net.drops) {
      if ((portOwner.get(x) ?? net) === net) continue;
      const c = nodes.get(id)!;
      const free = Array.from({ length: c.w }, (_, i) => c.x + i)
        .filter((col) => (portOwner.get(col) ?? net) === net)
        .sort((a, b) => Math.abs(a - x) - Math.abs(b - x));
      if (free.length) net.drops.set(id, free[0]!);
    }
  }
  for (const net of nets) {
    const xs = [...net.ports.values(), ...net.drops.values()];
    net.lo = Math.min(...xs);
    net.hi = Math.max(...xs);
  }
}

/** For each net, the nets that must run above it (they leave from a column it drops into). */
function rowConstraints(bused: Net[]): Map<Net, Net[]> {
  const mustBeBelow = new Map<Net, Net[]>();
  for (const a of bused) {
    for (const b of bused) {
      if (a === b) continue;
      const drops = new Set(b.drops.values());
      if ([...a.ports.values()].some((x) => drops.has(x))) mustBeBelow.set(b, [...(mustBeBelow.get(b) ?? []), a]);
    }
  }
  return mustBeBelow;
}

/** Greedy rows in `order`: first row below placed upper neighbours where the bus touches no other. */
function packRows(order: Net[], mustBeBelow: Map<Net, Net[]>): Map<Net, number> {
  const rows: Net[][] = [];
  const at = new Map<Net, number>();
  for (const net of order) {
    let row = Math.max(0, ...(mustBeBelow.get(net) ?? []).map((m) => (at.get(m) ?? -1) + 1));
    while ((rows[row] ?? []).some((m) => !(m.hi + 1 < net.lo || net.hi + 1 < m.lo))) row++;
    (rows[row] ??= []).push(net);
    at.set(net, row);
  }
  return at;
}

/** Crossings of a row assignment (+100 per broken must-be-above). */
function rowCost(at: Map<Net, number>, nets: Net[], mustBeBelow: Map<Net, Net[]>): number {
  let cost = 0;
  for (const [b, rb] of at) {
    for (const a of nets) {
      if (a === b) continue;
      const ra = at.get(a); // undefined = straight drop through every row
      const within = (x: number): boolean => x >= b.lo && x <= b.hi;
      for (const x of a.ports.values()) if (within(x) && (ra === undefined || rb < ra)) cost++;
      if (ra !== undefined) for (const x of a.drops.values()) if (within(x) && rb > ra) cost++;
    }
    for (const a of mustBeBelow.get(b) ?? []) if ((at.get(a) ?? -1) >= rb) cost += 100;
  }
  return cost;
}

/** Left-to-right order in which every net comes after the nets that must run above it. */
function constraintOrder(bused: Net[], mustBeBelow: Map<Net, Net[]>): Net[] {
  const pending = [...bused];
  const order: Net[] = [];
  while (pending.length) {
    const ready = pending.findIndex((n) => (mustBeBelow.get(n) ?? []).every((m) => order.includes(m)));
    order.push(pending.splice(Math.max(0, ready), 1)[0]!); // a cycle falls back to left-to-right
  }
  return order;
}

function permutations<T>(items: T[]): T[][] {
  if (items.length <= 1) return [items];
  return items.flatMap((item, i) => permutations([...items.slice(0, i), ...items.slice(i + 1)]).map((rest) => [item, ...rest]));
}

/** Most bus rows searched exhaustively for the fewest crossings (6! orders). */
const ROW_SEARCH_MAX = 6;

/**
 * Bus rows: a net leaving from a column where another net drops runs above
 * it; among the orders that respect that, the one with the fewest crossings
 * (then the fewest rows) wins. Returns the number of bus rows.
 */
function assignRows(nets: Net[]): number {
  const bused = nets.filter((n) => n.lo !== n.hi).sort((a, b) => a.lo - b.lo || a.id - b.id);
  const mustBeBelow = rowConstraints(bused);
  const orders = bused.length <= ROW_SEARCH_MAX ? permutations(bused) : [constraintOrder(bused, mustBeBelow)];
  let best: { at: Map<Net, number>; cost: number; height: number } | null = null;
  for (const order of orders) {
    const at = packRows(order, mustBeBelow);
    const height = at.size ? Math.max(...at.values()) + 1 : 0;
    const cost = rowCost(at, nets, mustBeBelow);
    if (!best || cost < best.cost || (cost === best.cost && height < best.height)) best = { at, cost, height };
  }
  for (const [net, row] of best?.at ?? []) net.row = row;
  return best?.height ?? 0;
}

/** Cell lines keyed by connection: one connection merges, two only cross. */
class Canvas {
  private cells: Map<number, number>[][] = [];
  private glyphs: (string | null)[][] = [];
  constructor(private width: number) {}
  private ensure(y: number): void {
    while (this.cells.length <= y) {
      this.cells.push(Array.from({ length: this.width }, () => new Map<number, number>()));
      this.glyphs.push(new Array(this.width).fill(null));
    }
  }
  link(x: number, y: number, dir: number, net: number): void {
    this.ensure(y);
    const cell = this.cells[y]![x]!;
    cell.set(net, (cell.get(net) ?? 0) | dir);
  }
  vline(x: number, y0: number, y1: number, openTop: boolean, openBottom: boolean, net: number): void {
    for (let y = y0; y <= y1; y++) {
      if (y > y0 || openTop) this.link(x, y, N, net);
      if (y < y1 || openBottom) this.link(x, y, S, net);
    }
  }
  hline(y: number, x0: number, x1: number, net: number): void {
    for (let x = Math.min(x0, x1); x <= Math.max(x0, x1); x++) {
      if (x > Math.min(x0, x1)) this.link(x, y, W, net);
      if (x < Math.max(x0, x1)) this.link(x, y, E, net);
    }
  }
  put(x: number, y: number, text: string): void {
    this.ensure(y);
    for (let i = 0; i < text.length && x + i < this.width; i++) this.glyphs[y]![x + i] = text[i]!;
  }
  lines(): string[] {
    return this.cells.map((row, y) => row.map((cell, x) => this.glyphs[y]![x] ?? cellGlyph(cell)).join("").trimEnd());
  }
}

function cellGlyph(cell: Map<number, number>): string {
  if (cell.size === 0) return " ";
  if (cell.size === 1) return JUNCTION[[...cell.values()][0]!] ?? "┼";
  // Two connections cross: the vertical stays whole, the bus jumps it.
  return [...cell.values()].some((bits) => bits & (N | S)) ? "│" : "─";
}

/** Net id for the pass-through cells drawn on layer rows. */
const LAYER_NET = 0;

/** Draw edges between layer `i` (row y) and layer `i + 1`; returns the next layer's row. */
function drawGap(
  canvas: Canvas,
  y: number,
  upper: string[],
  nodes: Map<string, LayoutNode>,
  edges: Edge[],
): number {
  const upperSet = new Set(upper);
  const nets = gapNets(edgeGroups(edges.filter((e) => upperSet.has(e.from))), nodes);
  assignPorts(nets);
  settleDrops(nets, nodes);
  const arrowY = y + 2 + assignRows(nets);
  for (const net of nets) {
    if (net.row === undefined) {
      canvas.vline(net.lo, y + 1, arrowY, true, true, net.id);
    } else {
      const busY = y + 2 + net.row;
      for (const x of net.ports.values()) canvas.vline(x, y + 1, busY, true, false, net.id);
      canvas.hline(busY, net.lo, net.hi, net.id);
      for (const x of net.drops.values()) canvas.vline(x, busY, arrowY, false, true, net.id);
    }
    for (const c of net.children) if (!c.virtual) canvas.put(net.drops.get(c.id)!, arrowY, "▼");
  }
  return arrowY + 1;
}

export interface DagDrawing {
  lines: string[];
  width: number;
  fits: boolean;
}

/** Draw `steps` as layered boxes and arrows within `maxWidth` columns. */
export function renderDag(
  steps: DagStep[],
  opts: { maxWidth: number; labelMax?: number; gap?: number; labelFor?: (name: string) => string },
): DagDrawing {
  if (steps.length === 0) return { lines: [], width: 0, fits: true };
  const { nodes, edges } = buildGraph(steps, opts.labelMax ?? 28, opts.labelFor);
  const rows = orderLayers(nodes, edges, steps);
  const width = placeColumns(rows, nodes, edges, opts.gap ?? 3);
  const canvas = new Canvas(width);
  let y = 0;
  rows.forEach((ids, layer) => {
    for (const id of ids) {
      const n = nodes.get(id)!;
      if (n.virtual) canvas.vline(n.center, y, y, true, true, LAYER_NET);
      else canvas.put(n.x, y, n.label);
    }
    if (layer < rows.length - 1) y = drawGap(canvas, y, ids, nodes, edges);
  });
  return { lines: canvas.lines(), width, fits: width <= opts.maxWidth };
}
