/**
 * Detail-pane chrome title — pure (pinned by tests; 2026-10-06 focus model).
 */

export const PANE_TITLES: Record<string, string> = {
  dashboard: "Overview",
  research: "Research",
  tasks: "Tasks",
  // D2 (core-flows plan): pane id "work" — the unified Work view (local runs
  // + remote jobs + approvals + workflows; slash aliases /work + /workflows).
  work: "Work",
  knowledge: "Knowledge",
  infrastructure: "Infrastructure",
  loop: "Workflow builder (demo)",
  settings: "Settings",
  sessions: "Sessions",
  codeview: "Code view",
  catalog: "Catalog",
  term: "Terminal",
  logs: "Logs",
};

/** Views whose space OWNS Tab for an internal cycle (research focus,
 *  knowledge catalog/verify) — the global Tab focus-toggle (keymap, last
 *  binding) never fires there, so their affordance is the CLICK. */
const TAB_CONSUMING_VIEWS = new Set(["research", "knowledge"]);

export function paneTitle(view: string, paneFocus: boolean): string {
  const base = PANE_TITLES[view] ?? view;
  if (paneFocus) return base;
  if (view === "term") return `${base} · click to focus`;
  if (TAB_CONSUMING_VIEWS.has(view)) return `${base} · click to focus`;
  return `${base} · Tab to focus`;
}
