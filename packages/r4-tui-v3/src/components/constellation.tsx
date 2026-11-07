/**
 * Knowledge constellation — animated braille graph.
 */

import { createMemo, createSignal, onCleanup } from "solid-js";
import Canvas from "drawille";
import { workspace } from "../stores";
import { palette } from "../themes";
import { t, dim } from "@opentui/core";
import { StyledTextView } from "../ui/StyledTextView";

export interface ConstellationNode {
  id: string;
  label: string;
  type: "goal" | "evidence" | "knowledge" | "task";
}

export interface ConstellationEdge {
  from: string;
  to: string;
  label?: string;
}

export function Constellation(props: {
  width: number;
  height: number;
  nodes: ConstellationNode[];
  edges: ConstellationEdge[];
}) {
  const [angle, setAngle] = createSignal(0);
  const timer = setInterval(() => setAngle((a) => (a + 0.03) % (Math.PI * 2)), 120);
  onCleanup(() => clearInterval(timer));

  const W = () => Math.max(8, Math.floor(props.width / 2) * 2);
  const H = () => Math.max(8, Math.floor(props.height / 4) * 4);

  const positions = createMemo(() => {
    const cx = W() / 2;
    const cy = H() / 2;
    const radius = Math.min(cx, cy) * 0.7;
    const count = Math.max(1, props.nodes.length);
    const base = angle();
    return new Map(
      props.nodes.map((n, i) => {
        const a = base + (i / count) * Math.PI * 2;
        return [n.id, { x: Math.round(cx + Math.cos(a) * radius), y: Math.round(cy + Math.sin(a) * radius) }] as const;
      }),
    );
  });

  const frame = createMemo(() => {
    const canvas = new Canvas(W(), H());
    const pos = positions();

    for (const edge of props.edges) {
      const a = pos.get(edge.from);
      const b = pos.get(edge.to);
      if (!a || !b) continue;
      drawLine(canvas, a.x, a.y, b.x, b.y);
    }

    for (const node of props.nodes) {
      const p = pos.get(node.id);
      if (!p) continue;
      drawNode(canvas, p.x, p.y, node.id === workspace.selectedObjectId);
    }

    return canvas.frame();
  });

  const selected = createMemo(() => props.nodes.find((n) => n.id === workspace.selectedObjectId));

  return (
    <box flexDirection="column" gap={1}>
      <text fg={palette.knowledge}>{frame()}</text>
      {/* StyledTextView: reactive StyledText children append per re-render
          (opentui 0.3.4) — content-setter path replaces. */}
      <StyledTextView text={t`${dim("Selected:")} ${selected()?.label ?? "—"}`} />
    </box>
  );
}

function drawLine(canvas: Canvas, x0: number, y0: number, x1: number, y1: number) {
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  let err = dx - dy;
  let x = x0;
  let y = y0;
  while (true) {
    canvas.set(x, y);
    if (x === x1 && y === y1) break;
    const e2 = 2 * err;
    if (e2 > -dy) {
      err -= dy;
      x += sx;
    }
    if (e2 < dx) {
      err += dx;
      y += sy;
    }
  }
}

function drawNode(canvas: Canvas, x: number, y: number, selected: boolean) {
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      canvas.set(x + dx, y + dy);
    }
  }
  if (selected) {
    canvas.set(x - 2, y);
    canvas.set(x + 2, y);
    canvas.set(x, y - 2);
    canvas.set(x, y + 2);
  }
}
