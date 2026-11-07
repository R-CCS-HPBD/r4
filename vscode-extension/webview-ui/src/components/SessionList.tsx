import { useState, type ReactElement } from 'react';
import { post, relTime } from '@/lib/acp-bridge';
import { deleteSession, renameSession, selectSession, togglePin } from '@/lib/actions';
import { useApp } from '@/state/store';
import { ServerSessionFilters, sessionBadges } from './session-badges';

type SessionInfo = ReturnType<typeof useApp>['sessions'][number];

/** Loading skeleton / empty state for the chat list. */
function EmptySessions({ connecting }: { connecting: boolean }): ReactElement {
  if (connecting) {
    return (
      <nav className="r4-sessions" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="r4-session r4-skeleton-row" aria-hidden="true" />
        ))}
      </nav>
    );
  }
  return (
    <nav className="r4-sessions">
      <p className="r4-sessions-empty">No chats yet</p>
    </nav>
  );
}

/** Project filter bar (toggle project scope, reveal hidden chats). */
function SessionFilterBar(props: {
  activeProject: string;
  anyTagged: boolean;
  scoped: boolean;
  hiddenCount: number;
  onToggle: () => void;
  onShowAll: () => void;
}): ReactElement | null {
  const { activeProject, anyTagged, scoped, hiddenCount, onToggle, onShowAll } = props;
  if (!activeProject || !anyTagged) return null;
  return (
    <div className="r4-sessions-filter">
      <button
        type="button"
        className="r4-btn r4-session-filter-toggle"
        title={
          scoped
            ? `Scoped to project ${activeProject} — click to show all chats`
            : 'Showing all chats — click to scope to this project'
        }
        onClick={onToggle}
      >
        {scoped ? `▣ ${activeProject}` : '▢ all projects'}
      </button>
      {scoped && hiddenCount > 0 && (
        <button
          type="button"
          className="r4-sessions-hidden"
          title={`${hiddenCount} chat(s) from other projects — show all`}
          onClick={onShowAll}
        >
          +{hiddenCount}
        </button>
      )}
    </div>
  );
}

interface SessionRowProps {
  session: SessionInfo;
  active: boolean;
  running: boolean;
  failed: boolean;
  editing: boolean;
  draft: string;
  onSelect: () => void;
  onBeginRename: () => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onDraftChange: (value: string) => void;
  onDelete: () => void;
  /** Bridge backend: export the transcript; delete archives (VP-3a). */
  canExport?: boolean;
}

/** A row's fork and turn index (the console's Sessions pane `f` / `t`). */
function RowBridgeVerbs({ session }: { session: SessionInfo }): ReactElement {
  const verb = (type: 'forkRow' | 'sessionTurns') => (e: { stopPropagation(): void }) => {
    e.stopPropagation();
    post({ type, sessionId: session.id });
  };
  return (
    <>
      <button type="button" className="r4-btn r4-btn-icon r4-session-action" title="Fork this chat (opens the copy here)" aria-label={`Fork ${session.name}`} onClick={verb('forkRow')}>
        ⑂
      </button>
      <button type="button" className="r4-btn r4-btn-icon r4-session-action" title="Its stored turns (the server's turn index)" aria-label={`Turns of ${session.name}`} onClick={verb('sessionTurns')}>
        ≡
      </button>
    </>
  );
}

/** A server session whose runtime may still hold a lease. */
function runsOnServer(session: SessionInfo): boolean {
  return session.venue === 'frontend' && !session.archived && session.state !== 'stopped' && session.state !== 'completed';
}

/** The row's buttons: pin, rename, export (bridge backend) and delete — which
 * archives on the bridge (the journal is kept). */
function SessionActions(props: SessionRowProps): ReactElement {
  const { session } = props;
  return (
    <span className="r4-session-actionset">
      <button
        type="button"
        className="r4-btn r4-btn-icon r4-session-action"
        title={session.pinned ? 'Unpin chat' : 'Pin chat'}
        aria-label={`${session.pinned ? 'Unpin' : 'Pin'} ${session.name}`}
        onClick={(e) => {
          e.stopPropagation();
          togglePin(session.id, Boolean(session.pinned));
        }}
      >
        {session.pinned ? '📌' : '📎'}
      </button>
      <button
        type="button"
        className="r4-btn r4-btn-icon r4-session-action"
        title="Rename chat"
        aria-label={`Rename ${session.name}`}
        onClick={(e) => {
          e.stopPropagation();
          props.onBeginRename();
        }}
      >
        ✎
      </button>
      {props.canExport ? (
        <button
          type="button"
          className="r4-btn r4-btn-icon r4-session-action"
          title="Open in a chat tab (its own bridge; turns run side by side)"
          aria-label={`Open ${session.name} in a tab`}
          onClick={(e) => {
            e.stopPropagation();
            post({ type: 'openInTab', sessionId: session.id });
          }}
        >
          ⧉
        </button>
      ) : null}
      {props.canExport ? <RowBridgeVerbs session={session} /> : null}
      {props.canExport && runsOnServer(session) ? (
        <button
          type="button"
          className="r4-btn r4-btn-icon r4-session-action"
          title="Stop its runtime on the server (the lease is revoked; the session stays)"
          aria-label={`Stop ${session.name} on the server`}
          onClick={(e) => {
            e.stopPropagation();
            post({ type: 'venueStop', sessionId: session.id });
          }}
        >
          ⏹
        </button>
      ) : null}
      {props.canExport ? (
        <button
          type="button"
          className="r4-btn r4-btn-icon r4-session-action"
          title="Export the transcript (Markdown, in an editor tab)"
          aria-label={`Export ${session.name}`}
          onClick={(e) => {
            e.stopPropagation();
            post({ type: 'exportSession', sessionId: session.id });
          }}
        >
          ⤓
        </button>
      ) : null}
      <button
        type="button"
        className="r4-btn r4-btn-icon r4-session-action"
        title={props.canExport ? 'Archive chat (the journal is kept)' : 'Delete chat'}
        aria-label={`Delete ${session.name}`}
        onClick={(e) => {
          e.stopPropagation();
          props.onDelete();
        }}
      >
        ✕
      </button>
    </span>
  );
}

/** One chat row: inline rename editor or the selectable row with actions. */
function SessionRow(props: SessionRowProps): ReactElement {
  const { session, active, running, failed, editing, draft } = props;
  if (editing) {
    return (
      <div className="r4-session">
        <input
          className="r4-input"
          value={draft}
          autoFocus
          onChange={(e) => props.onDraftChange(e.target.value)}
          onBlur={props.onCommitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') props.onCommitRename();
            if (e.key === 'Escape') props.onCancelRename();
          }}
        />
      </div>
    );
  }

  return (
    <div
      className={`r4-session${active ? ' r4-session-active' : ''}`}
      onClick={() => {
        if (!active) props.onSelect();
      }}
      onDoubleClick={props.onBeginRename}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !active) props.onSelect();
      }}
    >
      <div className="r4-session-main">
        <span className="r4-session-name">
          {session.pinned && <span title="Pinned" aria-label="Pinned">📌 </span>}
          {running && <span className="r4-dot r4-dot-running" title="Responding" />}
          {failed && <span className="r4-dot r4-dot-error" title="Last turn failed" />}
          {session.name}
        </span>
        <span className="r4-session-meta">
          {[sessionBadges(session), session.model, relTime(session.updated_at)].filter(Boolean).join(' · ')}
        </span>
      </div>
      <SessionActions {...props} />
    </div>
  );
}

export default function SessionList() {
  const { sessions, currentId, runStates, status, studio, extras } = useApp();
  const bridge = extras.backend === 'bridge';
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  function beginRename(id: string, name: string): void {
    setEditingId(id);
    setDraft(name);
  }

  function commitRename(id: string): void {
    const name = draft.trim();
    if (name) renameSession(id, name);
    setEditingId(null);
  }

  // Project scoping: chats tagged with the active project surface in its
  // scope; untagged chats stay visible everywhere; toggle lifts the filter.
  const activeProject = studio.currentProjectId ?? '';
  const anyTagged = sessions.some((s) => Boolean(s.project_id));
  const scoped = Boolean(activeProject) && anyTagged && !showAllProjects;
  const visible = scoped
    ? sessions.filter((s) => !s.project_id || s.project_id === activeProject)
    : sessions;
  const hiddenCount = sessions.length - visible.length;

  if (sessions.length === 0) {
    // A filter that matches nothing must still show its controls (VP-3a).
    if (bridge && status !== 'connecting') {
      return (
        <nav className="r4-sessions">
          <ServerSessionFilters />
          <p className="r4-sessions-empty">No chats match</p>
        </nav>
      );
    }
    return <EmptySessions connecting={status === 'connecting'} />;
  }

  const filterBar = (
    <SessionFilterBar
      activeProject={activeProject}
      anyTagged={anyTagged}
      scoped={scoped}
      hiddenCount={hiddenCount}
      onToggle={() => setShowAllProjects((v) => !v)}
      onShowAll={() => setShowAllProjects(true)}
    />
  );

  if (visible.length === 0) {
    return (
      <nav className="r4-sessions">
        {filterBar}
        <p className="r4-sessions-empty">No chats in this project</p>
      </nav>
    );
  }

  return (
    <nav className="r4-sessions">
      {bridge ? <ServerSessionFilters /> : null}
      {filterBar}
      {visible.map((session) => {
        const active = session.id === currentId;
        return (
          <SessionRow
            key={session.id}
            session={session}
            active={active}
            running={runStates[session.id] === 'running'}
            failed={runStates[session.id] === 'error'}
            editing={editingId === session.id}
            draft={draft}
            onSelect={() => selectSession(session.id)}
            onBeginRename={() => beginRename(session.id, session.name)}
            onCommitRename={() => commitRename(session.id)}
            onCancelRename={() => setEditingId(null)}
            onDraftChange={setDraft}
            onDelete={() => deleteSession(session.id)}
            canExport={bridge}
          />
        );
      })}
    </nav>
  );
}
