/**
 * Console mode flags — demo/offline vs live CLI backend.
 */

let demoMode = false;
let demoVisuals = false;

/** True when OfflineDataSource (or explicit offline) is active. */
export function isDemoMode(): boolean {
  return demoMode;
}

export function setDemoMode(enabled: boolean): void {
  demoMode = enabled;
}

/** Synthetic/demo panels (mascot, constellation, strategy/blackboard fixtures). */
export function isDemoVisuals(): boolean {
  return demoVisuals;
}

export function setDemoVisuals(enabled: boolean): void {
  demoVisuals = enabled;
}

/** Prefer env, then runtime flag set at boot. */
export function isOfflineBackend(): boolean {
  return process.env["R4_TUI_BACKEND"] === "offline" || demoMode;
}

export function resolveBootFlags(argv: string[] = []): {
  demoVisuals: boolean;
  forceOffline: boolean;
  fresh: boolean;
} {
  const demoVisualsEnabled =
    argv.includes("--demo") ||
    process.env["R4_TUI_DEMO"] === "1" ||
    process.env["R4_TUI_BACKEND"] === "offline";
  const forceOffline = process.env["R4_TUI_BACKEND"] === "offline" || argv.includes("--demo");
  // A1 (2026-10-05): --fresh skips the tab-layout auto-resume.
  const fresh = argv.includes("--fresh");
  return { demoVisuals: demoVisualsEnabled, forceOffline, fresh };
}
