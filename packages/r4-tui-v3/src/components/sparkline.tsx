/**
 * Unicode block sparkline — compact and axis-free.
 */

import { palette } from "../themes";

const BLOCKS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];

export function Sparkline(props: { data: number[]; height?: number; color?: string }) {
  const min = Math.min(...props.data);
  const max = Math.max(...props.data);
  const range = max - min || 1;
  const line = props.data
    .map((v) => {
      const ratio = (v - min) / range;
      const idx = Math.min(BLOCKS.length - 1, Math.max(0, Math.floor(ratio * (BLOCKS.length - 1))));
      return BLOCKS[idx];
    })
    .join("");

  return <text fg={props.color ?? palette.statusOk}>{line}</text>;
}
