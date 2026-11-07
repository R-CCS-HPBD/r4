/**
 * Management card row (M5) — Transcript renderer for mgmt-card.v1 query
 * answers arriving from the bridge. Same card kinds and same row content
 * as the v3/tui-js TUIs; one component per kind, all read-only.
 *
 * Card kinds: wf-list, wf-show, wf-run, wf-results, loop-list, loop-status,
 * session-view, knowledge-status, knowledge-detail, knowledge-graph,
 * request-op.
 */
import {
  knowledgeGraphListRows,
  knowledgeInventoryRows,
  knowledgeStatusRows,
  loopIterationRows,
  loopListRows,
  loopStatusRows,
  sessionViewRows,
  statusIcon,
  wfDetailSteps,
  wfListRows,
  wfResultsRows,
} from '@/lib/mgmt-card-format';
import type { MgmtCardMessage, MgmtRow } from '@/types/protocol';

const KIND_TITLES: Record<MgmtCardMessage['kind'], string> = {
  'wf-list': 'UMRS workflows',
  'wf-show': 'workflow describe',
  'wf-run': 'workflow run',
  'wf-results': 'workflow results',
  'loop-list': 'research loops',
  'loop-status': 'loop detail',
  'session-view': 'research session view',
  'knowledge-status': 'knowledge graph status',
  'knowledge-detail': 'knowledge inventory',
  'knowledge-graph': 'registered knowledge graphs',
  'request-op': 'approval event',
};

/** Column pair table row. */
function KvRow({ row }: { row: MgmtRow }) {
  return (
    <div className="r4-mgmt-row">
      <span className="r4-mgmt-k">{row.key}:</span>
      <span className="r4-mgmt-v">{row.value}</span>
    </div>
  );
}

/** Status badge chip, paralleling the tool-card badges. */
function StatusBadge({ status }: { status?: string }) {
  const s = (status ?? '').toLowerCase();
  const variant =
    s === 'failed' || s === 'error'
      ? 'r4-badge-error'
      : s === 'running'
        ? 'r4-badge-warn'
        : s.startsWith('succ') || s === 'ok' || s === 'done' || s === 'completed'
          ? 'r4-badge-ok'
          : 'r4-badge-warn';
  return <span className={`r4-chip ${variant}`}>{statusIcon(status)} {status ?? 'unknown'}</span>;
}

export default function MgmtCardRow({ card }: { card: MgmtCardMessage }) {
  const title = KIND_TITLES[card.kind];

  if (card.status === 'pending') {
    return (
      <div className="r4-mgmt-card r4-anim-enter">
        <div className="r4-mgmt-header">
          <span className="r4-mgmt-title">{title}</span>
          <span className="r4-spin" aria-label="loading" />
        </div>
      </div>
    );
  }
  if (card.status === 'error') {
    return (
      <div className="r4-mgmt-card r4-anim-enter r4-mgmt-error">
        <div className="r4-mgmt-header">
          <span className="r4-mgmt-title">{title}</span>
          <span className="r4-chip r4-badge-error">error</span>
        </div>
        <pre className="r4-mgmt-error-text">{card.error ?? 'unknown error'}</pre>
      </div>
    );
  }

  const payload = card.payload;

  return (
    <div className="r4-mgmt-card r4-anim-enter">
      <div className="r4-mgmt-header">
        <span className="r4-mgmt-title">{title}</span>
      </div>
      {card.kind === 'wf-list' && <WfListBody payload={payload} />}
      {card.kind === 'wf-show' && <WfShowBody payload={payload} />}
      {card.kind === 'wf-run' && <WfRunBody payload={payload} />}
      {card.kind === 'wf-results' && <WfResultsBody payload={payload} />}
      {card.kind === 'loop-list' && <LoopListBody payload={payload} />}
      {card.kind === 'loop-status' && <LoopStatusBody payload={payload} />}
      {card.kind === 'session-view' && <SessionViewBody payload={payload} />}
      {card.kind === 'knowledge-status' && <KnowledgeStatusBody payload={payload} />}
      {card.kind === 'knowledge-detail' && <KnowledgeDetailBody payload={payload} />}
      {card.kind === 'knowledge-graph' && <KnowledgeGraphBody payload={payload} />}
      {card.kind === 'request-op' && <RequestOpBody payload={payload} />}
    </div>
  );
}

function WfListBody({ payload }: { payload: unknown }) {
  const { rows, total } = wfListRows(payload);
  return (
    <>
      {rows.map((row, i) => (
        <div key={i} className="r4-mgmt-row">
          <span className="r4-mgmt-v">{row[0]} {row[1] ? <span className="r4-mgmt-dim">[{row[1]}]</span> : null}</span>
          <span className="r4-mgmt-dim">{row[2]}</span>
        </div>
      ))}
      <div className="r4-mgmt-foot">{total} workflow(s) total</div>
    </>
  );
}

function WfShowBody({ payload }: { payload: unknown }) {
  const p = (payload ?? {}) as { workflow_name?: string; describe?: Record<string, unknown> };
  const d = (p.describe ?? {}) as { version?: string; description?: string; steps?: Array<string | Record<string, unknown>> };
  const steps = Array.isArray(d.steps) ? d.steps : [];
  return (
    <>
      <KvRow row={{ key: 'workflow', value: p.workflow_name ?? '' }} />
      {d.version != null && <KvRow row={{ key: 'version', value: String(d.version) }} />}
      {d.description != null && <KvRow row={{ key: 'description', value: String(d.description) }} />}
      {steps.length > 0 && <div className="r4-mgmt-dim">steps: {steps.length}</div>}
    </>
  );
}

function WfRunBody({ payload }: { payload: unknown }) {
  const { header, steps } = wfDetailSteps(payload);
  const status = header.find((r) => r.key === 'status')?.value;
  return (
    <>
      <div className="r4-mgmt-row">
        <span className="r4-mgmt-dim">run status</span> <StatusBadge status={status} />
      </div>
      {header.filter((r) => r.key !== 'status').map((row) => <KvRow key={row.key} row={row} />)}
      {steps.length > 0 && <div className="r4-mgmt-dim">steps:</div>}
      {steps.map((step, i) => (
        <div key={i} className="r4-mgmt-row">
          <StatusBadge status={step.status} />
          <span className="r4-mgmt-v"> {step.name} </span>
          <span className="r4-mgmt-dim">{step.duration ?? ''}</span>
        </div>
      ))}
    </>
  );
}

function WfResultsBody({ payload }: { payload: unknown }) {
  const rows = wfResultsRows(payload);
  return rows.map((row) => <KvRow key={row.key} row={row} />);
}

function LoopListBody({ payload }: { payload: unknown }) {
  const { rows, active, runtime } = loopListRows(payload);
  return (
    <>
      {rows.map((row) => (
        <div key={row[0]} className="r4-mgmt-row">
          <StatusBadge status={row[1]} />
          <span className="r4-mgmt-v"> {row[0]} </span>
          <span className="r4-mgmt-dim">{row[2]} · {row[3]}</span>
        </div>
      ))}
      <div className="r4-mgmt-foot">
        {rows.length} loop(s) · {active} active{runtime ? ` · runtime ${runtime}` : ''}
      </div>
    </>
  );
}

function LoopStatusBody({ payload }: { payload: unknown }) {
  const rows = loopStatusRows(payload);
  const iterations = loopIterationRows(payload);
  const status = rows.find((r) => r.key === 'status')?.value;
  return (
    <>
      <div className="r4-mgmt-row">
        <span className="r4-mgmt-dim">status</span> <StatusBadge status={status} />
      </div>
      {rows.filter((r) => r.key !== 'status').map((row) => <KvRow key={row.key} row={row} />)}
      {iterations.length > 0 && <div className="r4-mgmt-dim">recent iterations:</div>}
      {iterations.map((row, i) => (
        <div key={i} className="r4-mgmt-row">
          <span className="r4-mgmt-k">{row[0]}</span>
          <span className="r4-mgmt-v">{row[1]}</span>
        </div>
      ))}
    </>
  );
}

function SessionViewBody({ payload }: { payload: unknown }) {
  const rows = sessionViewRows(payload);
  return rows.map((row) => <KvRow key={row.key} row={row} />);
}

function KnowledgeStatusBody({ payload }: { payload: unknown }) {
  const rows = knowledgeStatusRows(payload);
  return rows.map((row) => <KvRow key={row.key} row={row} />);
}

function KnowledgeDetailBody({ payload }: { payload: unknown }) {
  const { nodes, edges, patterns, lessons, backend } = knowledgeInventoryRows(payload);
  return (
    <>
      <KvRow row={{ key: 'nodes', value: String(nodes) }} />
      <KvRow row={{ key: 'edges', value: String(edges) }} />
      <KvRow row={{ key: 'patterns', value: String(patterns) }} />
      <KvRow row={{ key: 'lessons', value: String(lessons) }} />
      {backend && <KvRow row={{ key: 'runtime', value: backend }} />}
    </>
  );
}

function KnowledgeGraphBody({ payload }: { payload: unknown }) {
  const rows = knowledgeGraphListRows(payload);
  if (rows.length === 0) return <div className="r4-mgmt-dim">no graphs</div>;
  return (
    <>
      {rows.map((row) => (
        <div key={row[0]} className="r4-mgmt-row">
          <span className="r4-mgmt-v">{row[0]} {row[1] ? <span className="r4-mgmt-dim">[{row[1]}]</span> : null} </span>
          <span className="r4-mgmt-dim">{row[2]} · {row[3]}</span>
        </div>
      ))}
    </>
  );
}

function RequestOpBody({ payload }: { payload: unknown }) {
  const p = (payload ?? {}) as { tool?: string; decision?: string };
  const decision = p.decision ?? '?';
  const label =
    decision === 'yes'
      ? 'gate approved'
      : decision === 'always'
        ? 'gate approved (always)'
        : 'gate rejected';
  return (
    <div className="r4-mgmt-row">
      <span className={decision === 'no' ? 'r4-chip r4-badge-error' : 'r4-chip r4-badge-ok'}>◔ {label}</span>
      <span className="r4-mgmt-dim"> · {p.tool ?? 'workflow gate'}</span>
    </div>
  );
}
