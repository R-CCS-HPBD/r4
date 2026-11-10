/**
 * ADR-104 D-1: one mode, one badge. The session has one mode (chat | plan |
 * autopilot); what runs without a card is derived from it by the companion
 * (`r4/companion/agent_mode.py`), so the console shows the mode only.
 *
 * ADR-104 amendment 3: autopilot auto-answers local non-deletion permission
 * prompts (`mode:autopilot`); `mode:autopilot(policy)` records that the
 * optional organisation policy was consulted, not that its prompt blocks
 * autopilot. Recognised user-file deletion/worktree removal still asks.
 */

const TRUST_SUFFIX: Record<string, string> = {
  server: "(policy)",
};

/** `mode:<m>` with the autopilot trust source when the snapshot names one. */
export function modeBadge(interactionMode: string, trustSource: string): string {
  const mode = interactionMode || "chat";
  const suffix = mode === "autopilot" ? (TRUST_SUFFIX[trustSource] ?? "") : "";
  return `mode:${mode}${suffix}`;
}

/** ADR-104 D-1 table, as `/settings matrix` and the Settings pane show it. */
export const MODE_MATRIX_LINES: readonly string[] = [
  "mode        what runs without a card",
  "  chat        read-only tools run; every other tool asks (card)",
  "  plan        read-only tools only; other tools are refused (not asked); the first reply is a plan → plan card",
  "  autopilot   local non-delete asks run with an audit note; ask_user continues with a stated assumption",
  "              user-file deletion, patch rename/delete sources, worktree removal, deleting merge-backs ask",
  "              denies and backend/sandbox refusals still apply; autopilot(policy) names the policy consulted",
  "switch: Shift+Tab or /mode chat|plan|autopilot (/trust = /mode autopilot) · plan card: a → autopilot, m → chat",
];
