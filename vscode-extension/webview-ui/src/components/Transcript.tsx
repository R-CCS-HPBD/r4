import { memo, useEffect, useRef, useState } from 'react';
import { thinkingText, useApp, type SessionEdit, type ThinkingTrace, type ToolCallCard as ToolCallCardModel } from '@/state/store';
import { acceptEdit, openFile, rejectEdit, requestToolResult, reviewEdit } from '@/lib/actions';
import { MGMT_VERB_PREFIX } from '@/lib/mgmt-slash';
import MessageBubble from './MessageBubble';
import { isFinalAnswer, turnOfEntry } from '@/lib/turn-of-entry';
import type { TranscriptEntry } from '@/state/store';
import type { ViewCitation } from '@/types/protocol';

/** VP-2b: a message's turn, and an answer's citations (its turn's final answer only). */
function entryTurnProps(
  entries: TranscriptEntry[],
  index: number,
  citations: Record<string, ViewCitation[]>,
): { turnId?: string; citations?: ViewCitation[] } {
  const entry = entries[index];
  if (entry.kind !== 'msg') return {};
  const turnId = turnOfEntry(entries, index);
  if (!turnId) return {};
  if (entry.role === 'You') return { turnId };
  return isFinalAnswer(entries, index) ? { turnId, citations: citations[turnId] } : {};
}
import Mascot from './Mascot';
import MgmtCardRow from './MgmtCardRow';

/** Rotating cognition verbs shown while the agent works with no stream yet. */
const THINKING_VERBS = [
  'Thinking',
  'Reading context',
  'Reasoning',
  'Connecting the dots',
  'Gathering facts',
];

function ThinkingIndicator() {
  const [verbIndex, setVerbIndex] = useState(0);

  useEffect(() => {
    const timer = setInterval(
      () => setVerbIndex((i) => (i + 1) % THINKING_VERBS.length),
      2200,
    );
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="r4-typing" aria-live="polite">
      <Mascot state="thinking" size={16} />
      <span className="r4-typing-dot" />
      <span className="r4-typing-dot" />
      <span className="r4-typing-dot" />
      <span key={verbIndex} className="r4-typing-label r4-verb-fade">
        {`${THINKING_VERBS[verbIndex]}…`}
      </span>
    </div>
  );
}

interface ToolCallCardProps {
  id: string;
  name: string;
  status: 'pending' | 'completed' | 'error';
  arguments?: Record<string, unknown>;
  argsSummary?: string;
  resultPreview?: string;
  durationMs?: number;
  /** Round-3 task 2: preview was bounded; full result loads on expand. */
  truncated?: boolean;
  /** Round-3 task 2: full result fetched from the session journal. */
  fullResult?: string;
  /** Session for the expand fetch. */
  sessionId?: string;
}

/** Argument keys that commonly carry a file path in tool calls. */
const PATH_ARG_KEYS = ['path', 'file', 'file_path', 'filepath', 'filename', 'target_file'];
const FILE_EXT_RE = /\.(py|ts|tsx|js|jsx|mjs|cjs|json|ya?ml|md|txt|sh|bash|toml|rs|go|c|h|cpp|ipynb|sql|csv)$/i;

/** Pull clickable file paths out of tool-call arguments (deduped, capped at 4). */
function extractFilePaths(args?: Record<string, unknown>): string[] {
  if (!args) return [];
  const found: string[] = [];
  for (const key of PATH_ARG_KEYS) {
    const value = args[key];
    if (typeof value === 'string' && FILE_EXT_RE.test(value) && !found.includes(value)) {
      found.push(value);
    }
  }
  return found.slice(0, 4);
}

function ToolCallCard({
  id,
  name,
  status,
  arguments: args,
  argsSummary,
  resultPreview,
  durationMs,
  truncated,
  fullResult,
  sessionId,
}: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const filePaths = extractFilePaths(args);
  const statusLabel = status === 'pending' ? 'Running…' : status === 'error' ? 'Failed' : 'Done';
  const badgeClass =
    status === 'pending'
      ? 'r4-toolcard-badge r4-badge-warn'
      : status === 'error'
        ? 'r4-toolcard-badge r4-badge-error'
        : 'r4-toolcard-badge r4-badge-ok';

  const body = fullResult ?? resultPreview;
  const expandable = truncated || Boolean(resultPreview && resultPreview.length > 300);
  const handleToggle = () => {
    setExpanded((v) => {
      const next = !v;
      // First open of a bounded card: fetch the full result from the journal.
      if (next && truncated && !fullResult && sessionId) {
        requestToolResult(sessionId, id);
      }
      return next;
    });
  };
  const shown = body
    ? expanded || !expandable
      ? body
      : `${body.slice(0, 300)}…`
    : null;

  return (
    <div className={`r4-toolcard r4-anim-enter${status === 'pending' ? ' r4-toolcard--verifying' : ''}`}>
      <div className={`r4-toolcard-header${status === 'pending' ? ' r4-verifying-ring' : ''}`}>
        <code className="r4-toolcard-name">{name}</code>
        <span className={badgeClass}>{statusLabel}</span>
        {argsSummary && <span className="r4-toolcard-summary" title={argsSummary}>{argsSummary}</span>}
        {filePaths.map((path) => (
          <button
            key={path}
            type="button"
            className="r4-filechip"
            title={`Open ${path}`}
            onClick={() => openFile(path)}
          >
            📄 {path.split('/').pop()}
          </button>
        ))}
        {durationMs != null && <span className="r4-toolcard-duration">{durationMs}ms</span>}
        {expandable && (
          <button
            type="button"
            className="r4-btn r4-btn-icon r4-toolcard-toggle"
            aria-expanded={expanded}
            onClick={handleToggle}
            title={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? '▴' : '▾'}
          </button>
        )}
      </div>
      {shown && (
        <pre
          className={`r4-toolcard-result${expandable ? ' r4-toolcard-result--expandable' : ''}`}
          style={expanded ? { maxHeight: 'none' } : undefined}
        >
          {shown}
        </pre>
      )}
    </div>
  );
}

/** Agent-proposed file edit with native-diff review actions. */
function EditCard({ edit }: { edit: SessionEdit }) {
  const pending = edit.status === 'pending_review';
  const badge =
    edit.status === 'accepted'
      ? 'r4-toolcard-badge r4-badge-ok'
      : 'r4-toolcard-badge r4-badge-error';
  return (
    <div className={`r4-editcard r4-anim-enter${pending ? ' r4-editcard--pending' : ''}`}>
      <div className="r4-editcard-header">
        <span className="r4-editcard-icon" aria-hidden>
          📝
        </span>
        <button
          type="button"
          className="r4-filechip r4-editcard-path"
          title={`Open ${edit.path}`}
          onClick={() => openFile(edit.path)}
        >
          {edit.relPath || edit.path.split('/').pop()}
        </button>
        <span className="r4-editcard-stats" title={`${edit.added} added, ${edit.removed} removed`}>
          <span className="r4-editcard-added">+{edit.added}</span>
          <span className="r4-editcard-removed">−{edit.removed}</span>
        </span>
        {!pending && <span className={badge}>{edit.status === 'accepted' ? 'Accepted' : 'Rejected'}</span>}
      </div>
      {pending && (
        <div className="r4-editcard-actions">
          <button type="button" className="r4-btn" onClick={() => reviewEdit(edit.editId)}>
            Review diff
          </button>
          <button type="button" className="r4-btn r4-btn-primary" onClick={() => acceptEdit(edit.editId)}>
            Accept
          </button>
          <button type="button" className="r4-btn r4-btn-danger" onClick={() => rejectEdit(edit.editId)}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

interface UsageFooterProps {
  prompt: number;
  completion: number;
  total: number;
}

function UsageFooter({ prompt, completion, total }: UsageFooterProps) {
  return (
    <div className="r4-usage" title={`${total} tokens total`}>
      💡 {prompt} prompt · {completion} completion
    </div>
  );
}

/** Collapsed summary for ≥2 consecutive tool calls (S4 quiet-by-default). */
const ToolCallGroup = memo(function ToolCallGroup({ calls, sessionId }: { calls: ToolCallCardModel[]; sessionId?: string }) {
  const [expanded, setExpanded] = useState(false);
  const done = calls.filter((c) => c.status === 'completed').length;
  const failed = calls.filter((c) => c.status === 'error').length;
  const pending = calls.length - done - failed;
  const totalMs = calls.reduce((sum, c) => sum + (c.durationMs ?? 0), 0);
  const status =
    pending > 0 ? 'r4-badge-warn' : failed > 0 ? 'r4-badge-error' : 'r4-badge-ok';
  const title = `${calls.length} tool calls · done ${done} · failed ${failed} · pending ${pending}`;
  return (
    <div className="r4-toolgroup r4-anim-enter" data-testid="tool-call-group">
      <button
        type="button"
        className="r4-toolgroup-header"
        aria-expanded={expanded}
        title={title}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="r4-toolgroup-chevron" aria-hidden>{expanded ? '▾' : '▸'}</span>
        <span className={`r4-toolcard-badge ${status}`}>
          {calls.length} tool calls{totalMs > 0 ? ` · ${totalMs}ms` : ''}
        </span>
        {pending > 0 && <span className="r4-toolgroup-meta">{pending} running</span>}
        {failed > 0 && <span className="r4-toolgroup-meta r4-event-error">{failed} failed</span>}
      </button>
      {expanded && (
        <div className="r4-toolgroup-body">
          {calls.map((call) => (
            <ToolCallCard
              key={`tc-${call.id}`}
              id={call.id}
              name={call.name}
              status={call.status}
              arguments={call.arguments}
              argsSummary={call.argsSummary}
              resultPreview={call.resultPreview}
              truncated={call.truncated}
              fullResult={call.fullResult}
              durationMs={call.durationMs}
              sessionId={sessionId}
            />
          ))}
        </div>
      )}
    </div>
  );
});

/** Canonical thinking trace block: muted, collapsible, shown only when the
 *  turn produced reasoning content (chrome hidden otherwise, per plan). */
const ThinkingBlock = memo(function ThinkingBlock({ trace }: { trace: ThinkingTrace }) {
  const [expanded, setExpanded] = useState(false);
  if (!trace || trace.chunks.length === 0) return null;
  const text = thinkingText(trace);
  const running = !trace.done;
  return (
    <div className="r4-thinkingblock r4-anim-enter" data-testid="thinking-block" data-running={running}>
      <button
        type="button"
        className="r4-thinkingblock-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        title={expanded ? 'Hide reasoning' : 'Show reasoning'}
      >
        <span className="r4-thinking-chevron" aria-hidden>{expanded ? '▾' : '▸'}</span>
        {running ? 'Reasoning (streaming)…' : 'Reasoning'}
      </button>
      {expanded && <pre className="r4-thinkingblock-content">{text}</pre>}
    </div>
  );
});

/** Keep the user's command echo visible only when it's NOT a management
 *  verb echoed from this store's slash routing — the card is the answer. */
function shouldShowVerbEcho(entries: { text: string; kind: string; role?: string }[], index: number): boolean {
  const entry = entries[index];
  if (entry.kind !== 'msg' || !MGMT_VERB_PREFIX.test(entry.text.trim())) return true;
  return false;
}

export default function Transcript() {
  const { currentId, transcripts, streaming, toolCalls, usage, runStates, edits, thinking, cards, extras } = useApp();
  const turnCitations = currentId ? (extras.citations[currentId] ?? {}) : {};
  const scrollerRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  // State mirror of pinnedRef so the jump-to-bottom pill re-renders.
  const [showJump, setShowJump] = useState(false);

  const entries = currentId ? (transcripts[currentId] ?? []) : [];
  const isStreaming = currentId ? Boolean(streaming[currentId]) : false;
  const isRunning = currentId ? runStates[currentId] === 'running' : false;
  const calls = currentId ? (toolCalls[currentId] ?? []) : [];
  const usageData = currentId ? usage[currentId] : undefined;
  const sessionEdits = currentId ? (edits[currentId] ?? []) : [];
  const thinkingTrace = currentId ? thinking[currentId] : undefined;
  const mgmtCards = currentId ? (cards[currentId] ?? []) : [];

  // Length of the tail entry changes on every delta, so this covers streaming.
  const tailLength = entries.length > 0 ? entries[entries.length - 1].text.length : 0;

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !pinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [entries.length, tailLength, calls.length, currentId]);

  // Reset pinning when switching to another session.
  useEffect(() => {
    pinnedRef.current = true;
    setShowJump(false);
  }, [currentId]);

  function onScroll(): void {
    const el = scrollerRef.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinnedRef.current = gap < 40;
    // Only offer the pill when content actually moved below the fold.
    setShowJump(!pinnedRef.current && gap > 80);
  }

  function jumpToBottom(): void {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    pinnedRef.current = true;
    setShowJump(false);
  }

  function renderBody() {
    const parts: React.ReactNode[] = [];

    // Message entries (stable entry.id keys — mid-list inserts no longer
    // remount trailing entries).
    if (entries.length > 0) {
      parts.push(
        ...entries.map((entry, index) => {
          // Hide management-verb command echoes whose card answer is queued
          // or delivered — card is the reply on every surface.
          if (!shouldShowVerbEcho(entries, index)) return null;
          return (
            <MessageBubble
              key={entry.id}
              entry={entry}
              isStreaming={isStreaming && index === entries.length - 1}
              sessionId={currentId ?? undefined}
              {...entryTurnProps(entries, index, turnCitations)}
            />
          );
        }),
      );
    } else if (calls.length === 0) {
      parts.push(
        <div key="placeholder" className="r4-emptystate">
          <Mascot state="idle" size={44} />
          <p className="r4-emptystate-title">No messages yet</p>
          <p className="r4-emptystate-hint">
            Ask a question below — or paste code, logs, or a paper for analysis.
          </p>
        </div>,
      );
    }

    // Canonical reasoning trace above the final text (mute chrome when
    // the model exposed none). Rendered BEFORE the typing indicator so a
    // fresh turn keeps the stream visually anchored.
    if (thinkingTrace && thinkingTrace.chunks.length > 0) {
      parts.push(<ThinkingBlock key="thinking-block" trace={thinkingTrace} />);
    }

    // Typing indicator: the turn is running but no text has streamed yet.
    if (isRunning && !isStreaming) {
      parts.push(<ThinkingIndicator key="thinking-ind" />);
    }

    // Tool cards belong to the live turn — render them after the messages so
    // they follow chronology instead of floating above the whole transcript.
    // ≥2 consecutive calls collapse into one summary row (T3-ui S4: quiet by
    // default for long runs; expandable for detail).
    if (calls.length === 1) {
      const call = calls[0];
      parts.push(
        <ToolCallCard
          key={`tc-${call.id}`}
          id={call.id}
          name={call.name}
          status={call.status}
          arguments={call.arguments}
          argsSummary={call.argsSummary}
          resultPreview={call.resultPreview}
          truncated={call.truncated}
          fullResult={call.fullResult}
          durationMs={call.durationMs}
          sessionId={currentId ?? undefined}
        />,
      );
    } else if (calls.length > 1) {
      parts.push(<ToolCallGroup key="tc-group" calls={calls} sessionId={currentId ?? undefined} />);
    }

    // Agent edit proposals: pending strip + cards, after tools so they track
    // the turn chronologically.
    if (sessionEdits.length > 0) {
      const pendingCount = sessionEdits.filter((e) => e.status === 'pending_review').length;
      if (pendingCount > 0) {
        parts.push(
          <div key="edits-strip" className="r4-edits-strip">
            ⚠ {pendingCount} file edit{pendingCount === 1 ? '' : 's'} pending review
          </div>,
        );
      }
      parts.push(...sessionEdits.map((edit) => <EditCard key={`edit-${edit.editId}`} edit={edit} />));
    }

    // M5 management cards: rendered after tools/edits so they carry their own
    // ask→answer pairing (the TUI convention — card content mirrors the v3
    // mgmt-card.v1 contract payload-for-payload).
    if (mgmtCards.length > 0) {
      parts.push(
        ...mgmtCards.map((card, index) => (
          <MgmtCardRow key={`mgmt-${card.requestId}-${index}`} card={card} />
        )),
      );
    }

    if (usageData) {
      parts.push(<UsageFooter key="usage" {...usageData} />);
    }

    return parts;
  }

  if (!currentId) {
    return (
      <section className="r4-transcript r4-empty-state">
        <p className="r4-placeholder">Start a new chat to talk to the R4 agent.</p>
      </section>
    );
  }

  return (
    <div className="r4-transcript-wrap">
      <section className="r4-transcript" ref={scrollerRef} onScroll={onScroll}>
        {renderBody()}
      </section>
      {showJump && (
        <button
          type="button"
          className="r4-jump-bottom"
          onClick={jumpToBottom}
          aria-label="Jump to the latest message"
        >
          ↓ New messages
        </button>
      )}
    </div>
  );
}
