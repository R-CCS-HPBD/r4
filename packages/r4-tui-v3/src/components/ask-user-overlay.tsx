/**
 * Ask-user overlay — Companion ask_user clarifying question.
 *
 * No OpenTUI <input> (cursor bleeds onto the border). KeyTrap + windowed
 * draft; ↑↓ select options · Enter confirm · type for free text · Esc cancel.
 */

import { For, Show, createSignal, createEffect, createMemo, onCleanup, untrack } from "solid-js";
import { workspace, activeTab } from "../stores";
import {
  respondAskUser,
  cancelAskUser,
  isRemoteAskUser,
  returnRemoteAnswer,
  takeClosedElsewhere,
} from "../bridge/ask-user-bridge";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { chatWidth } from "../lib/term-width";
import { KeyTrap, keyTrapWouldInsert, keyTrapInsertChar } from "./key-trap";
import type { KeyEvent } from "@opentui/core";
import { Panel } from "../ui/Panel";

const ASK_COLS = 76;
const ASK_DRAFT_MAX = 200;
const QUESTION_MAX_LINES = 6;
const OPTION_MAX_LINES = 3;

/** Draft-row budget: the card's design width, narrowed for the chat column
 * actually available. Chrome ≈ 11: chat border+pad, card border+pad, row
 * indent on the left, card/chat right padding + clip safety on the right —
 * measured against the rendered frame, not read off the Yoga tree. The
 * window + "› " prefix + "█" cursor must fit this so truncate() and the
 * overflow=hidden box never eat the cursor or the last typed character
 * (report 2026-10-20). */
const draftCols = () => Math.max(20, Math.min(ASK_COLS, chatWidth() - 11));

/** Continuation indent aligning wrapped option lines under the option text
 * (prefix pattern is "{marker} N. " — 5 visible columns). */
const OPTION_CONTINUATION = "     ";

function wrapLines(text: string, width: number, maxLines: number): string[] {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if (!w) continue;
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= width) {
      cur = next;
    } else {
      if (cur) lines.push(cur);
      cur = w.length > width ? w.slice(0, width) : w;
      if (lines.length >= maxLines - 1) break;
    }
  }
  if (cur && lines.length < maxLines) lines.push(cur);
  return lines.length > 0 ? lines : [""];
}

function windowedDraft(text: string, cols: number): string {
  if (text.length <= cols) return text;
  return `…${text.slice(-(cols - 1))}`;
}

export function AskUserOverlay() {
  const pending = () => activeTab().askUser;
  // ADR-099 D-8: a remote session's question (durable; Esc leaves it pending).
  const remote = () => isRemoteAskUser(pending()?.id);
  const [draft, setDraft] = createSignal("");
  const [selected, setSelected] = createSignal(0);

  const options = createMemo(() => pending()?.options.slice(0, 9) ?? []);
  const hasOptions = createMemo(() => options().length > 0);

  // The card unmounts when its question goes away: remember which it showed.
  const mountedTab = untrack(() => workspace.activeChatTabId);
  let shownId: string | number | undefined;

  createEffect(() => {
    const req = pending();
    if (req) {
      shownId = req.id;
      setDraft("");
      setSelected(0);
    }
  });

  // A remote question closed under the card (answered elsewhere, expired,
  // the session stopped; RS-1 round 2, finding 5): the typed answer moves to
  // an empty composer, else a notice carries it (round 3, N3).
  onCleanup(() => {
    const typed = draft().trim();
    if (takeClosedElsewhere(shownId) && typed && workspace.activeChatTabId === mountedTab) {
      returnRemoteAnswer(typed, "the remote question closed while you typed");
    }
  });

  const submit = () => {
    const opts = options();
    const text = draft().trim();
    // Free-text wins if the user typed something; otherwise submit highlighted option.
    if (text.length > 0) {
      respondAskUser(text);
      return;
    }
    if (opts.length > 0) {
      respondAskUser(opts[selected()] ?? opts[0]!);
      return;
    }
    respondAskUser(null);
  };

  /** Each key step returns true when it consumed the key. */
  const onSubmitKeys = (event: KeyEvent): boolean => {
    if (event.name === "escape") {
      cancelAskUser();
      return true;
    }
    if (event.name === "return" || event.name === "enter") {
      submit();
      return true;
    }
    return false;
  };

  const onNavKeys = (event: KeyEvent): boolean => {
    if (!hasOptions()) return false;
    if (event.name === "up" || (event.name === "p" && event.ctrl)) {
      setSelected((s) => Math.max(0, s - 1));
      return true;
    }
    if (event.name === "down" || (event.name === "n" && event.ctrl)) {
      setSelected((s) => Math.min(options().length - 1, s + 1));
      return true;
    }
    return false;
  };

  // Number keys jump-select (1-based) and submit immediately for speed — only
  // while the free-text draft is empty: once the user is typing an answer, a
  // digit is text ("use 2 GPUs"), not option 2 (stabilization sweep 2026-10-21).
  const onNumberKey = (event: KeyEvent): boolean => {
    if (!hasOptions() || draft().length > 0 || event.ctrl) return false;
    if (!event.name || !/^[1-9]$/.test(event.name)) return false;
    const idx = Number(event.name) - 1;
    if (idx >= options().length) return false;
    setSelected(idx);
    respondAskUser(options()[idx]!);
    return true;
  };

  const onDraftKeys = (event: KeyEvent): boolean => {
    if (event.name === "backspace" || event.name === "delete") {
      setDraft((d) => d.slice(0, -1));
      return true;
    }
    if (!keyTrapWouldInsert(event) || event.ctrl || event.meta) return false;
    const ch = keyTrapInsertChar(event);
    if (ch) setDraft((d) => (d + ch).slice(0, ASK_DRAFT_MAX));
    return true;
  };

  const keySteps = [onSubmitKeys, onNavKeys, onNumberKey, onDraftKeys];

  const handleKey = (event: KeyEvent) => {
    if (!pending()) return;
    if (keySteps.some((step) => step(event))) event.preventDefault();
  };

  const displayDraft = () => {
    // cols - 3: "› " (2) + windowed body + "█" (1) lands exactly on draftCols().
    const body = windowedDraft(draft(), draftCols() - 3);
    return `› ${body}█`;
  };

  const questionLines = createMemo(
    () => wrapLines(pending()?.question || "(no question text)", ASK_COLS, QUESTION_MAX_LINES).length,
  );
  const optionRowCount = createMemo(() =>
    options().reduce(
      (n, opt) => n + wrapLines(opt, ASK_COLS - 8, OPTION_MAX_LINES).length,
      0,
    ),
  );
  // border 2 + padding 2 + title 1 + question + options block (margin 1 +
  // hint 1 + wrapped rows) + draft (margin 1 + row 1) + footer (margin 1 +
  // row 1). Without an explicit height + flexShrink 0, Yoga shrinks the card
  // and overflow="hidden" clips the draft/footer rows — the typed answer
  // renders off-card so the panel looks like it ignores input.
  const boxHeight = () =>
    Math.min(24, 9 + questionLines() + (hasOptions() ? optionRowCount() + 2 : 0));

  return (
    <Show when={pending()}>
      {/* V4: inline transcript card (was an absolute overlay). */}
      <Panel
        width="100%"
        height={boxHeight()}
        flexShrink={0}
        accent={palette.accent}
        flexDirection="column"
        padding={1}
        gap={0}
        overflow="hidden"
      >
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.accent}>{remote() ? "R4 (remote session) needs your input" : "R4 needs your input"}</text>
        </box>
        <For
          each={wrapLines(
            pending()!.question || "(no question text)",
            ASK_COLS,
            QUESTION_MAX_LINES,
          )}
        >
          {(line) => (
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.fgBright}>{line}</text>
            </box>
          )}
        </For>

        <Show when={hasOptions()}>
          <box height={1} width="100%" overflow="hidden" marginTop={1}>
            <text fg={palette.fgDim}>
              {`↑↓ select · Enter · 1–${options().length} · or type`}
            </text>
          </box>
          {/* Options wrap instead of truncating — users must read the whole
              option before selecting (report 2026-09). */}
          <For each={options()}>
            {(opt, idx) => {
              const isSel = () => idx() === selected() && draft().trim().length === 0;
              const rows = () => wrapLines(opt, ASK_COLS - 8, OPTION_MAX_LINES);
              return (
                <For each={rows()}>
                  {(line, rowIdx) => (
                    <box
                      height={1}
                      width="100%"
                      overflow="hidden"
                      backgroundColor={isSel() ? palette.bgActive : undefined}
                    >
                      <text fg={isSel() ? palette.fgBright : palette.fg}>
                        {truncate(
                          rowIdx() === 0
                            ? `${isSel() ? "▸" : " "} ${idx() + 1}. ${line}`
                            : `${OPTION_CONTINUATION}${line}`,
                          ASK_COLS,
                        )}
                      </text>
                    </box>
                  )}
                </For>
              );
            }}
          </For>
        </Show>

        <box
          height={1}
          width="100%"
          flexShrink={0}
          marginTop={1}
          overflow="hidden"
          backgroundColor={palette.msgUserBg}
          paddingLeft={1}
        >
          <text fg={draft().length > 0 ? palette.fgBright : palette.fgDim}>
            {draft().length > 0
              ? truncate(displayDraft(), draftCols())
              : hasOptions()
                ? "› type a custom answer (optional)…"
                : truncate(displayDraft(), draftCols())}
          </text>
          <KeyTrap
            focused={true}
            captureText
            onKeyDown={handleKey}
            onPaste={(text) => {
              const cleaned = text.replace(/\r?\n/g, " ");
              setDraft((d) => (d + cleaned).slice(0, ASK_DRAFT_MAX));
            }}
          />
        </box>
        <box height={1} width="100%" overflow="hidden" marginTop={1}>
          <text fg={palette.fgDim}>
            {(hasOptions()
              ? "Enter = highlighted option (or typed text) · "
              : "Enter submit · ") + (remote() ? "Esc answer later" : "Esc cancel")}
          </text>
        </box>
      </Panel>
    </Show>
  );
}
