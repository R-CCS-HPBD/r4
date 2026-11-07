/** Short copy for live CLI mode when panels have no backend data yet. */

export const WORKFLOW_EMPTY =
  "No RC1 pipeline yet. /project import → init → plan → run (nodes appear as jobs run).";

export const LOOP_EMPTY =
  "This Workflow builder is a local compositional demo (not RC1). For live research: Mission Control [R]un demo or /project run <id> --simulate --approve";

export const STRATEGY_EMPTY =
  "Strategy needs a plan. Run /project plan <id> <goal> (add knowledge first with /rag upload <file>).";

// K1.3 (2026-10-24): the knowledge hints name verbs that work. `/project
// import → init` dead-ended: init reads a local manifest a durable import
// never writes (review of K1 part B: STRATEGY_EMPTY and READINESS_EMPTY
// pointed there too). (WORKFLOW_EMPTY describes the local RC1 pipeline projection
// and is left to that pane's owner.)
export const BLACKBOARD_EMPTY =
  "Blackboard fills as the project gains knowledge and a plan. Add sources with /rag upload <file>.";

export const TASKS_EMPTY =
  "No tasks yet. Run /project plan <id> <goal> (tasks come from the research plan).";

export const TASKS_HOW_TO =
  "Plan tasks · ↑↓ select · Enter/a ask R4 · r Research · k Knowledge";

export const KNOWLEDGE_EMPTY =
  "No project knowledge yet. Add a file with /rag upload <file> (or r4 knowledge upload); r4 knowledge ls lists it.";

/** K1.3 review: the pane's line when the server list could not be read. */
export function knowledgeListUnavailable(reason: string): string {
  return `knowledge list unavailable: ${reason} (r4 knowledge ls shows the server's answer)`;
}

export const KNOWLEDGE_HOW_TO =
  "T=task  E=experiment  K=catalog file · Tab types a verify question · ↑↓ this list only";

export const COVERAGE_EMPTY =
  "Coverage not in poll yet — run /project plan; panel fills when the backend exposes it.";

export const READINESS_EMPTY =
  "Readiness not in poll yet — /project plan <id> <goal> fills it; r4 knowledge ls lists what the project knows.";

export const PLAN_EMPTY =
  "No plan in poll — /project plan <id> <goal> then wait for job refresh.";

export const LOOP_POLL_EMPTY =
  "No RC1 loop snapshot in poll — start via Research [R]un demo or /project run.";
