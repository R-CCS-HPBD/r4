/**
 * Wave G — tier-1 inline sparklines (one text row per series).
 * ▁▂▃▄▅▆▇█ by normalized value; empty series renders an honest gap.
 */

const TICKS = "▁▂▃▄▅▆▇█";

export function sparkline(values: number[], width?: number): string {
  if (values.length === 0) return "—";
  const capped = width && values.length > width ? values.slice(values.length - width) : values;
  const min = Math.min(...capped);
  const max = Math.max(...capped);
  const span = max - min;
  return capped
    .map((v) => {
      const idx = span === 0 ? 0 : Math.min(TICKS.length - 1, Math.floor(((v - min) / span) * (TICKS.length - 1) + 0.5));
      return TICKS[idx];
    })
    .join("");
}

/** Labelled sparkline as one row `label ▂▄▅ ` (clipped to width). */
export function sparklineRow(label: string, values: number[], width: number): string {
  return `${label} ${sparkline(values, Math.max(4, width - label.length - 1))}`;
}
