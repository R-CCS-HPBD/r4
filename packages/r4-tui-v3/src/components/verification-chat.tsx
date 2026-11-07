/**
 * Verification Chat — Phase 5.
 *
 * Live: CLI `r4 project verify`. Offline/demo: local explanation answers.
 * Answers stay in the pane: the TUI writes no finding. Findings belong to a
 * project's verification reports (GET/PATCH /umrs/knowledge/findings); the
 * old "upsert answer as finding" key posted to a route that never
 * persisted anything and is gone (H-10.1 follow-up, 2026-10-18).
 *
 * OpenTUI: never use expanding <input> here — focusing Input collapses the
 * 30%-width pane and walks the cursor into neighboring cells. KeyTrap + a
 * windowed text row keeps layout stable.
 */

import { For, Show, createSignal, createEffect, onCleanup } from "solid-js";
import { workspace, actions } from "../stores";
import { resolveVerificationForUi } from "../lib/resolve-verification";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import { KeyTrap, keyTrapInsertChar } from "./key-trap";
import { Panel } from "../ui/Panel";
import type { KeyEvent } from "@opentui/core";
import type { VerificationAnswer } from "../types";

/** Wrap column tracks the real 30%-of-pane width (was fixed 40). */
const verifyWrap = () => panePanelWidth(0.3, 10, 18);
/** Visible columns inside the 30% pane (› + draft). Cap draft so cursor never escapes. */
const verifyInputCols = () => Math.max(16, verifyWrap() - 8);
const VERIFY_DRAFT_MAX = 240;

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

function windowedDraft(text: string, cols: number): string {
  if (text.length <= cols) return text;
  return `…${text.slice(-(cols - 1))}`;
}

function VerificationAnswerView(props: { answer: VerificationAnswer }) {
  const a = props.answer;
  const confidenceColor = () => {
    if (a.confidence >= 0.8) return palette.statusOk;
    if (a.confidence >= 0.5) return palette.statusWarn;
    return palette.statusErr;
  };
  const answerLines = () => {
    const text = a.answer.replace(/\s+/g, " ").trim();
    const max = verifyWrap();
    const lines: string[] = [];
    for (let i = 0; i < text.length && lines.length < 4; i += max) {
      lines.push(text.slice(i, i + max));
    }
    return lines.length > 0 ? lines : ["(empty)"];
  };
  return (
    <Panel
      flexDirection="column"
      marginBottom={1}
      padding={1}
      overflow="hidden"
      width="100%"
    >
      <For each={answerLines()}>{(line) => <Line>{line}</Line>}</For>
      <Show when={a.confidence > 0}>
        <Line fg={confidenceColor()}>{`Confidence: ${(a.confidence * 100).toFixed(0)}%`}</Line>
      </Show>
      <Show when={a.reasoningPath.length > 0}>
        <Line fg={palette.fgDim}>Reasoning Path:</Line>
        <For each={a.reasoningPath.slice(0, 4)}>
          {(step) => <Line fg={palette.fg}>{truncate(`→ ${step}`, verifyWrap())}</Line>}
        </For>
      </Show>
      <Show when={a.evidenceIds.length > 0}>
        <Line fg={palette.fgDim}>{`Evidence (${a.evidenceIds.length}):`}</Line>
        <For each={a.evidenceIds.slice(0, 4)}>
          {(eid) => <Line fg={palette.knowledge}>{truncate(`◆ ${eid}`, verifyWrap())}</Line>}
        </For>
      </Show>
      <Show when={a.graphTraversal.length > 0}>
        <Line fg={palette.fgDim}>Graph Traversal:</Line>
        <Line fg={palette.accent}>{truncate(a.graphTraversal.join(" "), verifyWrap())}</Line>
      </Show>
    </Panel>
  );
}

export function VerificationChat() {
  const [draft, setDraft] = createSignal("");
  const [answers, setAnswers] = createSignal<VerificationAnswer[]>([]);
  const [busy, setBusy] = createSignal(false);
  const [inputFocused, setInputFocused] = createSignal(false);

  const focusInput = (on: boolean) => {
    setInputFocused(on);
    actions.setVerifyInputFocused(on);
    if (on) {
      actions.setResearchFocus("verify");
    } else {
      // Esc leave: clear draft so idle/Tab are not stuck blocked.
      setDraft("");
      actions.setVerifyDraft("");
      if (workspace.researchFocus === "verify") actions.setResearchFocus("graph");
    }
  };

  createEffect(() => {
    if (workspace.verifyInputFocused) setInputFocused(true);
    else setInputFocused(false);
  });

  onCleanup(() => actions.setVerifyInputFocused(false));

  const setDraftBoth = (value: string) => {
    const clipped = value.slice(0, VERIFY_DRAFT_MAX);
    setDraft(clipped);
    actions.setVerifyDraft(clipped);
  };

  const submit = () => {
    const text = draft().trim();
    if (!text || busy()) return;
    setBusy(true);
    setDraftBoth("");
    void (async () => {
      try {
        const answer = await resolveVerificationForUi(text);
        setAnswers((list) => [...list, answer]);
      } finally {
        setBusy(false);
      }
    })();
  };

  const handleTrapKey = (event: KeyEvent) => {
    if (event.name === "escape") {
      event.preventDefault();
      focusInput(false);
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      submit();
      return;
    }
    if (event.name === "backspace" || event.name === "delete") {
      event.preventDefault();
      setDraftBoth(draft().slice(0, -1));
      return;
    }
    // Printable insert — KeyTrap must not forward these to the global keymap.
    if (!event.ctrl && !event.meta) {
      const ch = keyTrapInsertChar(event);
      if (!ch) return;
      event.preventDefault();
      setDraftBoth(draft() + ch);
    }
  };

  const displayLine = () => {
    const body = windowedDraft(draft(), verifyInputCols());
    const caret = inputFocused() ? "█" : "";
    return `› ${body}${caret}`;
  };

  return (
    <Panel
      width="30%"
      flexGrow={0}
      flexShrink={0}
      height="100%"
      minHeight={12}
      alignSelf="stretch"
      accent={
        workspace.researchFocus === "verify" || inputFocused()
          ? palette.borderFocus
          : palette.accent2
      }
      title="Live verify"
      tone={workspace.researchFocus === "verify" || inputFocused() ? "raised" : "base"}
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <Show when={inputFocused() || workspace.researchFocus === "verify"}>
        <Line fg={palette.fgDim}>
          Enter ask · Esc
        </Line>
      </Show>
      <scrollbox
        flexGrow={1}
        flexShrink={1}
        minHeight={3}
        stickyScroll
        stickyStart="bottom"
        scrollY
        overflow="hidden"
        width="100%"
      >
        <For each={answers().slice(-3)}>
          {(answer) => <VerificationAnswerView answer={answer} />}
        </For>
        <Show when={answers().length === 0 && !busy()}>
          <Line fg={palette.fgDim}>Ask why / support / contradict…</Line>
        </Show>
        <Show when={busy()}>
          <Line fg={palette.fgDim}>Verifying…</Line>
        </Show>
      </scrollbox>
      <box
        height={1}
        width="100%"
        flexShrink={0}
        marginTop={1}
        overflow="hidden"
        backgroundColor={palette.msgUserBg}
        paddingLeft={1}
        onMouseDown={() => focusInput(true)}
      >
        <text fg={inputFocused() ? palette.fgBright : palette.fg}>
          {truncate(displayLine(), verifyInputCols() + 4)}
        </text>
        {/* Absolute trap — owns focus; never takes Yoga row space */}
        <KeyTrap
          paneScoped
          focused={inputFocused()}
          captureText
          onKeyDown={handleTrapKey}
          onPaste={(text) => {
            if (!inputFocused() || busy()) return;
            const cleaned = text.replace(/\r?\n/g, " ");
            setDraftBoth(draft() + cleaned);
          }}
        />
      </box>
    </Panel>
  );
}
