/**
 * Key ownership registry: one map, claim key → claimants (plan H-6,
 * 2026-10-18; first cut in TUI audit round 2, 2026-10-17, as three sets).
 * The model is written down in docs/r4/TUI_REFERENCE.md, "Key ownership".
 *
 * The global keymap is a prepended keypress listener: it runs BEFORE the
 * focused renderable, and a handler that does not return `false` swallows
 * the key. So the keymap must know who else owns a key before it acts. It
 * never reads that from component state: the owner registers a claim here,
 * and keymap handlers only ask the registry.
 *
 *  - State in the workspace store (an overlay's open flag) → the claim is
 *    declared once in `store-claims.ts`, beside every other store layer.
 *  - State in a component (signals: the composer's search, the approval
 *    card's feedback stage, a picker's pending confirm) → the component calls
 *    `claimKeys` and releases the claim from onCleanup.
 *
 * The registry is reactive (an epoch signal): the App effect that feeds the
 * keymap's `idle` / `claim.*` data re-runs when a claim comes or goes.
 */

import { createSignal } from "solid-js";

/** Keys, and key classes, a layer can claim. */
export type ClaimKey =
  /** The Esc ladder: the highest-ranked active claimant decides. */
  | "escape"
  /** The chat ↔ pane focus toggle yields Tab. */
  | "tab"
  /** Pane and overlay letter shortcuts yield (feeds the keymap `idle` gate). */
  | "letters"
  /** Typed text and Enter belong to another component's input; the
   * composer swallows them instead of typing. */
  | "text"
  /** The tool-approval verbs y / a / n / Enter (bindings carry `claim`). */
  | "approval"
  /** Detail-overlay verb families (bindings carry `claim`). */
  | "team-overlay"
  | "exec-overlay"
  /** Chords a focused KeyTrap uses for list navigation or cursor moves. */
  | "ctrl+n"
  | "ctrl+p"
  | "ctrl+b"
  | "ctrl+g";

/** Esc ladder positions; a higher rank runs first, and within a rank the
 * newest claimant. Only the order matters, never the numbers. */
export const CLAIM_RANK = {
  transient: 100,
  revertArm: 95,
  splash: 90,
  approvalFeedback: 85,
  tour: 80,
  remotePickerConfirm: 79,
  remotePicker: 78,
  webAccess: 77,
  venuePicker: 76,
  detailOverlay: 70,
  modal: 60,
  local: 50,
  pane: 10,
} as const;

export interface ClaimOptions {
  /** Ladder position (default `CLAIM_RANK.local`). */
  rank?: number;
  /** Acts on the key and returns true when it consumed it. Absent: the
   * claimant yields the key to its own focused renderable. */
  onKey?: (key: string) => boolean;
}

export interface Claimant {
  owner: string;
  rank: number;
  active: () => boolean;
  onKey?: (key: string) => boolean;
}

const claims = new Map<ClaimKey, Claimant[]>();
const [epoch, setEpoch] = createSignal(0);

const always = (): boolean => true;

/** Highest rank first; within a rank the NEWEST claimant first (the layer
 * mounted last sits on top). */
function insertClaimant(key: ClaimKey, claimant: Claimant): void {
  const list = [...(claims.get(key) ?? [])];
  const at = list.findIndex((c) => c.rank <= claimant.rank);
  list.splice(at === -1 ? list.length : at, 0, claimant);
  claims.set(key, list);
}

function removeClaimant(key: ClaimKey, claimant: Claimant): void {
  const list = (claims.get(key) ?? []).filter((c) => c !== claimant);
  if (list.length > 0) claims.set(key, list);
  else claims.delete(key);
}

/**
 * Claim `keys` for `owner` while `active()` holds. Returns the release
 * function (call it from onCleanup; calling it twice is harmless).
 */
export function claimKeys(
  owner: string,
  keys: readonly ClaimKey[],
  active: () => boolean = always,
  options: ClaimOptions = {},
): () => void {
  const claimant: Claimant = {
    owner,
    rank: options.rank ?? CLAIM_RANK.local,
    active,
    onKey: options.onKey,
  };
  for (const key of keys) insertClaimant(key, claimant);
  setEpoch((n) => n + 1);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    for (const key of keys) removeClaimant(key, claimant);
    setEpoch((n) => n + 1);
  };
}

/** The highest-ranked claimant of `key` whose claim holds now. */
export function topClaimant(key: ClaimKey): Claimant | undefined {
  epoch();
  return (claims.get(key) ?? []).find((c) => c.active());
}

/** Some claimant holds `key` now. */
export function keyClaimed(key: ClaimKey): boolean {
  return topClaimant(key) !== undefined;
}

/** Owners holding `key` now, highest rank first (diagnostics and tests). */
export function keyClaimants(key: ClaimKey): string[] {
  epoch();
  return (claims.get(key) ?? []).filter((c) => c.active()).map((c) => c.owner);
}

/** Active claimants of `key` ranked above `rank`, highest first (Alt+W
 * asks which dialog is open before it draws an overlay, 2026-10-24). */
export function claimantsAbove(key: ClaimKey, rank: number): Claimant[] {
  epoch();
  return (claims.get(key) ?? []).filter((c) => c.rank > rank && c.active());
}

/**
 * Hand the key to its highest-ranked active claimant. Returns that
 * claimant's verdict; false when nobody holds the key or the holder yields
 * (no `onKey`), so the keymap lets the key through.
 */
export function runClaim(key: ClaimKey, name: string = key): boolean {
  const top = topClaimant(key);
  return top?.onKey ? top.onKey(name) : false;
}
