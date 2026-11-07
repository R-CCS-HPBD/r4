/**
 * Reduced-motion switch (P4 audit): R4_TUI_STILL=1 freezes decorative
 * animation — spinners pin their first frame, rotating placeholders stop
 * rotating. State-transition motion (job progress, graph deltas) is data,
 * not decoration, and stays.
 * Evaluated ONCE at module load (env read per render would flip mid-frame).
 */

export function motionAllowed(env: Record<string, string | undefined> = process.env): boolean {
  return (env.R4_TUI_STILL ?? "").trim() !== "1";
}

const allowed = motionAllowed();

/** True when decorative animation should run (module-load snapshot). */
export function decorativeMotion(): boolean {
  return allowed;
}
