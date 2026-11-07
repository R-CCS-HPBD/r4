/**
 * ADR-104 D-3: the answers an approval card offers. The companion says which
 * (`options` on the approval_request line); the console renders them and
 * never answers an ask by itself.
 *
 *   once     this call
 *   always   this tool for the session (the companion remembers)
 *   reject   this call; the agent is told the user said no
 *
 * Amendment 1 (2026-11-03): "always this project" (`p`) is gone — one
 * keypress wrote a durable, argument-blind rule; standing rules are
 * deliberate configuration (~/.config/r4/permissions.json, /settings rules).
 */

export type ApprovalOption = "once" | "always" | "reject";

export const ALL_APPROVAL_OPTIONS: readonly ApprovalOption[] = ["once", "always", "reject"];

/** Per-call answers only: a never-auto or fenced ask. */
export const PER_CALL_OPTIONS: readonly ApprovalOption[] = ["once", "reject"];

const KNOWN = new Set<string>(ALL_APPROVAL_OPTIONS);

export function isApprovalOption(value: unknown): value is ApprovalOption {
  return typeof value === "string" && KNOWN.has(value);
}

/** What a card built without `options` derives them from (an older bridge,
 * a remote sandbox intent, a test). */
export interface LegacyApprovalHints {
  options?: readonly unknown[];
  sessionApprovalAllowed?: boolean;
  neverAuto?: boolean;
  fenced?: boolean;
}

/** The server's list, filtered to known words, in canonical order; `once`
 * and `reject` are always there (a person can always answer per call). */
function fromServerList(list: readonly unknown[]): ApprovalOption[] {
  const offered = new Set(list.filter(isApprovalOption));
  offered.add("once");
  offered.add("reject");
  return ALL_APPROVAL_OPTIONS.filter((o) => offered.has(o));
}

/** The options of one ask: the server's word when it sent one, else derived
 * as an older bridge meant it (ADR-104 D-3). */
export function approvalOptionsOf(hints: LegacyApprovalHints): ApprovalOption[] {
  if (Array.isArray(hints.options)) return fromServerList(hints.options);
  const perCall = hints.sessionApprovalAllowed === false || hints.neverAuto === true || hints.fenced === true;
  return perCall ? [...PER_CALL_OPTIONS] : [...ALL_APPROVAL_OPTIONS];
}

/** A card's options; a card written without them (a raw store write in an
 * older test, a pre-ADR-104 snapshot) derives them like an older bridge. */
export function cardOptions(card: {
  options?: readonly unknown[];
  sessionApprovalAllowed?: boolean;
  neverAuto?: boolean;
  fenceReason?: string;
}): ApprovalOption[] {
  if (Array.isArray(card.options)) return fromServerList(card.options);
  return approvalOptionsOf({
    sessionApprovalAllowed: card.sessionApprovalAllowed,
    neverAuto: card.neverAuto,
    fenced: Boolean(card.fenceReason),
  });
}

/** Remote (sandbox) intents answer through session.venue_decide, which takes
 * once | always | reject. */
export function remoteApprovalOptions(fenced: boolean): ApprovalOption[] {
  return fenced ? [...PER_CALL_OPTIONS] : ["once", "always", "reject"];
}

const KEY_DECISIONS: Record<string, ApprovalOption> = {
  y: "once",
  return: "once",
  enter: "once",
  a: "always",
  r: "reject",
  escape: "reject",
};

/** The answer a card key gives, or null when the key is not an answer or
 * its option is not offered on this card (`a` then does nothing).
 * `n` is not here: it opens the reject-with-feedback stage. */
export function decisionForKey(name: string, options: readonly ApprovalOption[]): ApprovalOption | null {
  const decision = KEY_DECISIONS[(name ?? "").toLowerCase()];
  if (!decision) return null;
  return options.includes(decision) ? decision : null;
}

/** One legend line for the card (only the offered keys). */
export function approvalLegend(options: readonly ApprovalOption[]): string {
  const parts = ["y/Enter allow once"];
  if (options.includes("always")) parts.push("a always this session");
  parts.push("n reject with feedback", "r/Esc reject");
  return parts.join(" · ");
}
