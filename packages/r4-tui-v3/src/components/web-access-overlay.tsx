/**
 * Web-access question (ADR-099 D-2, maintainer decision M-4: "accessibility
 * from the agent need to be selected by users … when opening a session").
 *
 * Off · Allowlist (with domains) · Public. Opened right after the venue
 * choice of a new session (local or remote), on the first submit of a
 * session that has no choice yet, and by `/web`. A remote question offers
 * only what the server's caps allow (`session.venue_policy`); the current or
 * remembered choice is preselected, never applied without the question.
 *
 * ↑↓ choose · type the domains on the Allowlist row · Enter confirm · Esc cancel.
 */

import { For, Show, createMemo, createSignal, onMount } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { actions, activeTab, heldMessages, returnHeld, workspace, type WebAccessPrompt } from "../stores";
import { applyOnTab } from "../tabs/registry";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";
import { useTerminalDims } from "../lib/terminal-dims";
import { KeyTrap, keyTrapInsertChar, keyTrapWouldInsert } from "./key-trap";
import { Floating } from "../ui/Floating";
import {
  NO_CAPS,
  REMOTE_NOTE,
  WEB_MODE_TEXT,
  localNote,
  noteRows,
  validateWebChoice,
  webAccessLabel,
  webPromptDefaults,
  type WebCaps,
  type WebChoice,
  type WebMode,
} from "../lib/web-access";
import {
  fetchSessions,
  fetchVenuePolicy,
  fetchWebAccess,
  newSession,
  setSessionWebAccess,
  type WebAccessState,
} from "../bridge/session-bridge";

const DOMAINS_MAX = 2000;

/** Verbs the answer drives (injectable for tests). */
export interface WebChoiceDeps {
  newSession: typeof newSession;
  setSessionWebAccess: typeof setSessionWebAccess;
  fetchSessions: () => unknown;
}

const DEFAULT_DEPS: WebChoiceDeps = { newSession, setSessionWebAccess, fetchSessions };

type ChoiceHandler = (prompt: WebAccessPrompt, choice: WebChoice, deps: WebChoiceDeps) => Promise<boolean>;

/** New local session: the answer rides the initialize call that creates it. */
const startLocal: ChoiceHandler = async (prompt, choice, deps) => {
  const ok = await deps.newSession(prompt.venue ?? "local", choice);
  if (ok) void deps.fetchSessions();
  return ok;
};

/** New remote session: the source picker creates it with the answer and starts it. */
const startRemote: ChoiceHandler = async (_prompt, choice) => {
  actions.setPendingRemoteWebAccess(choice);
  actions.openRemoteSourcePicker("start");
  return true;
};

/** /venue start on a session that had no choice: store it, then the picker. */
const startCurrent: ChoiceHandler = async (_prompt, choice, deps) => {
  if (!(await deps.setSessionWebAccess(choice))) return false;
  actions.openRemoteSourcePicker("current");
  return true;
};

/** First submit: store the answer, then send the held message. On failure
 * the held text returns as an unanswered question's does (`returnHeld`):
 * never over a draft typed during the call, never into another tab's
 * composer (RS-1 round 4, web R4-1). */
const sendHeld: ChoiceHandler = async (prompt, choice, deps) => {
  const held = heldMessages(prompt);
  const tabId = workspace.activeChatTabId;
  const ok = await deps.setSessionWebAccess(choice);
  if (held.length === 0) return ok;
  if (ok) for (const text of held) actions.sendChatMessage(text, { relay: prompt.pendingRelay === true });
  else {
    const stillHere = workspace.activeChatTabId === tabId;
    applyOnTab(tabId, () => returnHeld(prompt, stillHere));
  }
  return ok;
};

/** /web: store the answer; locally it applies to the agent's next web call. */
const change: ChoiceHandler = async (_prompt, choice, deps) => {
  const ok = await deps.setSessionWebAccess(choice);
  if (ok) actions.sendSystemNotice(`web access: ${webAccessLabel(choice)} — applies to the agent's next web call`);
  return ok;
};

const CHOICE_HANDLERS: Record<WebAccessPrompt["purpose"], ChoiceHandler> = {
  "new-local": startLocal,
  "new-remote": startRemote,
  "venue-current": startCurrent,
  "first-submit": sendHeld,
  slash: change,
};

/** Act on the user's answer, per why the question was asked. */
export function applyWebChoice(
  prompt: WebAccessPrompt,
  choice: WebChoice,
  deps: WebChoiceDeps = DEFAULT_DEPS,
): Promise<boolean> {
  return CHOICE_HANDLERS[prompt.purpose](prompt, choice, deps);
}

/** The caps the question honours: the server's for a remote session, none locally. */
function policyFor(open: WebAccessPrompt | null): Promise<{ caps: WebCaps; note: string }> {
  return open?.remote ? fetchVenuePolicy() : Promise.resolve({ caps: NO_CAPS, note: "" });
}

/** Where the question starts. A new session has no choice of its own yet,
 * so only /web starts from the session's current one. */
function startingPoint(open: WebAccessPrompt | null, state: WebAccessState | null): WebAccessState {
  const fresh: WebAccessState = { current: null, remembered: state ? state.remembered : null, preset: false };
  const ownChoice = state !== null && open !== null && open.purpose === "slash";
  return ownChoice ? state : fresh;
}

const cap = () => Math.max(40, Math.floor(termWidth() * 0.64) - 4);

/** One mode row: its label, and below it the consequence — or, on the
 * focused Allowlist row, the domains being typed. */
function ModeRow(props: { mode: WebMode; focused: boolean; domains: string; onPick: () => void }) {
  const second = () =>
    props.mode === "allowlist" && props.focused ? `domains: ${props.domains}█` : WEB_MODE_TEXT[props.mode].consequence;
  const marker = () => (props.focused ? "▸" : " ");
  return (
    <box flexDirection="column" flexShrink={0}>
      <box
        height={1}
        width="100%"
        overflow="hidden"
        backgroundColor={props.focused ? palette.bgHover : undefined}
        onMouseDown={props.onPick}
      >
        <text fg={props.focused ? palette.fgBright : palette.fg}>
          {truncate(`${marker()} ${WEB_MODE_TEXT[props.mode].label}`, cap())}
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden" paddingLeft={4}>
        <text fg={palette.fgDim}>{truncate(second(), cap() - 4)}</text>
      </box>
    </box>
  );
}

export function WebAccessOverlay(props: { deps?: WebChoiceDeps }) {
  const prompt = () => workspace.webAccessPrompt;
  const [caps, setCaps] = createSignal(NO_CAPS);
  const [loading, setLoading] = createSignal(true);
  const [cursor, setCursor] = createSignal(0);
  const [domains, setDomains] = createSignal("");
  const [footer, setFooter] = createSignal<string | null>(null);
  const [busy, setBusy] = createSignal(false);
  const dims = useTerminalDims();

  const modes = createMemo((): WebMode[] => caps().allow);
  const selected = (): WebMode | undefined => modes()[cursor()];

  onMount(() => {
    void (async () => {
      const [state, policy] = await Promise.all([fetchWebAccess(), policyFor(prompt())]);
      setCaps(policy.caps);
      if (policy.note) setFooter(policy.note);
      const seed = startingPoint(prompt(), state);
      const defaults = webPromptDefaults(seed.current, seed.remembered, policy.caps);
      setCursor(Math.max(0, policy.caps.allow.indexOf(defaults.mode)));
      setDomains(defaults.domainsText);
      setLoading(false);
    })();
  });

  const title = () =>
    prompt()?.remote
      ? "web access for the agent in this remote session"
      : "web access for the agent in this session";

  const confirm = () => {
    const mode = selected();
    const open = prompt();
    if (!mode || !open || busy() || loading()) return;
    const { choice, error } = validateWebChoice(mode, domains(), caps());
    if (!choice) {
      setFooter(error);
      return;
    }
    setBusy(true);
    actions.closeWebAccessPrompt({ sent: true });
    void applyWebChoice(open, choice, props.deps ?? DEFAULT_DEPS);
  };

  const onDomainKeys = (event: KeyEvent): boolean => {
    if (selected() !== "allowlist") return false;
    if (event.name === "backspace" || event.name === "delete") {
      setDomains((d) => d.slice(0, -1));
      return true;
    }
    if (!keyTrapWouldInsert(event) || event.ctrl || event.meta) return false;
    const ch = keyTrapInsertChar(event);
    if (ch) setDomains((d) => (d + ch).slice(0, DOMAINS_MAX));
    return true;
  };

  const onNavKeys = (event: KeyEvent): boolean => {
    const name = String(event.name ?? "");
    if (name === "up" || name === "down") {
      const step = name === "up" ? -1 : 1;
      setCursor((c) => Math.min(Math.max(0, c + step), Math.max(0, modes().length - 1)));
      setFooter(null);
      return true;
    }
    if (name === "return" || name === "enter") {
      confirm();
      return true;
    }
    if (name === "escape") {
      actions.closeWebAccessPrompt();
      return true;
    }
    return false;
  };

  const onPaste = (text: string) => {
    if (selected() === "allowlist") setDomains((d) => (d + text.replace(/\r?\n/g, ",")).slice(0, DOMAINS_MAX));
  };

  const onKey = (event: KeyEvent) => {
    if (onNavKeys(event) || onDomainKeys(event)) event.preventDefault();
  };

  // ADR-101 D-6: the local note follows this tab's sandbox.
  const note = () => (prompt()?.remote ? REMOTE_NOTE : localNote(activeTab().sandbox));
  // The note's second row only when it wraps (the remote note fits on one;
  // tui-lane review 2026-10-30: an always-on second row left a blank line).
  const noteLines = () => noteRows(note(), cap());
  const rows = () => 8 + (noteLines()[1] ? 1 : 0) + modes().length * 2;
  const boxTop = () => Math.max(0, Math.min(Math.floor(dims().height * 0.22), dims().height - rows()));

  const hint = () => {
    if (busy()) return "applying…";
    if (footer()) return footer()!;
    if (loading()) return "checking what this session may choose…";
    return selected() === "allowlist"
      ? "type domains (comma-separated) · ↑↓ choose · Enter confirm · Esc cancel"
      : "↑↓ choose · Enter confirm · Esc cancel";
  };

  return (
    <Floating
      position="absolute"
      width="64%"
      height={rows()}
      left="18%"
      top={boxTop()}
      title={title()}
      flexDirection="column"
      padding={1}
    >
      <box flexDirection="column" flexGrow={1} overflow="hidden">
        <For each={modes()}>
          {(mode, idx) => (
            <ModeRow mode={mode} focused={idx() === cursor()} domains={domains()} onPick={() => setCursor(idx())} />
          )}
        </For>
        <Show when={!loading() && modes().length === 0}>
          <box height={1}>
            <text fg={palette.statusWarn}>the session policy allows no web access mode here</text>
          </box>
        </Show>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>{noteLines()[0]}</text>
      </box>
      <Show when={noteLines()[1]}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{truncate(noteLines()[1], cap())}</text>
        </box>
      </Show>
      <box height={1} width="100%" overflow="hidden">
        <text fg={footer() ? palette.statusWarn : palette.fgDim}>{truncate(hint(), cap())}</text>
      </box>
      <box height={1} width={1} overflow="hidden">
        <KeyTrap
          focused
          captureText
          onKeyDown={onKey}
          onPaste={onPaste}
        />
      </box>
    </Floating>
  );
}
