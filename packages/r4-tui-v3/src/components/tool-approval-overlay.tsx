/**
 * Tool approval — bordered multi-line card, always fully visible (no fold,
 * no click-expand; same visual grammar as the agent question card).
 * ADR-104 D-3: the card offers what the companion's `options` allow — y once,
 * a always this session, p always this project, n reject with feedback, r/Esc
 * reject — and the companion remembers; nothing is auto-answered here.
 *
 * Keys are handled here AND via the global keymap (chat often keeps native
 * focus after Alt-Tab, so KeyTrap alone is not enough).
 */

import { For, Show, createEffect, createMemo, createSignal, on, onCleanup } from "solid-js";
import { t, fg } from "@opentui/core";
import { actions, activeTab } from "../stores";
import { dispatchToolApprovalKey, respondToolApproval } from "../bridge/approval-bridge";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { approvalLegend, cardOptions, type ApprovalOption } from "../lib/approval-options";
import { chatContentWidth } from "../lib/term-width";
import { KeyTrap } from "./key-trap";
import { CLAIM_RANK, claimKeys } from "../keymap/key-claims";
import type { KeyEvent, ScrollBoxRenderable } from "@opentui/core";
import {
  APPROVAL_PREVIEW_MAX_ROWS,
  buildApprovalPreview,
  diffFitsBlock,
  diffPreviewRows,
  wrapApprovalRows,
  type ApprovalPreviewRow,
} from "../lib/approval-preview";
import { Panel } from "../ui/Panel";
import { DiffBlock } from "../ui/DiffBlock";
import { ParallelDiffBlock, preferSideBySide } from "../ui/ParallelDiffBlock";
import { GUTTER_WIDTH } from "../ui/diff-chrome";
import { useTerminalDims } from "../lib/terminal-dims";
import { approvalCardRows, approvalNeedsTight, publishApprovalTight } from "../lib/approval-fit";

/** Text cells of one diff line in the card's diff block: the unified block's
 * gutter + sign, or one side-by-side cell's number + sign columns. */
function diffBlockBudget(width: number, sideBySide: boolean): number {
  if (sideBySide) return Math.max(20, Math.floor((width - 1) / 2)) - 7;
  return width - GUTTER_WIDTH - 2;
}

export function ToolApprovalOverlay() {
  const pending = () => activeTab().toolApproval;
  // C1: reject-with-feedback stage — on "no" the overlay flips to a comment input;
  // Enter submits the rejection with the typed feedback queued as the next user
  // turn (deliverFeedback).
  // The global keymap runs first (round 2, 2026-10-17); since plan H-6 it
  // sees this card only through the claims below, never through the stage.
  const [feedbackMode, setFeedbackMode] = createSignal(false);
  const [feedbackText, setFeedbackText] = createSignal("");
  /** The keymap's approval verbs (y / a / n / Enter) land here. In the
   * feedback stage they yield: typed text is feedback, never an approval. */
  const keymapVerb = (key: string): boolean => {
    if (feedbackMode()) return false;
    if (key !== "n") return dispatchToolApprovalKey(key);
    // n enters the feedback stage WITHOUT rejecting (only Enter from the
    // feedback input denies).
    setFeedbackMode(true);
    return true;
  };
  onCleanup(claimKeys("approval-card", ["approval"], () => pending() != null, { onKey: keymapVerb }));
  // The feedback stage owns Esc (one layer: back to the card) and typed text.
  onCleanup(
    claimKeys("approval-feedback", ["escape", "text"], feedbackMode, {
      rank: CLAIM_RANK.approvalFeedback,
      onKey: (key) => {
        if (key !== "escape") return false;
        setFeedbackMode(false);
        return true;
      },
    }),
  );
  // A new (or closed) approval never inherits the previous card's stage.
  createEffect(
    on(
      () => activeTab().toolApproval?.id,
      () => setFeedbackMode(false),
    ),
  );
  createEffect(
    on(feedbackMode, (active) => {
      if (!active) setFeedbackText("");
    }),
  );
  const headings = new Map([
    [true, "Reject with feedback"],
    [false, "Tool approval required"],
  ]);

  const sideBySide = () => preferSideBySide(chatContentWidth(0));
  /** A diff the colored block would cut (too long, a line past its column,
   * control characters) goes in the scrolled rows instead, every line of it
   * (w3-py#1: the card showed 30 lines of a write and approved the rest). */
  const diffAsRows = createMemo(() => {
    const diff = pending()?.diff;
    return !!diff && !diffFitsBlock(diff, diffBlockBudget(chatContentWidth(0), sideBySide()));
  });
  /** Every row of the context block, wrapped to the card's real width —
   * nothing is cut (chat#1, 2026-10-21: the card showed 2 lines of a
   * command, each cut at the column, and "y" ran the hidden rest). */
  const basePieces = createMemo((): ApprovalPreviewRow[] => {
    const p = pending();
    if (!p) return [];
    const rows = buildApprovalPreview({ tool: p.tool, description: p.description });
    return wrapApprovalRows(diffAsRows() ? [...rows, ...diffPreviewRows(p.diff!)] : rows);
  });
  // Short terminal (item 5): the card's natural height vs the terminal.
  const dims = useTerminalDims();
  const tight = createMemo(() => {
    const p = pending();
    if (!p) return false;
    return approvalNeedsTight(dims().height, naturalCardRows(p, cardOptions(p).length, basePieces().length, diffAsRows()));
  });
  createEffect(() => publishApprovalTight(tight()));
  onCleanup(() => publishApprovalTight(false));
  /** Tight cards scroll the diff with the command rows (no inline block). */
  const pieces = createMemo((): ApprovalPreviewRow[] => {
    const diff = pending()?.diff;
    if (!tight() || !diff || diffAsRows()) return basePieces();
    return [...basePieces(), ...wrapApprovalRows(diffPreviewRows(diff))];
  });
  /** Rows beyond the card's budget: shown in a scroll box + a marker row. */
  const hiddenRows = () => Math.max(0, pieces().length - APPROVAL_PREVIEW_MAX_ROWS);
  const scrolled = () => tight() || hiddenRows() > 0;
  let previewScroll: ScrollBoxRenderable | undefined;
  const scrollPreview = (key: string): boolean => {
    if (!previewScroll || !scrolled()) return false;
    const page = APPROVAL_PREVIEW_MAX_ROWS - 1;
    const step: Record<string, number> = { up: -1, down: 1, pageup: -page, pagedown: page };
    if (!(key in step)) return false;
    previewScroll.scrollBy(step[key]!);
    return true;
  };
  const previewRow = (row: ApprovalPreviewRow) => (
    <box height={1} width="100%" overflow="hidden" flexShrink={0}>
      <text children={t`${row.label ? fg(palette.fgDim)(row.label.padEnd(8)) : "        "}${row.text}`} />
    </box>
  );

  const submitReject = () => {
    // Read first: answering closes the card, and its reset effect clears the text.
    const feedback = feedbackText().trim();
    respondToolApproval("reject");
    if (feedback) deliverFeedback(feedback);
    setFeedbackMode(false);
    setFeedbackText("");
  };

  /** The typed feedback really goes to R4: queued as the next user message
   * behind the running turn (the old "Feedback queued for the assistant"
   * only parked it in the composer — nothing was sent; stabilization sweep
   * 2026-10-21, chat#8). With no turn running it waits in the composer, and
   * the notice says so. */
  const deliverFeedback = (feedback: string) => {
    if (activeTab().chatStreaming) {
      actions.sendChatMessage(feedback);
      actions.sendSystemNotice("rejected — your feedback is queued as your next message (sent when this turn ends)");
      return;
    }
    actions.setPendingChatDraft(feedback);
    actions.sendSystemNotice(
      `rejected — your feedback is in the composer, not sent yet (Enter sends it): ${truncate(feedback, chatContentWidth(10))}`,
    );
  };

  /** Feedback input keys: Enter submits the rejection, Esc leaves the stage.
   * Typed text (y/a included) is feedback — never an approval. */
  const handleFeedbackKey = (event: KeyEvent) => {
    const name = String(event.name ?? "").toLowerCase();
    if (name === "return" || name === "enter") {
      event.preventDefault();
      submitReject();
      return;
    }
    if (name === "escape") {
      event.preventDefault();
      setFeedbackMode(false);
    }
  };

  /** The card's answers (the companion's `options`). */
  const options = (): ApprovalOption[] => {
    const p = pending();
    return p ? cardOptions(p) : [];
  };
  /** Asks waiting behind this card (shown after it is answered). */
  const queued = () => activeTab().toolApprovalQueue?.length ?? 0;

  const handleKey = (event: KeyEvent) => {
    if (feedbackMode()) {
      // Leaving the stage clears the typed text (the feedbackMode effect).
      handleFeedbackKey(event);
      event.preventDefault();
      return;
    }
    const name = String(event.name ?? "").toLowerCase();
    // N enters feedback mode WITHOUT rejecting — only Enter from the textarea
    // actually denies. Everything else routes to the existing yes/always/no map.
    if (name === "n") {
      event.preventDefault();
      setFeedbackMode(true);
      return;
    }
    if (scrollPreview(name) || dispatchToolApprovalKey(name || event.sequence || "")) {
      event.preventDefault();
    }
  };

  return (
    <Show when={pending()}>
      {/* Bordered multi-line card (agent questions use the same form) — shown
          in full as soon as the approval arrives; nothing to expand. */}
      <Panel
        width="100%"
        accent={palette.statusWarn}
        flexDirection="column"
        gap={0}
        overflow="hidden"
        {...cardFlex(tight())}
        paddingLeft={1}
      >
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.statusWarn}>
            {`${headings.get(feedbackMode()) ?? "Tool approval required"}${queued() > 0 ? ` · ${queued()} more waiting` : ""}`}
          </text>
        </box>
        {/* ADR-092: which plan agent / team role asks (absent = the lead). */}
        <Show when={pending()!.agent}>
          <box height={1} width="100%" overflow="hidden">
            <text children={t`${fg(palette.fgDim)("agent".padEnd(8))}${pending()!.agent!}`} />
          </box>
        </Show>
        <Show when={pending()!.neverAuto}>
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.statusWarn}>never auto-approved — you answer this one each time</text>
          </box>
        </Show>
        {/* Fix 2026-10-30: a content fence asks about THIS call — no earlier
            yes or project rule answered it, and y approves this call only. */}
        <Show when={pending()!.fenceReason}>
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.statusWarn}>{`fenced: ${pending()!.fenceReason} — this call only`}</text>
          </box>
        </Show>
        {/* Full context block ($ cmd / path / args) — no truncation so users
            can actually read the thing they're approving. Past the row budget
            it scrolls in place, and the marker row says how much is below. */}
        <Show when={scrolled()} fallback={<For each={pieces()}>{previewRow}</For>}>
          {/* Tight: the scroll takes whatever the card has left (≥1 row). */}
          <scrollbox
            ref={(el: ScrollBoxRenderable) => {
              previewScroll = el;
            }}
            {...previewFlex(tight())}
            minHeight={1}
            width="100%"
            scrollY
            overflow="hidden"
          >
            <For each={pieces()}>{previewRow}</For>
          </scrollbox>
          <Show when={!tight()}>
            <box height={1} width="100%" overflow="hidden" flexShrink={0}>
              <text fg={palette.statusWarn}>
                {`▼ ${pieces().length} rows, ${APPROVAL_PREVIEW_MAX_ROWS} visible — scroll here (wheel · ↑↓ PgUp PgDn) and read every row before approving`}
              </text>
            </box>
          </Show>
        </Show>
        {/* Server-computed change preview (opencode EditBody parity): the
            human approves the ACTUAL diff, side-by-side at ≥110 columns. */}
        <Show when={!feedbackMode() && !diffAsRows() && !tight() && pending()!.diff}>
          <Show
            when={sideBySide()}
            fallback={
              <DiffBlock
                content={pending()!.diff!}
                width={chatContentWidth(0)}
                filePath={pending()!.path}
              />
            }
          >
            <ParallelDiffBlock
              content={pending()!.diff!}
              width={chatContentWidth(0)}
              filePath={pending()!.path}
            />
          </Show>
        </Show>
        {/* Required-input hint — without this the card can read as plain text,
            leaving users unsure what to type. Keys mirror dispatchToolApprovalKey. */}
        <Show when={!feedbackMode() && !tight()}>
          <box height={1} width="100%" overflow="hidden" marginTop={1}>
            <text fg={palette.fgDim}>
              {`Keys: ${approvalLegend(options())}`}
            </text>
          </box>
        </Show>
        {/* Tight (item 5): one pinned legend row under the scrolled rows —
            the option rows below yield to it. */}
        <Show when={!feedbackMode() && tight()}>
          <box height={1} width="100%" overflow="hidden" flexShrink={0}>
            <text fg={palette.statusWarn}>{tightLegend(pieces().length, options())}</text>
          </box>
        </Show>
        <Show when={!tight()}>
          <ApprovalOptionRows options={options()} onReject={() => setFeedbackMode(true)} />
        </Show>
        <KeyTrap focused={!feedbackMode()} captureText onKeyDown={handleKey} />
        {/* C1 — feedback stage replaces keys with a textarea */}
        <Show when={feedbackMode()}>
          <box height={1} width="100%" overflow="hidden" marginTop={1}>
            <text fg={palette.accent}>Tell R4 what to do instead (Enter submits):</text>
          </box>
          <box height={1} width="100%" overflow="hidden">
            <input
              placeholder="feedback…"
              value={feedbackText()}
              width="100%"
              focused={true}
              onInput={setFeedbackText}
              onKeyDown={handleFeedbackKey}
              textColor={palette.fgBright}
              placeholderColor={palette.fgDim}
              backgroundColor={palette.msgUserBg}
            />
          </box>
        </Show>
      </Panel>
    </Show>
  );
}

type PendingApproval = {
  agent?: string;
  neverAuto?: boolean;
  fenceReason?: string;
  diff?: string;
};

/** The card's natural height (lib/approval-fit) for this approval. */
function naturalCardRows(p: PendingApproval, optionCount: number, previewRows: number, diffAsRows: boolean): number {
  return approvalCardRows({
    extraRows: (p.agent ? 1 : 0) + (p.neverAuto ? 1 : 0) + (p.fenceReason ? 1 : 0),
    previewRows,
    previewCap: APPROVAL_PREVIEW_MAX_ROWS,
    diffRows: p.diff && !diffAsRows ? p.diff.split("\n").length + 2 : 0,
    optionCount,
  });
}

/** Card box: tight fills the chat body and shrinks; normal keeps its rows. */
function cardFlex(tight: boolean) {
  return tight
    ? { padding: 0, marginBottom: 0, flexGrow: 1, flexBasis: 0, flexShrink: 1 }
    : { padding: 1, marginBottom: 1, flexGrow: 0, flexBasis: "auto" as const, flexShrink: 0 };
}

/** Preview scroll: tight takes what the card has left; normal is capped. */
function previewFlex(tight: boolean) {
  return tight
    ? { height: "auto" as const, flexGrow: 1, flexBasis: 0, flexShrink: 1 }
    : { height: APPROVAL_PREVIEW_MAX_ROWS, flexGrow: 0, flexBasis: "auto" as const, flexShrink: 0 };
}

/** The tight card's one-line key legend (item 5): the offered keys, and
 * that the rows above scroll. */
export function tightLegend(rows: number, options: readonly ApprovalOption[]): string {
  const always = options.includes("always") ? " · a always" : "";
  return `y/Enter once${always} · n/r reject · Esc reject · ↑↓ PgUp PgDn scroll ${rows} rows`;
}

/** Lettered option rows (opencode approval parity), only the offered ones;
 * click also works. */
function ApprovalOptionRows(props: { options: readonly ApprovalOption[]; onReject: () => void }) {
  return (
    <>
      <box height={1} width="100%" overflow="hidden" marginTop={1} onMouseDown={() => respondToolApproval("once")}>
        <text fg={palette.statusOk}>[Y]es — allow once (this call)</text>
      </box>
      <Show when={props.options.includes("always")}>
        <box height={1} width="100%" overflow="hidden" onMouseDown={() => respondToolApproval("always")}>
          <text fg={palette.accent}>[A]lways — this tool, this session</text>
        </box>
      </Show>
      <box height={1} width="100%" overflow="hidden" onMouseDown={props.onReject}>
        <text fg={palette.statusErr}>[N]o — reject</text>
      </box>
    </>
  );
}
