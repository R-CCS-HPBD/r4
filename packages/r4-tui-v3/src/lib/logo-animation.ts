/**
 * Logo animation for the title screen — the R4 block letters get a
 * left→right shine sweep and the legacy wave art pulses in a traveling
 * row ripple. The animation decorates the CURRENT logos (lib/splash.ts);
 * no unrelated art is rendered.
 *
 * Pure color generators so tests can assert structure without rendering:
 * same inputs → same colors; different ticks → different colors.
 */

/** Frame tick interval (ms) — ~11fps, same cadence the splash already used. */
export const LOGO_FRAME_MS = 90;

/** Columns off-screen the sweep travels before/after the logo (wrap pad). */
const SWEEP_PAD = 4;
/** Sweep speed in columns per tick. */
const SWEEP_SPEED = 0.5;
/** Shine falloff — columns at this distance from the center get no lift. */
const SWEEP_RADIUS = 2.5;

/** Ripple angular speed (radians per tick) for the legacy wave art. */
const RIPPLE_SPEED = 0.35;
/** Phase offset between adjacent rows — makes the ripple travel downward. */
const RIPPLE_ROW_PHASE = 0.55;
/** Peak lift of the ripple (0..1 mix toward the bright color). */
const RIPPLE_PEAK = 0.85;

/** Linear mix of two #rrggbb colors. t clamps to [0, 1]. */
export function mixHex(a: string, b: string, t: number): string {
  const clamp = Math.max(0, Math.min(1, t));
  const pa = parseHex(a);
  const pb = parseHex(b);
  const ch = pa.map((va, i) => Math.round(va + (pb[i] - va) * clamp));
  return `#${ch.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function parseHex(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  if (!m) return [0, 0, 0];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

/**
 * Per-column colors for the shine sweep across a `width`-wide logo row.
 * A bright band travels left→right and wraps; when the band is off-screen
 * every column is exactly `base`. Columns near the band center mix toward
 * `bright` with a symmetric linear falloff.
 */
export function shineColumnColors(
  width: number,
  tick: number,
  base: string,
  bright: string,
): string[] {
  if (width <= 0) return [];
  const period = width + SWEEP_PAD * 2;
  const pos = ((tick * SWEEP_SPEED) % period + period) % period - SWEEP_PAD;
  const colors: string[] = [];
  for (let x = 0; x < width; x++) {
    const d = Math.abs(x - pos);
    const lift = Math.max(0, 1 - d / SWEEP_RADIUS);
    colors.push(lift === 0 ? base : mixHex(base, bright, lift));
  }
  return colors;
}

/**
 * Color for one row of the legacy wave art at `tick` — a sinusoidal lift
 * with a per-row phase offset, so the pulse travels down the art.
 */
export function rippleRowColor(
  row: number,
  tick: number,
  base: string,
  bright: string,
): string {
  const wave = 0.5 + 0.5 * Math.sin(tick * RIPPLE_SPEED - row * RIPPLE_ROW_PHASE);
  const lift = wave * RIPPLE_PEAK;
  return lift === 0 ? base : mixHex(base, bright, lift);
}
