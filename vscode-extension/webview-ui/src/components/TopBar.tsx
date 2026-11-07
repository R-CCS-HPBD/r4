import { useEffect, useRef, useState, type ReactElement } from 'react';
import { createProject, newSession, refresh, restart, setMode, switchProject } from '@/lib/actions';
import { useApp, useAppDispatch, type SessionMode, type ToolCallCard, type ViewTab } from '@/state/store';
import Mascot from './Mascot';
import HelpOverlay from './HelpOverlay';

const MODE_CHOICES: Array<{ id: SessionMode; label: string; title: string }> = [
  { id: 'agent', label: 'Auto', title: 'Agent mode — auto-approve every tool call' },
  { id: 'plan', label: 'Plan', title: 'Plan mode — the agent proposes a plan first' },
  { id: 'ask', label: 'Ask', title: 'Ask mode — approve each tool call individually' },
];

const TAB_CHOICES: Array<{ id: ViewTab; label: string }> = [
  { id: 'chat', label: 'Chat' },
  { id: 'studio', label: 'Studio' },
  { id: 'system', label: 'System' },
];

/** Chat-side project picker: switch active project or create one in place. */
function ProjectPicker(): ReactElement {
  const { studio } = useApp();
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState('');
  const active = studio.currentProjectId ?? '';
  const known = studio.projects.some((project) => project.project_id === active);

  const onSelect = (value: string): void => {
    if (value === '__create__') {
      setCreating(true);
      return;
    }
    if (value && value !== active) switchProject(value);
  };

  const closeCreate = (): void => {
    setDraft('');
    setCreating(false);
  };

  const submitCreate = (): void => {
    const projectId = draft.trim();
    if (!projectId) return;
    createProject(projectId);
    closeCreate();
  };

  return (
    <div className="r4-project-picker">
      <select
        className="r4-select r4-project-select"
        value={active}
        onChange={(event) => onSelect(event.target.value)}
        title={
          active
            ? `Active project: ${active} — knowledge from chat lands here`
            : 'No research project — chat will offer to create one'
        }
        aria-label="Active research project"
      >
        {!active && <option value="">No project</option>}
        {studio.projects.map((project) => (
          <option key={project.project_id} value={project.project_id}>
            {project.name || project.project_id}
          </option>
        ))}
        {active && !known && <option value={active}>{active}</option>}
        <option value="__create__">＋ New project…</option>
      </select>
      {creating && (
        <span className="r4-project-create">
          <input
            className="r4-input r4-project-input"
            placeholder="new project id"
            value={draft}
            autoFocus
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submitCreate();
              if (event.key === 'Escape') closeCreate();
            }}
          />
          <button
            type="button"
            className="r4-btn r4-btn-primary"
            onClick={submitCreate}
            disabled={!draft.trim()}
            title="Create project and make it active"
          >
            Create
          </button>
          <button type="button" className="r4-btn" title="Cancel" onClick={closeCreate}>
            ✕
          </button>
        </span>
      )}
    </div>
  );
}

/** Count of knowledge/research tool calls still running (chip pulse driver). */
function pendingKnowledgeCalls(calls: ToolCallCard[] | undefined): number {
  return (calls ?? []).filter(
    (call) => call.status === 'pending' && /^(knowledge|research)/.test(call.name),
  ).length;
}

/** Chip tooltip: running knowledge tools call out the live update. */
function knowledgeChipTitle(pendingCalls: number): string {
  if (pendingCalls <= 0) return 'Open knowledge graph (Studio → Knowledge & Loops)';
  const plural = pendingCalls === 1 ? '' : 's';
  return `Knowledge graph updating (${pendingCalls} tool${plural} running) — click to open`;
}

/** Growth + pulse state driven by node-count increases (auto-resets). */
function usePulseGrowth(nodeCount: number): { growth: number; pulsing: boolean } {
  const prevCountRef = useRef(nodeCount);
  const [growth, setGrowth] = useState(0);
  const [pulseAt, setPulseAt] = useState(0);

  // knowledgeState lands mid-turn after every knowledge tool call, so the
  // counter ticks live; each increase re-arms the pulse and accumulates the
  // growth delta so ingest bursts read as e.g. "+12".
  useEffect(() => {
    const prev = prevCountRef.current;
    prevCountRef.current = nodeCount;
    if (nodeCount <= prev) return undefined;
    setGrowth((acc) => acc + (nodeCount - prev));
    setPulseAt(Date.now());
    const timer = setTimeout(() => {
      setPulseAt(0);
      setGrowth(0);
    }, 2600);
    return () => clearTimeout(timer);
  }, [nodeCount]);

  return { growth, pulsing: pulseAt > 0 };
}

/** Chip button: spinner while knowledge tools run, +Δ badge while growing. */
function GraphChipButton(props: {
  nodeCount: number;
  pendingCalls: number;
  growth: number;
  pulsing: boolean;
  onOpen: () => void;
}): ReactElement {
  const { nodeCount, pendingCalls, growth, pulsing, onOpen } = props;
  return (
    <button
      type="button"
      className={`r4-btn r4-kg-chip${pulsing ? ' r4-kg-chip-pulse' : ''}`}
      title={knowledgeChipTitle(pendingCalls)}
      onClick={onOpen}
    >
      {pendingCalls > 0 && <span className="r4-spinner r4-kg-spinner" aria-hidden="true" />}
      ◇ KG {nodeCount}
      {pulsing && growth > 0 && <span className="r4-kg-growth">+{growth}</span>}
    </button>
  );
}

/** Topbar knowledge chip: live node count, pulses + shows +Δ while growing. */
function KnowledgeGraphChip(): ReactElement | null {
  const { studio, toolCalls, currentId } = useApp();
  const dispatch = useAppDispatch();
  const nodeCount = studio.knowledgeGraph?.nodes?.length ?? 0;
  const pendingCalls = pendingKnowledgeCalls(currentId ? toolCalls[currentId] : undefined);
  const { growth, pulsing } = usePulseGrowth(nodeCount);

  if (nodeCount === 0 && pendingCalls === 0) return null;
  return (
    <GraphChipButton
      nodeCount={nodeCount}
      pendingCalls={pendingCalls}
      growth={growth}
      pulsing={pulsing}
      onOpen={() => {
        dispatch({ type: 'ui/studioPanel', panel: 'knowledge' });
        dispatch({ type: 'ui/tab', tab: 'studio' });
      }}
    />
  );
}

export default function TopBar() {
  const { status, runStates, currentId, modes, tab, studio } = useApp();
  const dispatch = useAppDispatch();
  const activeLoops = (studio.researchLoops ?? []).filter((l) =>
    ['running', 'paused', 'pending'].includes(String(l.status ?? '').toLowerCase()),
  );
  const newestLoop = activeLoops[activeLoops.length - 1];
  const busy = status === 'connecting';
  const runState = currentId ? runStates[currentId] : undefined;
  const currentMode: SessionMode = (currentId && modes[currentId]) || 'ask';
  const [doneAt, setDoneAt] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);
  const prevRunRef = useRef<string | undefined>(undefined);

  // Completion celebration — brief ✓ sweep when a run goes running → idle.
  useEffect(() => {
    const prev = prevRunRef.current;
    prevRunRef.current = runState;
    if (prev === 'running' && runState === 'idle') {
      setDoneAt(Date.now());
      const timer = setTimeout(() => setDoneAt(0), 1200);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [runState]);

  const celebrating = doneAt > 0;
  const mascotState = celebrating
    ? 'done'
    : busy
      ? 'thinking'
      : status === 'error'
        ? 'error'
        : 'idle';

  return (
    <header className={`r4-topbar${celebrating ? ' r4-topbar-celebrate' : ''}`}>
      <Mascot state={mascotState} size={20} />
      <div className="r4-tabbar" role="tablist" aria-label="View">
        {TAB_CHOICES.map((choice) => (
          <button
            key={choice.id}
            type="button"
            role="tab"
            aria-selected={tab === choice.id}
            className={`r4-tab${tab === choice.id ? ' r4-tab-active' : ''}`}
            onClick={() => dispatch({ type: 'ui/tab', tab: choice.id })}
          >
            {choice.label}
          </button>
        ))}
      </div>
      {/* V4: chat is primal — picker + mode controls stay visible even with a pane open. */}
      <ProjectPicker />
      {(
        <div
          className="r4-mode-segment"
          role="group"
          title={
            currentId
              ? 'Interaction mode: pick how agent tool calls get approved'
              : 'Start a chat to choose an interaction mode'
          }
        >
          {MODE_CHOICES.map((choice) => (
            <button
              key={choice.id}
              type="button"
              className={`r4-mode-btn${currentMode === choice.id ? ' r4-mode-btn-active' : ''}`}
              disabled={!currentId}
              title={choice.title}
              onClick={() => {
                if (!currentId) return;
                dispatch({ type: 'ui/mode', sessionId: currentId, mode: choice.id });
                setMode(currentId, choice.id);
              }}
            >
              {choice.label}
            </button>
          ))}
        </div>
      )}
      {busy && (
        <span
          className="r4-thinking-shimmer"
          title="Connecting to the agent…"
          style={{ width: 56, height: 10, display: 'inline-block' }}
        />
      )}
      {activeLoops.length > 0 && (
        <button
          type="button"
          className="r4-btn r4-loop-chip"
          title={
            newestLoop?.research_goal
              ? `Loop: ${newestLoop.research_goal} — click to open Knowledge & Loops`
              : 'A research loop is running — click to open Knowledge & Loops'
          }
          onClick={() => {
            dispatch({ type: 'ui/studioPanel', panel: 'knowledge' });
            dispatch({ type: 'ui/tab', tab: 'studio' });
          }}
        >
          <span className="r4-loop-pulse" aria-hidden="true" />
          LOOP ×{activeLoops.length}
        </button>
      )}
      <KnowledgeGraphChip />
      <span className="r4-topbar-spacer" />
      <span className="r4-topbar-spacer" />
      {tab === 'chat' && (
        <button
          type="button"
          className="r4-btn r4-btn-primary"
          onClick={() => newSession()}
          disabled={busy}
          title="Start a new chat"
        >
          New chat
        </button>
      )}
      <button
        type="button"
        className="r4-btn"
        onClick={() => setHelpOpen(true)}
        title="Help — what can I do from this UI?"
        aria-label="Help"
      >
        ?
      </button>
      <button
        type="button"
        className="r4-btn"
        onClick={refresh}
        title="Reload sessions and endpoints"
      >
        ⟳
      </button>
      <button
        type="button"
        className="r4-btn"
        onClick={restart}
        title="Restart the agent process"
      >
        ↺
      </button>
      {helpOpen && <HelpOverlay onClose={() => setHelpOpen(false)} />}
    </header>
  );
}
