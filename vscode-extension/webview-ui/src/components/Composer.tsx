import { useEffect, useRef, useState } from 'react';
import { cancel, compactSession, mgmtCardQuery, sendMessage, setModel } from '@/lib/actions';
import { formatCostChip, formatCostTooltip } from '@/lib/cost-chip';
import { filterSlashSuggestions, resolveMgmtVerb } from '@/lib/mgmt-slash';
import { escStep } from '@/lib/esc-ladder';
import type { MgmtCardKind } from '@/types/protocol';
import { useApp, useAppDispatch, useCurrentSession } from '@/state/store';
import { useChips, useMentions } from './composer-assist';

/** Max auto-growth height for the composer textarea (px). */
const TEXTAREA_MAX_HEIGHT = 170;

/** Compact formatter: 123456 → '123.5k'. */
function formatTokens(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

let mgmtRequestSeq = 0;

/** While a turn runs, only the bridge backend takes a message (it queues it,
 * VP-2b); a slash line waits for the turn on every backend. */
function canSubmit(body: string, running: boolean, queueing: boolean): boolean {
  if (!body) return false;
  if (!running) return true;
  return queueing && !body.startsWith('/');
}

export default function Composer() {
  const { currentId, endpoints, runStates, usage, costs, history, transcripts, editorContext, asks, extras } = useApp();
  const dispatch = useAppDispatch();
  const session = useCurrentSession();
  const [text, setText] = useState('');
  // VP-2: a turn refused before it ran hands its text back (session_conflict
  // stage before_turn); only into an empty composer, never over a new draft.
  // VP-2b: Edit on a message (`force`) replaces the draft on purpose.
  const restored = asks.restoreDraft;
  useEffect(() => {
    if (!restored || restored.sessionId !== currentId) return;
    setText((draft) => (draft.trim() && !restored.force ? draft : restored.text));
    if (restored.force) textareaRef.current?.focus();
  }, [restored?.seq]);
  // A3: navigation cursor into the shared history tape — null = free edit.
  const [histIndex, setHistIndex] = useState<number | null>(null);
  // Model chosen before the first message, when no session exists yet.
  const [pendingModel, setPendingModel] = useState('');
  // A2: slash autocomplete popup state; Esc dismisses until the text changes.
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // VP-2b part 2: `@` mentions and paste chips (the console's rules).
  const chips = useChips();
  const mention = useMentions(text, setText, chips.trackMention);
  const queueing = extras.backend === 'bridge';

  const slashMatches = filterSlashSuggestions(text, extras.slashCatalog);
  // A2: the slash popup owns ↑/↓/Tab/Enter/Esc while open.
  const SLASH_KEYS: Record<string, () => void> = {
    ArrowUp: () => setSlashIndex((i) => Math.max(0, i - 1)),
    ArrowDown: () => setSlashIndex((i) => Math.min(slashMatches.length - 1, i + 1)),
    Tab: () => completeSlash(safeSlashIndex),
    Enter: () => completeSlash(safeSlashIndex),
    Escape: () => setSlashDismissed(true),
  };

  /** A3 history navigation — same tape the console/TUI walk. Armed only while
   * the draft is empty or a nav is in flight. True when the key was used. */
  function historyKey(key: string): boolean {
    if (key === 'ArrowUp') return historyBack();
    return key === 'ArrowDown' && historyForward();
  }

  function historyBack(): boolean {
    if ((text !== '' && histIndex === null) || history.length === 0) return false;
    const next = histIndex === null ? history.length - 1 : Math.max(0, histIndex - 1);
    setHistIndex(next);
    setText(history[next] ?? '');
    return true;
  }

  function historyForward(): boolean {
    if (histIndex === null) return false;
    const next = histIndex + 1;
    const past = next >= history.length;
    setHistIndex(past ? null : next);
    setText(past ? '' : history[next] ?? '');
    return true;
  }

  /** The composer's keys: popups first, then the Esc ladder, history, send. */
  function onComposerKey(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (mention.onKeyDown(e)) return;
    const action = keyAction(e);
    if (!action) return;
    e.preventDefault();
    action();
  }

  /** What a key does here, or null to let the textarea have it. */
  function keyAction(e: React.KeyboardEvent<HTMLTextAreaElement>): (() => void) | null {
    const slash = slashOpen ? SLASH_KEYS[e.key] : undefined;
    if (slash) return slash;
    if (e.key === 'Escape') return onEscape;
    if (historyKey(e.key)) return () => undefined;
    return e.key === 'Enter' && !e.shiftKey ? submit : null;
  }

  // VP-2d: the Esc ladder (stop a reply; Esc Esc on an empty composer rewinds).
  const escArmedAt = useRef(0);
  function onEscape(): void {
    const step = escStep({ running, empty: !text.trim(), canRewind: queueing, armedAt: escArmedAt.current, now: Date.now() });
    escArmedAt.current = step.armedAt;
    if (step.action === 'stop') cancel(currentId);
    else if (step.action === 'rewind') sendMessage('/rewind', currentId);
  }
  const slashOpen = slashMatches.length > 0 && !slashDismissed;
  const safeSlashIndex = Math.min(slashIndex, Math.max(0, slashMatches.length - 1));

  // A2: completion only — mgmt verbs need a sub-verb, locals just complete.
  function completeSlash(index: number): void {
    const chosen = slashMatches[index];
    if (!chosen) return;
    setText(chosen.runs === 'mgmt' ? `/${chosen.name} ` : `/${chosen.name}`);
    setSlashIndex(0);
    textareaRef.current?.focus();
  }

  const running = currentId ? runStates[currentId] === 'running' : false;
  const usageInfo = currentId ? usage[currentId] : undefined;
  const costInfo = currentId ? costs[currentId] : undefined;
  const selectedModel = currentId ? (session?.model ?? '') : pendingModel;

  // Keep a session's model selectable even if its endpoint is no longer ready.
  const options = endpoints.some((e) => e.id === selectedModel) || !selectedModel
    ? endpoints
    : [...endpoints, { id: selectedModel, label: `${selectedModel} (offline)`, status: 'unknown' }];

  // Auto-pick the first available model so a chat never goes out without one
  // (otherwise the agent replies with "No model selected" instead of answering).
  useEffect(() => {
    if (!selectedModel && options.length > 0 && options[0].id) {
      onModelChange(options[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, selectedModel]);

  function onModelChange(value: string): void {
    if (currentId) {
      setModel(currentId, value || undefined);
    } else {
      setPendingModel(value);
    }
  }

  function submit(): void {
    const body = text.trim();
    if (!canSubmit(body, running, queueing)) return;
    // C4: /undo — client-side prompt-text restore ONLY (plan §8: server
    // session.revert stays parked). Pops the last user message and everything
    // after it from the transcript, restores its text to the composer.
    if (body === '/undo' && currentId) {
      const entries = transcripts[currentId] ?? [];
      const idx = [...entries].reverse().findIndex((entry) => entry.kind === 'msg' && entry.role === 'You');
      if (idx === -1) {
        setText('');
        textareaRef.current?.focus();
        return;
      }
      const cutFrom = entries.length - idx - 1;
      const undoText = entries[cutFrom]?.text ?? '';
      dispatch({ type: 'ui/discardFrom', sessionId: currentId, index: cutFrom });
      setText(undoText);
      setHistIndex(null);
      textareaRef.current?.focus();
      return;
    }
    // C4: /compact — on-demand conversation compaction via the ACP verb.
    if (body.startsWith('/compact') && currentId) {
      const arg = body.slice('/compact'.length).trim();
      const keep = arg ? Math.max(1, Math.floor(Number(arg)) || 6) : undefined;
      compactSession(currentId, keep);
      setText('');
      setHistIndex(null);
      textareaRef.current?.focus();
      return;
    }
    // M5: management slash verbs run through the bridge query path, never into
    // the model's context; the card answer lands in the transcript.
    const verb = resolveMgmtVerb(body);
    if (verb && currentId) {
      const requestId = `mgmt-${Date.now()}-${++mgmtRequestSeq}`;
      dispatch({ type: 'ui/mgmtCardQueued', requestId, sessionId: currentId, kind: verb.kind, at: new Date().toISOString() });
      mgmtCardQuery(requestId, currentId, verb.kind, verb.arg);
      setText('');
      setHistIndex(null);
      textareaRef.current?.focus();
      return;
    }
    sendMessage(chips.expandForSend(body), currentId, selectedModel || undefined);
    setText('');
    setHistIndex(null);
    textareaRef.current?.focus();
  }

  function summon(kind: MgmtCardKind, arg?: string): void {
    if (!currentId || running) return;
    const requestId = `mgmt-${Date.now()}-${++mgmtRequestSeq}`;
    dispatch({ type: 'ui/mgmtCardQueued', requestId, sessionId: currentId, kind, at: new Date().toISOString() });
    mgmtCardQuery(requestId, currentId, kind, arg);
  }

  // Auto-grow the composer up to a cap, then let it scroll. Runs on every
  // keystroke and after submit (text cleared → shrink back to one row).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)}px`;
  }, [text]);

  // Focus the composer when the viewed session changes (new chat / switch).
  useEffect(() => {
    textareaRef.current?.focus();
  }, [currentId]);

  return (
    <div className="r4-composer">
      <div className="r4-composer-row">
        <select
          className="r4-select"
          value={selectedModel}
          onChange={(e) => onModelChange(e.target.value)}
          title="Model used for this chat"
          aria-label="Chat model"
        >
          <option value="">Default model</option>
          {options.map((endpoint) => (
            <option key={endpoint.id} value={endpoint.id}>
              {endpoint.label}
            </option>
          ))}
        </select>
      </div>

      {/* D5: editor-context chip — follows the active editor cursor. */}
      {editorContext ? (
        <div className="r4-editor-context-chip" title="Active editor context">
          <span aria-hidden>📎</span> {editorContext}
        </div>
      ) : null}
      {/* VP-2b part 2: `@` file mentions (↑↓, Tab/Enter inserts, Esc closes). */}
      {mention.open ? (
        <div className="r4-slash-popup" role="listbox" aria-label="Workspace files">
          {mention.items.map((path, idx) => (
            <div
              key={path}
              role="option"
              aria-selected={idx === mention.index}
              className={`r4-slash-option${idx === mention.index ? ' selected' : ''}`}
              onMouseDown={(e) => {
                e.preventDefault();
                mention.accept(idx);
              }}
            >
              <span className="r4-slash-name">@{path}</span>
            </div>
          ))}
        </div>
      ) : null}
      {/* A2: slash autocomplete popup — completion only (Tab/Enter). */}
      {slashOpen ? (
        <div className="r4-slash-popup" role="listbox" aria-label="Slash commands">
          {slashMatches.map((s, idx) => (
            <div
              key={s.name}
              role="option"
              aria-selected={idx === safeSlashIndex}
              className={`r4-slash-option${idx === safeSlashIndex ? ' selected' : ''}`}
              onMouseDown={(e) => {
                e.preventDefault();
                completeSlash(idx);
              }}
            >
              <span className="r4-slash-name">/{s.name}</span>
              <span className="r4-slash-hint">{s.hint}</span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="r4-composer-row">
        <textarea
          ref={textareaRef}
          className="r4-textarea r4-textarea-autogrow"
          rows={2}
          value={text}
          placeholder={running ? (queueing ? 'Queue a message for after this reply…' : 'Waiting for the current reply…') : 'Ask the R4 agent…'}
          onChange={(e) => {
            setText(chips.onEdit(text, e.target.value));
            setHistIndex(null);
            setSlashDismissed(false);
          }}
          onPaste={(e) => chips.onPaste(e, text, setText)}
          onKeyDown={onComposerKey}
        />
      </div>

      {/* M5: management verb quick row — one click summons the card inline.
           Same verbs (and answers) as the TUIs / agents' tool calls. */}
      <div className="r4-composer-row r4-mgmt-chips">
        <button type="button" className="r4-chip" onClick={() => summon('wf-list')} title="List UMRS workflows">/workflow list</button>
        <button type="button" className="r4-chip" onClick={() => summon('loop-list')} title="List research loops">/loop list</button>
        <button type="button" className="r4-chip" onClick={() => summon('session-view')} title="View the research session">/session view</button>
        <button type="button" className="r4-chip" onClick={() => summon('knowledge-status')} title="Knowledge graph status">/knowledge</button>
      </div>

      <div className="r4-composer-row r4-composer-actions">
        <span className="r4-hint">Enter to send · Shift+Enter for a new line</span>
        {costInfo ? (
          <span
            className={`r4-usage-chip${(costInfo.context_used_tokens / Math.max(1, costInfo.context_limit_tokens)) > 0.8 ? ' r4-usage-chip-warn' : ''}`}
            title={formatCostTooltip(costInfo)}
          >
            {formatCostChip(costInfo)}
          </span>
        ) : usageInfo ? (
          <span
            className={`r4-usage-chip${usageInfo.total > 100_000 ? ' r4-usage-chip-warn' : ''}`}
            title={`${usageInfo.prompt.toLocaleString()} in · ${usageInfo.completion.toLocaleString()} out`}
          >
            ⚡ {formatTokens(usageInfo.total)}
          </span>
        ) : null}
        {running ? (
          <button
            type="button"
            className="r4-btn r4-btn-stop"
            onClick={() => cancel(currentId)}
            title="Stop the current reply"
          >
            ■ Stop
          </button>
        ) : (
          <button
            type="button"
            className="r4-btn r4-btn-primary r4-btn-send"
            onClick={submit}
            disabled={!text.trim()}
            title="Send message"
          >
            Send <span className="r4-send-arrow" aria-hidden="true">➤</span>
          </button>
        )}
      </div>
    </div>
  );
}
