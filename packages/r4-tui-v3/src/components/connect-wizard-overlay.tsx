/**
 * Connect wizard overlay — the /connect menu's Q&A form.
 *
 * Asks one question at a time (login: the token; public source: name → url
 * → key → provider → description, plus a G-4 opt-in when a key would travel
 * plain http), then dispatches the built slash line through
 * dispatchSlashFromChat, so the transcript shows the same masked echo a
 * typed line would and every secret rides r4's stdin (S4 — the key is never
 * on the command line, in the transcript, or in prompt history).
 *
 * Over-long input is REFUSED, never silently capped: a 512-char silent cap
 * once mangled every pasted JWT longer than it, storing a broken credential
 * while login reported success (field report 2026-10-20). A secret's masked
 * echo carries its character count, so the pasted length stays verifiable.
 *
 * KeyTrap + windowed draft like the ask-user overlay (no OpenTUI <input> —
 * the cursor bleeds onto the border, and it would show the key unmasked).
 */

import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";
import {
  buildWizardLine,
  connectQuestions,
  CONNECT_WIZARD_TITLE,
  INSECURE_HTTP_REFUSED,
  PROSE_DRAFT_MAX,
  refusesInsecureHttp,
  type ConnectFlow,
  type ConnectQuestion,
} from "../lib/connect-wizard";
import { SECRET_MASK } from "../lib/source-add-args";
import { KeyTrap, keyTrapWouldInsert, keyTrapInsertChar } from "./key-trap";
import { Floating } from "../ui/Floating";

const cap = () => Math.max(60, Math.floor(termWidth() * 0.7) - 8);

function windowed(text: string, cols: number): string {
  return text.length <= cols ? text : `…${text.slice(-(cols - 1))}`;
}

/** What the input row shows: per-char • for a secret + its length (the cap
 *  that used to eat a pasted JWT was invisible without it), the text
 *  otherwise. */
function displayDraft(question: ConnectQuestion, draft: string): string {
  if (!question.secret || !draft) return windowed(draft, cap() - 4);
  const masked = `${"•".repeat(Math.min(draft.length, 24))} (${draft.length} chars)`;
  return windowed(masked, cap() - 4);
}

/** Why `value` cannot answer `question`, or null. */
function answerProblem(question: ConnectQuestion, value: string): string | null {
  if (!value) return question.required ? "required — paste a value, or Esc to cancel" : null;
  return question.validate?.(value) ?? null;
}

export function ConnectWizardOverlay() {
  const wizard = () => workspace.connectWizard;
  const flow = (): ConnectFlow => wizard()?.flow ?? "login";
  const [step, setStep] = createSignal(0);
  const [answers, setAnswers] = createSignal<string[]>([]);
  const [draft, setDraft] = createSignal("");
  const [error, setError] = createSignal<string | null>(null);
  /** Registered source names — the name question refuses them (connect#4). */
  const [taken, setTaken] = createSignal<string[]>([]);
  // answers decide the conditional tail (the http G-4 question appears only
  // once url+key say a credential would travel plain http).
  const questions = createMemo(() => connectQuestions(flow(), answers(), taken()));

  /** Best effort: a failed listing leaves the check to r4, which keeps the
   *  existing row's fields on an update. */
  const loadTakenNames = async () => {
    try {
      const { fetchSources } = await import("../lib/sources");
      setTaken((await fetchSources()).map((row) => row.name));
    } catch {
      setTaken([]);
    }
  };

  createEffect(() => {
    if (wizard()) {
      setStep(0);
      setAnswers([]);
      setDraft("");
      setError(null);
      setTaken([]);
      if (wizard()?.flow === "source") void loadTakenNames();
    }
  });

  const current = () => questions()[step()];
  const close = () => actions.closeConnectWizard();

  /** Grow the draft, refusing with a loud error past the question's cap —
   *  a silently capped token was stored broken and only the CLI login
   *  worked (2026-10-20 report). */
  const growDraft = (piece: string) => {
    const next = draft() + piece;
    const limit = current()?.maxDraft ?? PROSE_DRAFT_MAX;
    if (next.length > limit) {
      setError(`too long — ${next.length} characters, max ${limit}; nothing added`);
      return;
    }
    setDraft(next);
    setError(null);
  };

  const submitStep = () => {
    const question = current();
    if (!question) return;
    const value = draft().trim();
    const problem = answerProblem(question, value);
    if (problem) {
      setError(problem);
      return;
    }
    const next = [...answers(), value];
    if (step() + 1 < questions().length) {
      setAnswers(next);
      setStep(step() + 1);
      setDraft("");
      setError(null);
      return;
    }
    if (refusesInsecureHttp(flow(), next)) {
      // Nothing runs: back to the URL question, the name kept.
      setAnswers(next.slice(0, 1));
      setStep(1);
      setDraft("");
      setError(INSECURE_HTTP_REFUSED);
      return;
    }
    // Build BEFORE close(): close() nulls workspace.connectWizard, so flow()
    // falls back to "login" — every "Add public endpoint" run was dispatched
    // as `/login <source name>`, overwriting the stored login with the name
    // and registering nothing (field report 2026-10-21).
    const line = buildWizardLine(flow(), next);
    close();
    void actions.dispatchSlashFromChat(line);
  };

  const handleKey = (event: KeyEvent) => {
    if (event.name === "escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      submitStep();
      return;
    }
    if (event.name === "backspace" || event.name === "delete") {
      event.preventDefault();
      setDraft((d) => d.slice(0, -1));
      setError(null);
      return;
    }
    if (keyTrapWouldInsert(event) && !event.ctrl && !event.meta) {
      event.preventDefault();
      const ch = keyTrapInsertChar(event);
      if (!ch) return;
      growDraft(ch);
    }
  };

  const onPaste = (text: string) => {
    const question = current();
    // A secret is one token: whitespace (incl. the paste's newlines) never
    // belongs to it; prose keeps it as a single space.
    const cleaned = question?.secret ? text.replace(/\s+/g, "") : text.replace(/\r?\n/g, " ");
    growDraft(cleaned);
  };

  const hint = () =>
    error() ??
    (step() + 1 === questions().length ? "Enter runs it · Esc cancels" : "Enter next question · Esc cancels");

  const boxHeight = () => 6 + step() + (current()?.hint ? 1 : 0) + (error() ? 1 : 0);

  return (
    <Show when={wizard()}>
      <Floating
        position="absolute"
        width="70%"
        height={boxHeight()}
        left="15%"
        top="25%"
        title={CONNECT_WIZARD_TITLE[flow()]}
        flexDirection="column"
        padding={1}
        gap={0}
        overflow="hidden"
      >
        <For each={answers()}>
          {(answer, i) => {
            const question = () => questions()[i()];
            return (
              <box height={1} width="100%" overflow="hidden">
                <text fg={palette.fgDim}>
                  {truncate(
                    `✓ ${question()?.prompt ?? ""} → ${question()?.secret ? SECRET_MASK : answer}`,
                    cap(),
                  )}
                </text>
              </box>
            );
          }}
        </For>
        <box height={1} width="100%" overflow="hidden" marginTop={answers().length > 0 ? 1 : 0}>
          <text fg={palette.fgBright}>{truncate(current()?.prompt ?? "", cap())}</text>
        </box>
        <Show when={current()?.hint}>
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.fgDim}>{truncate(current()!.hint!, cap())}</text>
          </box>
        </Show>
        <box
          height={1}
          width="100%"
          flexShrink={0}
          overflow="hidden"
          backgroundColor={palette.msgUserBg}
          paddingLeft={1}
        >
          <text fg={draft().length > 0 ? palette.fgBright : palette.fgDim}>
            {truncate(
              `› ${displayDraft(
                current() ?? { prompt: "", secret: false, required: false, key: "", maxDraft: PROSE_DRAFT_MAX },
                draft(),
              )}█`,
              cap(),
            )}
          </text>
          <KeyTrap focused captureText onKeyDown={handleKey} onPaste={onPaste} />
        </box>
        <box height={1} width="100%" overflow="hidden">
          <text fg={error() ? palette.statusErr : palette.fgDim}>
            {truncate(`${step() + 1}/${questions().length} · ${hint()}`, cap())}
          </text>
        </box>
      </Floating>
    </Show>
  );
}
