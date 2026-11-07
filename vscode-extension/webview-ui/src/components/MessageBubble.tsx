import * as React from 'react';
import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';
import { forkSession, openCitation, openFile, saveTurn, sendMessage } from '@/lib/actions';
import { post } from '@/lib/acp-bridge';
import { useApp, useAppDispatch, type TranscriptEntry } from '@/state/store';
import type { ViewCitation } from '@/types/protocol';

interface MessageBubbleProps {
  entry: TranscriptEntry;
  isStreaming?: boolean;
  /** VP-2b: the session on screen (Edit puts text back into its composer). */
  sessionId?: string;
  /** VP-2b: the turn this message belongs to (an answer: its user message's). */
  turnId?: string;
  /** VP-2b: the passages this answer cited. */
  citations?: ViewCitation[];
}

/** VP-2b message verbs (the console's transcript cursor: r resend, e edit,
 * f fork, s save). Fork and save need the turn id, which the bridge gives. */
/** ✎: on the bridge, the console's edit-and-resend (the next send replaces
 * the conversation from this turn, VP-2c); otherwise the text goes back. */
function editState(extras: ReturnType<typeof useApp>['extras'], sessionId?: string, turnId?: string): { rewinds: boolean; editing: boolean } {
  const rewinds = extras.backend === 'bridge' && Boolean(turnId);
  return { rewinds, editing: rewinds && extras.rewind[sessionId ?? ''] === turnId };
}

function EditButton({ entry, sessionId, turnId }: { entry: TranscriptEntry; sessionId?: string; turnId?: string }) {
  const dispatch = useAppDispatch();
  const { extras } = useApp();
  const { rewinds, editing } = editState(extras, sessionId, turnId);
  const onClick = () => {
    if (rewinds) post({ type: 'rewindTo', turnId: turnId ?? '', text: entry.text });
    else if (sessionId) dispatch({ type: 'restoreDraft', sessionId, text: entry.text, force: true });
  };
  return (
    <button
      type="button"
      className={`r4-msg-act${editing ? ' r4-msg-act-on' : ''}`}
      title={rewinds ? 'Edit and resend: sending replaces the conversation from here (/rewind cancel keeps it)' : 'Edit in the composer and send again'}
      onClick={onClick}
    >
      {editing ? '✎ editing' : '✎'}
    </button>
  );
}

function MessageActions({ entry, sessionId, turnId }: { entry: TranscriptEntry; sessionId?: string; turnId?: string }) {
  const isUser = entry.role === 'You';
  return (
    <span className="r4-msg-actions">
      {isUser ? (
        <>
          <button type="button" className="r4-msg-act" title="Send this message again" onClick={() => sendMessage(entry.text, sessionId)}>
            ↻
          </button>
          <EditButton entry={entry} sessionId={sessionId} turnId={turnId} />
        </>
      ) : (
        <CopyButton text={entry.text} label="Copy response" />
      )}
      {turnId ? (
        <>
          {isUser ? (
            <button type="button" className="r4-msg-act" title="Fork the session from this turn" onClick={() => forkSession(turnId)}>
              ⑂
            </button>
          ) : null}
          <button
            type="button"
            className="r4-msg-act"
            title={isUser ? 'Keep this message in project knowledge' : 'Keep this answer in project knowledge'}
            onClick={() => saveTurn(turnId, isUser ? 'prompt' : 'answer')}
          >
            ⬒
          </button>
        </>
      ) : null}
    </span>
  );
}

/** "cited N": each passage opens in an editor tab (citation.open). */
function CitationChips({ citations }: { citations: ViewCitation[] }) {
  return (
    <div className="r4-cite-row">
      <span className="r4-cite-label">cited {citations.length}</span>
      {citations.map((c, i) => (
        <button key={`${c.sid}-${i}`} type="button" className="r4-cite-chip" title={c.preview || c.title} onClick={() => openCitation(c)}>
          [{i + 1}] {c.title}
        </button>
      ))}
    </div>
  );
}

/** Copy-to-clipboard button with a brief "copied" confirmation. */
export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function onCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API can be unavailable in webviews — fail silently.
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button
      type="button"
      className={`r4-copy-btn${copied ? ' r4-copy-btn-copied' : ''}`}
      onClick={onCopy}
      title={copied ? 'Copied!' : label}
      aria-label={copied ? 'Copied' : label}
    >
      {copied ? '✓' : '⧉'}
    </button>
  );
}

/** A `!cmd` cell (VP-2b). */
function ShellEntry({ entry }: { entry: TranscriptEntry }) {
  return <pre className={`r4-shell r4-anim-enter${entry.error ? ' r4-shell-error' : ''}`}>{entry.text}</pre>;
}

/** A slash command's output card (VP-2b part 2): its lines, as the console prints them. */
function OutputEntry({ entry }: { entry: TranscriptEntry }) {
  return <pre className={`r4-output r4-anim-enter${entry.error ? ' r4-output-error' : ''}`}>{entry.text}</pre>;
}

/** A thinking/reasoning block. Live blocks stay open while the model is still
 * reasoning; they auto-collapse the moment the block (or the answer) completes. */
function ThinkingEntry({ entry }: { entry: TranscriptEntry }) {
  return (
    <details className="r4-thinking r4-anim-enter" style={styles.thinking} open={entry.live ? true : undefined}>
      <summary style={styles.thinkingSummary}>
        <span className="r4-thinking-chevron" aria-hidden="true">▸</span>
        {entry.live ? 'Thinking…' : 'Thinking'}
      </summary>
      <pre style={styles.thinkingContent}>{entry.text}</pre>
    </details>
  );
}

/** A system/event row. */
function EventEntry({ entry }: { entry: TranscriptEntry }) {
  return (
    <div className={`r4-event r4-anim-enter ${entry.error ? 'r4-event-error' : ''}`} style={{
      ...styles.event,
      ...(entry.error ? styles.eventError : {}),
    }}>
      {entry.text}
    </div>
  );
}

/** A user or assistant message. */
function ChatMessage({ entry, isStreaming = false, sessionId, turnId, citations }: MessageBubbleProps) {
  const isUser = entry.role === 'You';
  const showCitations = !isUser && Boolean(citations?.length);
  return (
    <div
      className={`r4-message r4-anim-enter ${isUser ? 'r4-message-user' : 'r4-message-assistant'}`}
      style={{
        ...styles.message,
        ...(isUser ? styles.messageUser : styles.messageAssistant),
      }}
    >
      <div style={styles.role}>
        {isUser ? 'You' : 'R4 Agent'}
        {!isStreaming && <MessageActions entry={entry} sessionId={sessionId} turnId={turnId} />}
      </div>
      {isUser ? (
        // User messages are plain text (may contain code, but not markdown)
        <pre style={styles.userContent}>{entry.text}</pre>
      ) : (
        // Assistant messages get markdown rendering
        <MarkdownContent text={entry.text} isStreaming={isStreaming} />
      )}
      {showCitations && <CitationChips citations={citations!} />}
    </div>
  );
}

type EntryView = typeof ShellEntry;
type EntryKindName = TranscriptEntry['kind'];

const ENTRY_VIEWS: Partial<Record<EntryKindName, EntryView>> = {
  shell: ShellEntry,
  output: OutputEntry,
  thinking: ThinkingEntry,
  event: EventEntry,
};

const MessageBubble = memo(function MessageBubble(props: MessageBubbleProps) {
  const View = ENTRY_VIEWS[props.entry.kind];
  return View ? <View entry={props.entry} /> : <ChatMessage {...props} />;
});

export default MessageBubble;

// ── Markdown rendering ──────────────────────────────────────────────────────


interface MarkdownContentProps {
  text: string;
  isStreaming: boolean;
}

/** Conservative path token check: must carry an extension AND a separator —
 *  plain identifiers never linkify (false positives worse than misses). */
const PATH_TOKEN_RE = /^[\w./~-]+\.[a-zA-Z0-9]{1,10}$/;
function looksLikePath(text: string): boolean {
  if (text.length < 4 || text.length > 160) return false;
  if (!PATH_TOKEN_RE.test(text)) return false;
  return text.includes('/') || text.startsWith('.') || text.startsWith('~');
}

/** Inline-path chip: opens in editor on click, degrades to plain code otherwise. */
function InlineCodeRef({ text, children, className, ...rest }: {
  text: string;
  children: React.ReactNode;
  className?: string;
}) {
  if (looksLikePath(text)) {
    return (
      <button
        type="button"
        className={`r4-inlineref ${className ?? ''}`}
        title={`Open ${text} in editor`}
        onClick={() => openFile(text)}
        {...rest}
      >
        {children}
      </button>
    );
  }
  return (
    <code className={className} {...rest} style={styles.code}>
      {children}
    </code>
  );
}

function MarkdownContent({ text, isStreaming }: MarkdownContentProps) {
  return (
    <div className={`r4-markdown ${isStreaming ? 'r4-streaming' : ''}`} style={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          [rehypeHighlight, { detect: true, ignoreMissing: true }],
        ]}
        components={{
          // Custom components for better VS Code theming + code block copy.
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          code: ({ className, children, ...props }) => (
            <InlineCodeRef text={textOf(children)} className={className} {...props}>
              {children}
            </InlineCodeRef>
          ),
          ul: ({ children }) => <ul style={styles.list}>{children}</ul>,
          ol: ({ children, ...props }) => (
            <ol style={styles.list} {...props}>{children}</ol>
          ),
          li: ({ children }) => <li style={styles.listItem}>{children}</li>,
          table: ({ children }) => (
            <div style={styles.tableWrap}>
              <table style={styles.table}>{children}</table>
            </div>
          ),
          th: ({ children }) => <th style={styles.th}>{children}</th>,
          td: ({ children }) => <td style={styles.td}>{children}</td>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" style={styles.link}>
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote style={styles.blockquote}>{children}</blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
      {isStreaming && <span className="r4-cursor" style={styles.inlineCursor} />}
    </div>
  );
}

// ── Code block with copy affordance ─────────────────────────────────────────

/** Extract the text content of a rendered code tree (best-effort). */
function textOf(node: React.ReactNode): string {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (typeof node === 'object' && 'props' in (node as React.ReactElement)) {
    const el = node as React.ReactElement<{ children?: React.ReactNode }>;
    return textOf(el.props.children);
  }
  return '';
}

/** Language label from the highlighted code element's className ("language-x"). */
function languageOf(node: React.ReactNode): string {
  if (
    node != null &&
    typeof node === 'object' &&
    !Array.isArray(node) &&
    'props' in (node as React.ReactElement)
  ) {
    const el = node as React.ReactElement<{ className?: string }>;
    const cls = el.props.className ?? '';
    const match = /language-([\w-]+)/.exec(cls);
    if (match) return match[1];
  }
  return '';
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  const codeText = textOf(children).replace(/\n$/, '');
  const language = languageOf(children);
  return (
    <div className="r4-codeblock">
      <div className="r4-codeblock-bar">
        <span className="r4-codeblock-lang">{language || 'code'}</span>
        <CopyButton text={codeText} label="Copy code" />
      </div>
      <pre className="r4-codeblock-pre" style={styles.codeBlock}>
        {children}
      </pre>
    </div>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  message: {
    marginBottom: 14,
    overflowWrap: 'anywhere' as const,
  },
  messageUser: {
    paddingLeft: 8,
    background: 'color-mix(in srgb, var(--vscode-charts-blue, #007acc) 10%, transparent)',
    borderLeft: '3px solid var(--vscode-charts-blue, #007acc)',
    borderRadius: '0 4px 4px 0',
  },
  messageAssistant: {},
  role: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    color: 'var(--vscode-descriptionForeground, #888)',
    marginBottom: 2,
  },
  userContent: {
    whiteSpace: 'pre-wrap' as const,
    fontFamily: 'inherit',
    fontSize: 'inherit',
    lineHeight: 1.55,
    margin: 0,
  },
  markdown: {
    // Do NOT use pre-wrap here: react-markdown already produces block
    // elements, and preserving source newlines inserts double blank lines
    // between paragraphs/lists. Code blocks keep pre formatting via <pre>.
    whiteSpace: 'normal' as const,
    lineHeight: 1.55,
    fontSize: 13,
  },
  codeBlock: {
    background: 'var(--vscode-editor-inactiveSelectionBackground, #2d2d2d)',
    padding: '8px 12px',
    overflow: 'auto',
    margin: 0,
    fontSize: 12,
  },
  code: {
    fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
    fontSize: '0.9em',
  },
  list: {
    paddingLeft: 24,
    margin: '4px 0',
  },
  listItem: {
    margin: '2px 0',
  },
  tableWrap: {
    overflow: 'auto',
    margin: '8px 0',
  },
  table: {
    borderCollapse: 'collapse' as const,
    width: '100%',
    fontSize: 12,
  },
  th: {
    background: 'var(--vscode-editor-headerForeground, #ffffff0a)',
    border: '1px solid var(--vscode-panel-border, #ffffff1a)',
    padding: '4px 8px',
    textAlign: 'left' as const,
    fontWeight: 600,
  },
  td: {
    border: '1px solid var(--vscode-panel-border, #ffffff1a)',
    padding: '4px 8px',
  },
  link: {
    color: 'var(--vscode-textLink-foreground, #007acc)',
    textDecoration: 'none',
  },
  linkHover: {
    textDecoration: 'underline',
  },
  blockquote: {
    borderLeft: '3px solid var(--vscode-editorQuote-border, #007acc)',
    paddingLeft: 12,
    margin: '8px 0',
    color: 'var(--vscode-descriptionForeground, #888)',
  },
  thinking: {
    borderLeft: '3px solid var(--vscode-progressBar-background, #c27f0e)',
    paddingLeft: 8,
    background: 'color-mix(in srgb, var(--vscode-progressBar-background, #c27f0e) 6%, transparent)',
    borderRadius: '0 4px 4px 0',
    marginBottom: 10,
  },
  thinkingSummary: {
    cursor: 'pointer',
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--vscode-progressBar-background, #c27f0e)',
    textTransform: 'uppercase' as const,
    listStyle: 'none' as const,
    userSelect: 'none' as const,
    marginBottom: 2,
  },
  thinkingContent: {
    whiteSpace: 'pre-wrap' as const,
    fontSize: 12,
    color: 'var(--vscode-descriptionForeground, #888)',
    fontStyle: 'italic' as const,
    fontFamily: 'inherit',
    margin: '4px 0 0 0',
    lineHeight: 1.4,
  },
  event: {
    paddingLeft: 8,
    color: 'var(--vscode-descriptionForeground, #888)',
    borderLeft: '2px solid var(--vscode-progressBar-background, #c27f0e)',
    fontSize: 12,
    margin: '0 0 8px 0',
    whiteSpace: 'pre-wrap' as const,
    overflowWrap: 'anywhere' as const,
  },
  eventError: {
    color: 'var(--vscode-errorForeground, #f44747)',
    borderLeftColor: 'var(--vscode-errorForeground, #f44747)',
  },
  inlineCursor: {
    display: 'inline-block',
    width: 2,
    height: '1em',
    background: 'var(--vscode-foreground, #ccc)',
    animation: 'r4-blink 1s step-end infinite',
    verticalAlign: 'text-bottom' as const,
    marginLeft: 1,
  },
};
