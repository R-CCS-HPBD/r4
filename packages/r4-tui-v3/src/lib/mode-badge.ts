/**
 * ADR-104 D-1: one mode, one badge. The session has one mode (chat | plan |
 * autopilot); what runs without a card is derived from it by the companion
 * (`r4/companion/agent_mode.py`), so the console shows the mode only.
 *
 * ADR-104 amendment 1: autopilot runs every tool the safety floor lets
 * through (`mode:autopilot`); `mode:autopilot(policy)` = an organisation
 * policy narrows it to the server allowlist (ADR-035, opt-in). The older
 * "legacy" source is the plain default now.
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
  "  autopilot   every tool runs; it asks only before reading credentials or force-pushing, a knowledge decision,",
  "              your own ask rules, and a delete R4 could not back up first (copies go to ~/.local/share/r4/trash)",
  "              autopilot(policy): your organisation's allowlist narrows it (R4_AUTOPILOT_SERVER_POLICY, ADR-035)",
  "switch: Shift+Tab or /mode chat|plan|autopilot (/trust = /mode autopilot) · plan card: a → autopilot, m → chat",
];
