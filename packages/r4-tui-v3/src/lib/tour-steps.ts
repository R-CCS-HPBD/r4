/**
 * /tour wizard steps — a guided walkthrough where the user builds and
 * completes the example project `demo` end to end. Pure data + predicates,
 * unit-testable without a renderer.
 */

export interface TourStep {
  id: string;
  title: string;
  /** Why this step exists (one sentence, action-first). */
  why: string;
  /** Command handed to the composer (Enter runs it there); "" = glance step. */
  command: string;
  /** When set, the user is expected to type the tail themselves (import path…). */
  commandHint?: string;
}

/**
 * The walkthrough: create → model → import → init → readiness → first query →
 * plan → run → watch → completion. Every command is the REAL slash grammar.
 */
export const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome",
    title: "Welcome — you'll complete a real project by the end",
    why: "This guide walks the four core flows with a concrete example: project `demo`. Nothing runs without your Enter — disclosed commands land in the composer, you fire them.",
    command: "",
  },
  {
    id: "create-project",
    title: "Create the project",
    why: "Projects own a corpus + knowledge graph + sessions. Everything else keys off this id.",
    command: "/project create demo",
  },
  {
    id: "pick-model",
    title: "Pick a model",
    why: "Open the per-source model picker — ● ready rows answer immediately, ◐ provisioning rows start an endpoint for you.",
    command: "/models",
  },
  {
    id: "import-sources",
    title: "Import sources",
    why: "Feed the corpus: files, directories, URLs. Edit the trailing path, then Enter.",
    command: "/project import demo ",
    commandHint: "append your path — e.g. a folder of papers, a repo, a doc directory",
  },
  {
    id: "init-graph",
    title: "Initialize the knowledge graph",
    why: "Draws the corpus into entities + relations — the graph the answers get cited against.",
    command: "/project init demo",
  },
  {
    id: "readiness",
    title: "Check readiness",
    why: "Honest report: what the build knows, what's missing, what a query would find.",
    command: "/project readiness demo",
  },
  {
    id: "first-query",
    title: "Ask the graph a real question",
    // No chat reply carries structured citations yet (no adapter yields a
    // citations chunk), so the step promises no Ctrl+G → k drill (TUI audit r2 #66).
    why: "The verify answer names the graph evidence behind it — check that each claim points at a source you imported.",
    command: "/project verify demo ",
    commandHint: "append your question about the imported corpus",
  },
  {
    id: "plan-run",
    title: "Plan the research loop, then run it",
    why: "The loop iterates: retrieve → think → act → verify. Start with the plan, then let it run (approvals ask once per tool).",
    command: "/project plan demo improve graph coverage",
  },
  {
    id: "watch-work",
    title: "Watch it work — the Work view",
    why: "/work = approvals inbox + local agent runs + remote jobs + workflow executions, one row grammar. r retries a job, c cancels, t triggers a workflow.",
    command: "/project run demo && /work",
  },
  {
    id: "done",
    title: "Completed — `demo` is a live project",
    why: "You created a project, chose a model, imported sources, built its graph, verified an answer against its sources, and set a loop running. /tour map shows the full function index anytime.",
    command: "",
  },
];

export function tourStepCount(): number {
  return TOUR_STEPS.length;
}

export function tourStepAt(index: number): TourStep {
  const clamped = Math.max(0, Math.min(TOUR_STEPS.length - 1, index));
  return TOUR_STEPS[clamped]!;
}

/** Progress bar — filled == steps behind the current one, current == ▸. */
export function tourProgressBar(index: number): string {
  return TOUR_STEPS.map((_, i) => (i < index ? "■" : i === index ? "▶" : "□")).join("");
}
