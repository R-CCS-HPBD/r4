/**
 * Store-owned key claims (plan H-6, 2026-10-18): every layer whose open
 * state lives in the workspace store, declared once, in Esc-ladder order.
 * This table IS the precedence table of docs/r4/TUI_REFERENCE.md "Key
 * ownership"; component-owned claims (composer search, approval feedback
 * stage, a picker's pending confirm, catalog search, infra lists) slot in
 * by rank from their components.
 *
 * Before H-6 these rows were five ad-hoc guards inside the keymap
 * (`anyModalOpen`, `anyDetailOverlayOpen`, `closeTransientLayer`,
 * `closeSelfContainedOverlay`, `keyTrapOverlayOpen`) plus the
 * `overlayBlocksLetters` list in lib/space-shortcuts.ts and three
 * `keymap.setData` guard fields. Each list's membership is kept exactly.
 *
 * Registered once, on first import (keymap/index.ts and
 * lib/space-shortcuts.ts both import this module), so unit tests that
 * drive the store without mounting <App/> see the same ladder.
 */

import { actions, setWorkspace, workspace, activeTab } from "../stores";
import { closeCitationPreview } from "../lib/citation-preview";
import { CLAIM_RANK, claimKeys, type ClaimKey } from "./key-claims";
import { respondToolApproval } from "../bridge/approval-bridge";

interface StoreLayer {
  owner: string;
  open: () => boolean;
  keys: readonly ClaimKey[];
  rank: number;
  /** Closes the layer on Esc. Absent: the layer yields Esc to its input. */
  onEscape?: () => boolean;
}

/** A focused KeyTrap's list navigation (Ctrl+N / Ctrl+P) and Ctrl+B. */
const LIST_NAV: readonly ClaimKey[] = ["ctrl+n", "ctrl+p", "ctrl+b"];
/** A modal input owns typed letters and Tab. */
const MODAL_INPUT: readonly ClaimKey[] = ["letters", "tab"];

/** A modal layer's Esc closes the modal surfaces only (stab-focus,
 * 2026-10-21): the running turn, its queue and the docked pane stay. The
 * full closeAllOverlays sweep interrupted the turn and closed the pane. */
function closeModals(): boolean {
  actions.closeModalOverlays();
  return true;
}

/** One Esc clears every detail overlay at once (B4, 2026-09-27): the
 * pane underneath stays. */
function closeDetailOverlays(): boolean {
  actions.setTeamDetailId(null);
  actions.setAgentDetailId(null);
  actions.setEndpointDetailId(null);
  actions.setExecDetailId(null);
  actions.setWorkflowDefId(null);
  return true;
}

function cancelRevertArm(): boolean {
  actions.sendSystemNotice(`revert of ${workspace.revertArmPath} cancelled`);
  setWorkspace("revertArmPath", null);
  return true;
}

function closeWith(close: () => void): () => boolean {
  return () => {
    close();
    return true;
  };
}

/** Every app overlay draws above the splash (layout/shell.tsx mounts it
 * under app.tsx's overlays): while one is open the splash yields its keys
 * to it, so Esc closes Help opened over the splash first (stab-focus). */
function overlayOverSplash(): boolean {
  return STORE_LAYERS.some((l) => l.owner !== "splash" && l.rank > CLAIM_RANK.local && l.open());
}

/** Transient layers close before anything else: the /btw overlay never
 * touches the running turn (the modal sweep would interrupt streaming); an
 * armed revert cancels before its pane closes (Wave H). The splash skips
 * to chat even before its input's focus settles. */
const TRANSIENT_LAYERS: StoreLayer[] = [
  {
    owner: "btw-overlay",
    open: () => workspace.btw.open,
    keys: ["escape"],
    rank: CLAIM_RANK.transient,
    onEscape: closeWith(() => actions.closeBtw()),
  },
  {
    // K1.4: a citation chip's passage preview; like /btw it never touches
    // the running turn. It holds Tab too (K1 wave A): Tab used to move the
    // focus to the docked pane underneath, whose [ ] / a / r (K1.5) then
    // decided proposals the person could not see.
    owner: "citation-preview",
    open: () => workspace.citationPreview != null,
    keys: ["escape", "tab"],
    rank: CLAIM_RANK.transient,
    onEscape: closeWith(closeCitationPreview),
  },
  {
    owner: "codeview-revert-arm",
    open: () => Boolean(workspace.revertArmPath),
    keys: ["escape"],
    rank: CLAIM_RANK.revertArm,
    onEscape: cancelRevertArm,
  },
  {
    owner: "splash",
    open: () => workspace.splashOpen && !overlayOverSplash(),
    keys: ["escape", ...MODAL_INPUT],
    rank: CLAIM_RANK.splash,
    onEscape: closeWith(() => actions.closeSplash()),
  },
];

/** Self-contained overlays close ONE layer on Esc, never through
 * closeAllOverlays (which also interrupts the turn and drops the pane). */
const SELF_CONTAINED_LAYERS: StoreLayer[] = [
  {
    owner: "tour",
    open: () => workspace.tourOpen,
    keys: ["escape", ...LIST_NAV],
    rank: CLAIM_RANK.tour,
    onEscape: closeWith(() => actions.closeTour()),
  },
  {
    owner: "remote-source-picker",
    open: () => workspace.remoteSourcePickerOpen,
    keys: ["escape", ...MODAL_INPUT, ...LIST_NAV],
    rank: CLAIM_RANK.remotePicker,
    onEscape: closeWith(() => actions.closeRemoteSourcePicker()),
  },
  {
    // ADR-099 D-2: the web-access question (its domains field takes letters).
    owner: "web-access",
    open: () => workspace.webAccessPrompt !== null,
    keys: ["escape", ...MODAL_INPUT, ...LIST_NAV],
    rank: CLAIM_RANK.webAccess,
    onEscape: closeWith(() => actions.closeWebAccessPrompt()),
  },
  {
    // ADR-102 (2026-11-01): the offer to start the local search server.
    owner: "search-server-offer",
    open: () => workspace.searchServerOffer,
    keys: ["escape", ...MODAL_INPUT],
    rank: CLAIM_RANK.webAccess,
    onEscape: closeWith(() => actions.declineSearchServerOffer()),
  },
  {
    owner: "venue-picker",
    open: () => workspace.venuePickerOpen,
    keys: ["escape", ...MODAL_INPUT, ...LIST_NAV],
    rank: CLAIM_RANK.venuePicker,
    onEscape: closeWith(() => actions.closeVenuePicker()),
  },
];

function detailLayer(owner: string, open: () => boolean, verbs: readonly ClaimKey[] = []): StoreLayer {
  return { owner, open, keys: ["escape", ...verbs], rank: CLAIM_RANK.detailOverlay, onEscape: closeDetailOverlays };
}

/** Detail overlays ride their own Esc layer above the modal sweep: the
 * sweep would kill the pane with the overlay. */
const DETAIL_LAYERS: StoreLayer[] = [
  detailLayer("team-detail", () => workspace.teamDetailId != null, ["team-overlay"]),
  detailLayer("agent-detail", () => workspace.agentDetailId != null),
  detailLayer("endpoint-detail", () => workspace.endpointDetailId != null),
  detailLayer("exec-detail", () => workspace.execDetailId != null, ["exec-overlay"]),
  detailLayer("workflow-def", () => workspace.workflowDefId != null),
];

function modalLayer(owner: string, open: () => boolean, keys: readonly ClaimKey[]): StoreLayer {
  return { owner, open, keys: ["escape", ...keys], rank: CLAIM_RANK.modal, onEscape: closeModals };
}

/** Esc on the approval card itself rejects that one ask (ADR-104 D-3),
 * through respondToolApproval so its Research-list mirror settles too. */
function rejectApprovalCard(): boolean {
  respondToolApproval("reject");
  return true;
}

/** Modal overlays: Esc closes the modal surfaces (closeModalOverlays).
 * The approval card is FIRST: within a rank the newest claimant runs first,
 * so every other modal (help, palette, slash bar…) drawn over the card takes
 * Esc before it, and closing one of them leaves the approval pending
 * (ADR-104 defect 10). */
const MODAL_LAYERS: StoreLayer[] = [
  {
    owner: "tool-approval",
    open: () => activeTab().toolApproval != null,
    keys: ["escape", ...MODAL_INPUT],
    rank: CLAIM_RANK.modal,
    onEscape: rejectApprovalCard,
  },
  modalLayer("command-palette", () => workspace.commandPaletteOpen, [...MODAL_INPUT, ...LIST_NAV]),
  modalLayer("slash-bar", () => workspace.slashBarOpen, MODAL_INPUT),
  modalLayer("slash-picker", () => workspace.slashPickerOpen, [...MODAL_INPUT, ...LIST_NAV]),
  modalLayer("help", () => workspace.helpOpen, MODAL_INPUT),
  modalLayer("quit-dialog", () => workspace.quitDialogOpen, MODAL_INPUT),
  modalLayer("ask-user", () => activeTab().askUser != null, [...MODAL_INPUT, ...LIST_NAV]),
  modalLayer("list-overlay", () => workspace.listOverlay != null, [...MODAL_INPUT, ...LIST_NAV]),
  modalLayer("start-research", () => workspace.startResearch != null, MODAL_INPUT),
  modalLayer("connect-wizard", () => workspace.connectWizard != null, MODAL_INPUT),
  modalLayer("start-actions-menu", () => workspace.startActionsMenuOpen, [...MODAL_INPUT, ...LIST_NAV]),
  // The rewind menu never blocked letters or Tab (kept as it was).
  modalLayer("rewind-menu", () => workspace.rewindMenuOpen, LIST_NAV),
];

/** A focused pane-local input (Live verify, Knowledge verify) owns letters
 * and Tab; while its pane holds the keys it owns Esc too (it leaves verify
 * instead of the pane closing). The composer's draft keeps the letters
 * while the composer, not a focused pane, owns the keys (r2 #67). */
const LOCAL_LAYERS: StoreLayer[] = [
  { owner: "verify-input", open: () => workspace.verifyInputFocused, keys: MODAL_INPUT, rank: CLAIM_RANK.local },
  {
    owner: "verify-input",
    open: () => workspace.paneFocus && workspace.verifyInputFocused,
    keys: ["escape"],
    rank: CLAIM_RANK.local,
  },
  {
    owner: "composer-draft",
    open: () => !workspace.paneFocus && (activeTab().chatDraft ?? "").trim().length > 0,
    keys: ["letters"],
    rank: CLAIM_RANK.local,
  },
];

/** The bottom of the Esc ladder: the docked pane closes. */
const PANE_LAYER: StoreLayer = {
  owner: "pane",
  open: () => workspace.paneView !== null,
  keys: ["escape"],
  rank: CLAIM_RANK.pane,
  onEscape: closeWith(() => actions.closePane()),
};

/** Every store-owned layer, highest rank first (the documented order). */
export const STORE_LAYERS: readonly StoreLayer[] = [
  ...TRANSIENT_LAYERS,
  ...SELF_CONTAINED_LAYERS,
  ...DETAIL_LAYERS,
  ...MODAL_LAYERS,
  ...LOCAL_LAYERS,
  PANE_LAYER,
];

/** A layer that owns the keyboard through a focused input or KeyTrap:
 * typed letters, or list navigation. */
function ownsKeyboard(layer: StoreLayer): boolean {
  return layer.rank > CLAIM_RANK.local && (layer.keys.includes("letters") || layer.keys.includes("ctrl+n"));
}

/** Owners of the keyboard-owning layers (pins enumerate them). */
export function keyboardLayerOwners(): string[] {
  return STORE_LAYERS.filter(ownsKeyboard).map((l) => l.owner);
}

/** Some keyboard-owning store layer is open now. The composer yields while
 * one is (lib/chat-input-focus.ts), so its close is a false→true focus edge
 * and the composer remounts focused. Derived from this table so a new
 * overlay can never again leave the composer dead after it closes (the
 * /connect wizard and venue / remote-source pickers did, stab-focus). */
export function keyboardLayerOpen(): boolean {
  return STORE_LAYERS.some((l) => ownsKeyboard(l) && l.open());
}

function escapeHandler(layer: StoreLayer): ((key: string) => boolean) | undefined {
  const onEscape = layer.onEscape;
  if (!onEscape) return undefined;
  return (key) => key === "escape" && onEscape();
}

let registered = false;

/** Register the table once per process (idempotent). */
export function registerStoreClaims(): void {
  if (registered) return;
  registered = true;
  for (const layer of STORE_LAYERS) {
    claimKeys(layer.owner, layer.keys, layer.open, { rank: layer.rank, onKey: escapeHandler(layer) });
  }
}

registerStoreClaims();
