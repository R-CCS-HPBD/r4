/**
 * Demo-mode gate (WP-08, G-P3-1).
 *
 * Overview/Report/Artifacts/Timeline render from hard-coded mock rows that
 * have never been wired to the shared Read API. The read-only honesty
 * contract (FR-2 — ./README.md, since 2026-10-19) forbids serving fabricated rows by
 * default, so those views are hidden unless the operator opts into the
 * demo projection explicitly:
 *
 *     VITE_R4_WEB_DEMO=1 bun run dev   (or build with the env set)
 *
 * Everything outside the four mock views (Graph, Companion) is always live.
 */

/** True only when the operator explicitly enabled the demo projection. */
export const DEMO_MODE =
  import.meta.env.VITE_R4_WEB_DEMO === "1" ||
  import.meta.env.VITE_R4_WEB_DEMO === "true";

/** The four views that stay mock-only until the Read API lands (FR-2). */
export const DEMO_ONLY_VIEWS = ["Overview", "Report", "Artifacts", "Timeline"] as const;
