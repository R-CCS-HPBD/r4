/**
 * Knowledge panel — project knowledge graph constellation + research loops.
 *
 * Same surfaces as the console v3 "Knowledge" space: a radial constellation
 * of knowledge nodes with typed colors/icons, click-to-inspect neighbors,
 * plus the research-loop DAG list with per-iteration phases.
 * Data comes from the r4 ACP knowledge/graph and research/loops methods
 * via the extension bridge (read-only; stop is the only mutation).
 */

import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import {
  ingestKnowledge,
  pickKnowledgeFiles,
  refreshKnowledge,
  searchCatalog,
  setKnowledgeGraphConstraints,
  startResearchLoop,
  stopResearchLoop,
} from '@/lib/actions';
import { useApp, useAppDispatch } from '@/state/store';
import {
  satelliteColor,
  semanticKind,
  typeColor,
} from './knowledge-visual';
import KnowledgeConstellationSigma from './KnowledgeConstellationSigma';
import type {
  CatalogSearchHit,
  KnowledgeNode,
  KnowledgeEdge,
  KnowledgeGraphConstraints,
  KnowledgeGraphMeta,
  KnowledgeVerificationPayload,
  KnowledgeReconcilePayload,
  GraphContentPayload,
  LoopRuntimeInfo,
  ResearchLoop,
} from '@/types/protocol';

/* Live growth: poll cadence (only while work is in flight), spawn-pulse window. */
const KNOWLEDGE_POLL_MS = 5000;
const KNOWLEDGE_SPAWN_MS = 6000;
const ACTIVE_LOOP_STATUSES = new Set(['running', 'pending', 'paused']);

// ── Inspector ────────────────────────────────────────────────────────────

/** Canonical identity rows extracted from a durable node's metadata
 *  (ADR-031 §3 exploration — read-only, no new fetch). */
function canonicalNodeFacts(node: KnowledgeNode): Array<{ key: string; value: string }> {
  const meta = (node.metadata ?? {}) as Record<string, unknown>;
  const facts: Array<{ key: string; value: string }> = [{ key: 'node_id', value: String(node.id) }];
  const pick = (k: string) => {
    const v = meta[k];
    if (typeof v === 'string' && v) facts.push({ key: k, value: v });
    else if (typeof v === 'number') facts.push({ key: k, value: String(v) });
  };
  for (const k of ['kind', 'document_id', 'graph_id'] as const) pick(k);
  if (typeof meta.chunk_index === 'number') facts.push({ key: 'chunk_index', value: String(meta.chunk_index) });
  if (typeof meta.chunk_start === 'number' && typeof meta.chunk_end === 'number') {
    facts.push({ key: 'span', value: `${meta.chunk_start}–${meta.chunk_end}` });
  }
  if (typeof meta.origin === 'string') facts.push({ key: 'origin', value: String(meta.origin) });
  if (typeof meta.s3_object === 'string') facts.push({ key: 's3_object', value: String(meta.s3_object) });
  if (typeof meta.s3_chunk === 'string') facts.push({ key: 's3_chunk', value: String(meta.s3_chunk) });
  if (typeof meta.artifact_id === 'string') facts.push({ key: 'artifact_id', value: String(meta.artifact_id) });
  if (typeof meta.chunk_md5 === 'string' && meta.chunk_md5) {
    facts.push({ key: 'chunk_md5', value: String(meta.chunk_md5).slice(0, 10) + '…' });
  }
  return facts;
}

function KnowledgeInspector(props: {
  node: KnowledgeNode;
  edges: KnowledgeEdge[];
  nodes: KnowledgeNode[];
  /** §12 preflight for the node's document (ADR-031 reconcile-derived). */
  reconcile?: KnowledgeReconcilePayload | null;
}): ReactElement {
  const { node, edges, nodes } = props;
  const labelById = useMemo(
    () => new Map(nodes.filter(Boolean).map((n) => [String(n.id), String(n.name ?? n.label ?? n.id)])),
    [nodes],
  );
  const neighbors = useMemo(
    () =>
      edges
        .filter((e) => String(e.from) === String(node.id) || String(e.to) === String(node.id))
        .slice(0, 8)
        .map((edge) => ({
          edge,
          outgoing: String(edge.from) === String(node.id),
          otherId: String(edge.from) === String(node.id) ? String(edge.to) : String(edge.from),
        })),
    [node.id, edges],
  );

  return (
    <div className="r4-studio-detail">
      <div className="r4-studio-detail-head">
        <strong>{String(node.name ?? node.label ?? node.id)}</strong>
        <span className="r4-studio-pill">{String(node.type ?? 'knowledge')}</span>
      </div>
      <div className="r4-studio-detail-body">
        {(() => {
          const meta = (node.metadata ?? {}) as Record<string, unknown>;
          const docId = typeof meta.document_id === 'string' ? meta.document_id : null;
          const pre = preflightState(props.reconcile ?? null, docId);
          return pre ? (
            <div>
              <span className="r4-studio-key">preflight</span>
              <span className={`r4-studio-status r4-studio-status-${pre.state === 'COMPLETE' ? 'ready' : pre.state === 'EMBEDDING_PENDING' ? 'pending' : 'unknown'}`}>
                {pre.state}
              </span>
              {pre.reasons[0] && <span className="r4-studio-hint"> {pre.reasons[0]}</span>}
            </div>
          ) : (
            <div>
              <span className="r4-studio-key">preflight</span>
              <span className="r4-studio-hint">state unavailable (checker not scanned)</span>
            </div>
          );
        })()}
        {canonicalNodeFacts(node).map((fact) => (
          <div key={fact.key}>
            <span className="r4-studio-key">{fact.key}</span>
            <span style={{ wordBreak: 'break-all' }}>{fact.value}</span>
          </div>
        ))}
        {typeof node.confidence === 'number' && (
          <div>
            <span className="r4-studio-key">confidence</span>
            <span>{(node.confidence * 100).toFixed(0)}%</span>
          </div>
        )}
      </div>
      {neighbors.length > 0 && (
        <div className="r4-studio-detail-step">
          <span className="r4-studio-key">Related ({neighbors.length})</span>
          <ul className="r4-studio-list">
            {neighbors.map(({ edge, outgoing, otherId }, idx) => (
              <li key={idx} className="r4-studio-list-item">
                <span className="r4-studio-list-title">
                  {outgoing ? '→' : '←'} {String(edge.label ?? edge.type ?? 'related')}{' '}
                  <strong>{labelById.get(otherId) ?? otherId}</strong>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ── Science catalogue card (project data objects, /gw/catalog/search) ──────

const MODALITY_ICONS: Record<string, string> = {
  text: '📄',
  document: '📄',
  image: '🖼',
  tabular: '📊',
  table: '📊',
  audio: '🔊',
  video: '🎬',
  code: '💻',
  archive: '🗜',
};

function modalityIcon(modality: string | undefined): string {
  return MODALITY_ICONS[(modality ?? '').toLowerCase()] ?? '📦';
}

function catalogueFilename(s3Key: string): string {
  const last = s3Key.split('/').filter(Boolean).pop() ?? s3Key;
  return last.length > 42 ? `…${last.slice(-40)}` : last;
}

function catalogueTooltip(hit: CatalogSearchHit): string {
  const parts = [hit.deterministic_summary || hit.summary || ''].filter(Boolean);
  if (hit.perception_summary) parts.push(`perception: ${hit.perception_summary}`);
  if ((hit.tags ?? []).length > 0) parts.push(`tags: ${(hit.tags ?? []).join(', ')}`);
  const links = hit.links;
  if (links) {
    parts.push(
      `canonical: as doc ${links.document_id} (${links.link_method}) · ${links.chunk_nodes} chunks`,
    );
    for (const v of links.vector_rows) {
      parts.push(`vectors: ${v.model} ${v.dimension}d ×${v.rows}`);
    }
  } else {
    parts.push('canonical: not yet part of the knowledge spine');
  }
  parts.push(hit.s3_key);
  return parts.join('\n');
}

/** Badge semantics from plane links (UX §1 distinctions, no new authority):
 *  no links → not canonized yet; links + no vectors → embedding pending;
 *  links + vectors → searchable. Derived only, never persisted. */
function canonicalBadge(hit: CatalogSearchHit): { label: string; tone: string } {
  const links = hit.links;
  if (!links) return { label: 'catalogue only', tone: 'unknown' };
  if (links.vector_rows.length === 0) return { label: 'canonized · no vectors yet', tone: 'pending' };
  return { label: 'searchable', tone: 'ready' };
}

function canonicalLinksLine(hit: CatalogSearchHit): string | null {
  const links = hit.links;
  if (!links) return null;
  const vectorPart = links.vector_rows
    .map((v) => `${v.model.split('/').pop()}d${v.dimension}×${v.rows}`)
    .join(' ');
  return `${links.document_id} · ${links.chunk_nodes} chunks${vectorPart ? ` · ${vectorPart}` : ''}`;
}

/** One catalogue row: icon, filename, modality + state + canonical badges. */
/** §5 'What does R4 know about this?' — aggregate detail per row.*/
function catalogueDetailFacts(hit: CatalogSearchHit): Array<{ key: string; value: string }> {
  const facts: Array<{ key: string; value: string }> = [
    { key: 's3_key', value: hit.s3_key },
    { key: 'state', value: String(hit.state ?? 'unknown') },
  ];
  if (hit.detected_modality) facts.push({ key: 'modality', value: hit.detected_modality });
  if (hit.deterministic_summary) facts.push({ key: 'summary', value: hit.deterministic_summary });
  if (hit.perception_summary) facts.push({ key: 'perception', value: hit.perception_summary });
  if ((hit.tags ?? []).length > 0) facts.push({ key: 'tags', value: hit.tags!.join(', ') });
  const links = hit.links;
  if (links) {
    facts.push({ key: 'document_id', value: links.document_id });
    facts.push({ key: 'link lane', value: links.link_method });
    facts.push({ key: 'chunk nodes', value: String(links.chunk_nodes) });
    for (const v of links.vector_rows) {
      facts.push({ key: 'vector rows', value: `${v.model} (${v.dimension}d) ×${v.rows}` });
    }
  } else {
    facts.push({ key: 'canonical', value: 'not part of the knowledge spine yet' });
  }
  return facts;
}

function CatalogueRow(props: {
  hit: CatalogSearchHit;
  selected: boolean;
  onToggle: () => void;
}): ReactElement {
  const { hit } = props;
  const state = String(hit.state ?? 'unknown').toLowerCase();
  const badge = canonicalBadge(hit);
  const linksLine = canonicalLinksLine(hit);
  return (
    <li
      className={`r4-studio-list-item${props.selected ? ' r4-studio-list-item-active' : ''}`}
      title={catalogueTooltip(hit)}
      aria-label={catalogueFilename(hit.s3_key)}
      onClick={props.onToggle}
      style={{ cursor: 'pointer' }}
    >
      <span aria-hidden="true">{modalityIcon(hit.detected_modality)}</span>
      <span className="r4-studio-list-title">{catalogueFilename(hit.s3_key)}</span>
      <span className="r4-studio-hint">
        {linksLine && `${linksLine} · `}
        <span className={`r4-studio-status r4-studio-status-${badge.tone}`}>
          {badge.label}
        </span>
      </span>
      {hit.detected_modality && (
        <span className="r4-studio-pill">{hit.detected_modality}</span>
      )}
      <span className={`r4-studio-status r4-studio-status-${state}`}>{hit.state}</span>
    </li>
  );
}

/** Science catalogue card: what data objects exist for this project. */
function ScienceCatalogCard(props: { projectId: string | null }): ReactElement | null {
  const { studio } = useApp();
  const [query, setQuery] = useState('');
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  // Initial full listing once per project switch; explicit search after.
  const requestRef = useRef<string | null>(null);
  useEffect(() => {
    if (!props.projectId) return;
    requestRef.current = props.projectId;
    setLoadedOnce(false);
    searchCatalog('', props.projectId);
  }, [props.projectId]);

  // Any catalogue answer for this project settles the load state (incl. 0 hits).
  useEffect(() => {
    if (studio.catalogError !== null || studio.catalogHits.length > 0) {
      setLoadedOnce(true);
    }
  }, [studio.catalogHits, studio.catalogError]);

  /* Dead-air settle: when the ACP round trip itself never surfaces (no
     payload AND no error), settle the loading state after 12s so the card
     stops spinning forever and offers a retry. The error is not fabricated:
     the response slot was simply never filled (legacy gateway / inspector
     wedged / dead stream). */
  const [deadAir, setDeadAir] = useState(false);
  useEffect(() => {
    if (loadedOnce || !props.projectId) return undefined;
    const timer = setTimeout(() => {
      setDeadAir(true);
      setLoadedOnce(true);
    }, 12_000);
    return () => clearTimeout(timer);
  }, [loadedOnce, props.projectId]);

  const onRetry = () => {
    setDeadAir(false);
    setLoadedOnce(false);
    searchCatalog(query.trim() || '', props.projectId ?? undefined);
  };

  if (!props.projectId) return null;
  const projectId = props.projectId;

  const onSearch = () => {
    setLoadedOnce(true);
    searchCatalog(query.trim(), projectId);
  };

  const hits = studio.catalogHits;
  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>Science catalogue{hits.length > 0 ? ` (${hits.length})` : ''}</span>
        <button
          type="button"
          className="r4-btn"
          title="Reload catalogue"
          onClick={() => searchCatalog('', projectId)}
        >
          ⟳
        </button>
      </div>
      <div className="r4-studio-start-row">
        <input
          className="r4-input"
          placeholder="search objects (name, summary, tags)"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onSearch();
          }}
        />
        <button type="button" className="r4-btn" onClick={onSearch}>
          Search
        </button>
      </div>
      {studio.catalogError && <div className="r4-studio-error">{studio.catalogError}</div>}
      {!studio.catalogError && deadAir && hits.length === 0 && (
        <div className="r4-studio-empty">
          Catalogue silenced — no payload from the gateway in 12s
          <button type="button" className="r4-btn r4-btn-primary" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
      {!studio.catalogError && !deadAir && hits.length === 0 && loadedOnce && (
        <div className="r4-studio-empty">
          No catalogue objects for project “{props.projectId}”
        </div>
      )}
      {!studio.catalogError && !deadAir && hits.length === 0 && !loadedOnce && (
        <div className="r4-studio-empty">Loading catalogue…</div>
      )}
      {hits.length > 0 && (
        <ul className="r4-studio-list" aria-label="Science catalogue">
          {hits.map((hit) => (
            <CatalogueRow
              key={hit.s3_key}
              hit={hit}
              selected={selectedKey === hit.s3_key}
              onToggle={() => setSelectedKey(selectedKey === hit.s3_key ? null : hit.s3_key)}
            />
          ))}
        </ul>
      )}
      {selectedKey && hits.find((h) => h.s3_key === selectedKey) && (
        <div className="r4-studio-detail">
          <div className="r4-studio-detail-head">
            <strong>{catalogueFilename(selectedKey)}</strong>
            <span className={`r4-studio-status r4-studio-status-${canonicalBadge(hits.find((h) => h.s3_key === selectedKey)!).tone}`}>
              {canonicalBadge(hits.find((h) => h.s3_key === selectedKey)!).label}
            </span>
          </div>
          <div className="r4-studio-detail-body">
            {catalogueDetailFacts(hits.find((h) => h.s3_key === selectedKey)!).map((fact, idx) => (
              <div key={idx}>
                <span className="r4-studio-key">{fact.key}</span>
                <span style={{ wordBreak: 'break-all' }}>{fact.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ── Research loop card (console Research DAG parity) ─────────────────────

/** "agentic" vs "phased" badge from loop metadata. */
function loopAgenticBadge(loop: ResearchLoop): string {
  const meta = (loop.metadata ?? {}) as Record<string, unknown>;
  const agentic =
    meta.agentic === true ||
    (loop as Record<string, unknown>).agentic === true;
  return agentic ? 'agentic' : 'phased';
}

/** requires map → "key:value" badges (skipping false/null/empty values). */
function requiresBadges(requires: Record<string, unknown>): string[] {
  const badges: string[] = [];
  for (const [key, value] of Object.entries(requires)) {
    if (value !== false && value != null && value !== '') badges.push(`${key}:${String(value)}`);
  }
  return badges;
}

/** Runtime badges: venue + durable + backend list. */
function runtimeBadges(runtime: LoopRuntimeInfo | null): string[] {
  if (!runtime) return [];
  const badges: string[] = [];
  if (runtime.runtime) badges.push(`venue:${runtime.runtime}`);
  if (runtime.durable) badges.push('durable');
  if (runtime.backends.length > 0) badges.push(runtime.backends.join('/'));
  return badges;
}

/** "What kind of loop" badges: agentic/requires/venue/durable. */
function loopKindBadges(loop: ResearchLoop, runtime: LoopRuntimeInfo | null): string[] {
  const meta = (loop.metadata ?? {}) as Record<string, unknown>;
  const requires = (meta.requires ?? {}) as Record<string, unknown>;
  return [
    loopAgenticBadge(loop),
    ...requiresBadges(requires),
    ...runtimeBadges(runtime),
  ];
}

function ResearchLoopCard(props: {
  loop: ResearchLoop;
  runtime: LoopRuntimeInfo | null;
  onStop: (loopId: string) => void;
}): ReactElement {
  const { loop, runtime, onStop } = props;
  const status = String(loop.status ?? 'unknown').toLowerCase();
  const iterations = Array.isArray(loop.iterations) ? loop.iterations : [];
  const stoppable = status === 'running' || status === 'paused' || status === 'pending';
  const badges = loopKindBadges(loop, runtime);
  return (
    <div className="r4-studio-detail">
      <div className="r4-studio-detail-head">
        <strong>{String(loop.loop_id)}</strong>
        <span className={`r4-studio-status r4-studio-status-${status}`}>
          {loop.status ?? 'unknown'}
        </span>
        {stoppable && (
          <button
            type="button"
            className="r4-btn"
            title="Stop this research loop"
            onClick={() => onStop(String(loop.loop_id))}
          >
            Stop
          </button>
        )}
      </div>
      <div className="r4-studio-detail-body">
        <div>
          <span className="r4-studio-key">goal</span>
          <span>{String(loop.research_goal ?? '(none)')}</span>
        </div>
        <div>
          <span className="r4-studio-key">iteration</span>
          <span>
            {`${loop.current_iteration ?? 0}/${loop.max_iterations ?? '?'} · ${loop.phase ?? '—'}`}
          </span>
        </div>
        {badges.length > 0 && (
          <div className="r4-loop-kind-badges" title="Loop kind: executor, venue, durability">
            {badges.map((badge) => (
              <span key={badge} className="r4-studio-pill">
                {badge}
              </span>
            ))}
          </div>
        )}
      </div>
      {iterations.length > 0 && (
        <ul className="r4-studio-steps">
          {iterations.slice(-6).map((it, idx) => (
            <li key={idx} className="r4-studio-step">
              <span className={`r4-studio-status r4-studio-status-${String(it.status ?? 'pending').toLowerCase()}`}>
                {String(it.status ?? 'pending')}
              </span>
              <span>{String(it.phase ?? `iteration ${String(it.iteration ?? '?')}`)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Venue hint line for loop-kind transparency before starting. */
function venueHint(runtime: LoopRuntimeInfo | null): string {
  if (!runtime || !runtime.runtime) return 'venue resolves at start (default: UMRS)';
  const bits = [runtime.runtime];
  if (runtime.durable) bits.push('durable');
  if (runtime.backends.length > 0) bits.push(`backends: ${runtime.backends.join('/')}`);
  return `venue: ${bits.join(' · ')}`;
}

/** Start-research-loop card — chat-first CTA with loop-kind selectors. */
function StartResearchLoopCard(props: {
  projectId: string | null;
  runtime: LoopRuntimeInfo | null;
}): ReactElement {
  const [goal, setGoal] = useState('');
  const [maxIterations, setMaxIterations] = useState('10');
  const [agentic, setAgentic] = useState(false);
  const [venue, setVenue] = useState('');
  const [wantGpu, setWantGpu] = useState(false);
  const [wantCluster, setWantCluster] = useState(false);
  const [error, setError] = useState('');

  const onStart = () => {
    const trimmed = goal.trim();
    if (!trimmed) {
      setError('Research goal is required.');
      return;
    }
    const max = Math.max(1, Math.floor(Number(maxIterations) || 10));
    const requires: Record<string, unknown> = {};
    if (wantGpu) requires.gpu = 1;
    if (wantCluster) requires.cluster = true;
    setError('');
    startResearchLoop({
      researchGoal: trimmed,
      projectId: props.projectId ?? undefined,
      maxIterations: max,
      agentic,
      requires,
      runtimeMode: venue || undefined,
    });
    setGoal('');
  };

  return (
    <div className="r4-studio-detail r4-studio-start-card">
      <div className="r4-studio-detail-head">
        <strong>Start a research loop</strong>
      </div>
      <input
        className="r4-input"
        placeholder='research goal, e.g. "optimize MOF-5 for CO2 capture"'
        value={goal}
        onChange={(event) => setGoal(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onStart();
        }}
      />
      <div className="r4-studio-start-row">
        <input
          className="r4-input r4-input-narrow"
          placeholder="max iterations"
          value={maxIterations}
          onChange={(event) => setMaxIterations(event.target.value)}
          inputMode="numeric"
        />
        <select
          className="r4-select"
          value={venue}
          onChange={(event) => setVenue(event.target.value)}
          title="Runtime venue (empty = resolver rules, default UMRS)"
          aria-label="Loop runtime venue"
        >
          <option value="">venue: auto</option>
          <option value="umrs">venue: UMRS (durable, distributed)</option>
          <option value="local">venue: local (single-process)</option>
          <option value="hybrid">venue: hybrid (cluster via requires)</option>
        </select>
      </div>
      <div className="r4-studio-start-row">
        <label className="r4-studio-checkbox" title="Drive iterations with the site LLM agent executor">
          <input
            type="checkbox"
            checked={agentic}
            onChange={(event) => setAgentic(event.target.checked)}
          />
          agentic (site LLM picks each iteration)
        </label>
        <label className="r4-studio-checkbox" title="Advertise a GPU need (cluster-class → UMRS in hybrid mode)">
          <input
            type="checkbox"
            checked={wantGpu}
            onChange={(event) => setWantGpu(event.target.checked)}
          />
          gpu
        </label>
        <label className="r4-studio-checkbox" title="Cluster-class work (Slurm/K8s backend)">
          <input
            type="checkbox"
            checked={wantCluster}
            onChange={(event) => setWantCluster(event.target.checked)}
          />
          cluster
        </label>
      </div>
      <button type="button" className="r4-btn r4-btn-primary" onClick={onStart}>
        ▶ Start loop
      </button>
      <div className="r4-studio-hint">
        {agentic
          ? 'Loop kind: agentic — each iteration is one bounded site-LLM turn (hypothesis → evidence → next).'
          : 'Loop kind: phased — iterations run planned experiments (simulated without a durable workflow).'}
        {' '}{venueHint(props.runtime)}
      </div>
      {error && <div className="r4-studio-error">{error}</div>}
    </div>
  );
}

/** Feed data into knowledge: pick files, paste paths, or external-search toggle. */
function IngestCard(props: { projectId: string | null }): ReactElement {
  const { studio } = useApp();
  const dispatch = useAppDispatch();
  const [manualFile, setManualFile] = useState('');
  const [sourcesText, setSourcesText] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (studio.pendingKnowledgeFiles.length > 0) {
      setFiles((prev) => [...new Set([...prev, ...studio.pendingKnowledgeFiles])]);
      dispatch({ type: 'knowledgeFilesPicked', files: [] });
    }
  }, [studio.pendingKnowledgeFiles, dispatch]);

  const sources = useMemo(
    () =>
      sourcesText
        .split(/[\n,]/)
        .map((entry) => entry.trim())
        .filter(Boolean),
    [sourcesText],
  );

  const onSubmit = () => {
    if (files.length === 0 && sources.length === 0) {
      setError('Nothing to ingest — add files or sources.');
      return;
    }
    setError('');
    ingestKnowledge({ files, sources, projectId: props.projectId ?? undefined });
    setFiles([]);
    setSourcesText('');
  };

  return (
    <div className="r4-studio-detail r4-studio-start-card">
      <div className="r4-studio-detail-head">
        <strong>Feed data into knowledge</strong>
        <button type="button" className="r4-btn" onClick={pickKnowledgeFiles} title="Pick files">
          📎 Pick files…
        </button>
      </div>
      <div className="r4-studio-start-row">
        <input
          className="r4-input"
          placeholder="paste a file path and press Enter"
          value={manualFile}
          onChange={(event) => setManualFile(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              const entry = manualFile.trim();
              if (entry) setFiles((prev) => [...new Set([...prev, entry])]);
              setManualFile('');
            }
          }}
        />
      </div>
      {files.length > 0 && (
        <div className="r4-studio-chips">
          {files.map((path) => (
            <button
              key={path}
              type="button"
              className="r4-chip"
              title={`Remove ${path}`}
              onClick={() => setFiles((prev) => prev.filter((p) => p !== path))}
            >
              {path.split(/[\/\\\\]/).pop()} ✕
            </button>
          ))}
        </div>
      )}
      <input
        className="r4-input"
        placeholder='external search terms (comma or newline, e.g. "CO2 binding MOF")'
        value={sourcesText}
        onChange={(event) => setSourcesText(event.target.value)}
      />
      {sources.length > 0 && (
        <div className="r4-studio-chips">
          {sources.map((term) => (
            <span key={term} className="r4-chip r4-chip-variant">
              {term}
            </span>
          ))}
        </div>
      )}
      {error && <div className="r4-studio-error">{error}</div>}
      <button type="button" className="r4-btn r4-btn-primary" onClick={onSubmit}>
        ▶ Build knowledge
      </button>
      {studio.knowledgeIngested && (
        <div className="r4-studio-hint">Last ingest accepted — graph refreshes below as it builds.</div>
      )}
    </div>
  );
}

// ── Verification card (verify/status; hidden when gateway lacks the route) ──

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

interface VerificationNumbers {
  status: string;
  tone: string;
  confidence?: number;
  passed?: number;
  failed?: number;
  total?: number;
  blocking?: number;
}

/** Pull the numeric/status summary out of a verification payload. */
function verificationNumbers(verification: KnowledgeVerificationPayload): VerificationNumbers {
  const status = String(verification.status ?? (verification.verified === true ? 'verified' : 'unknown'));
  const failed = asNumber(verification.failed_checks);
  return {
    status,
    tone: failed !== undefined && failed > 0 ? 'failed' : status.toLowerCase(),
    confidence: asNumber(verification.confidence),
    passed: asNumber(verification.passed_checks),
    failed,
    total: asNumber(verification.total_checks),
    blocking: asNumber(verification.blocking_findings),
  };
}

/** Checks row: `${passed} passed · ${failed} failed[ · N total]`, hidden when absent. */
function VerificationChecksRow(props: {
  passed?: number;
  failed?: number;
  total?: number;
}): ReactElement | null {
  const { passed, failed, total } = props;
  if (passed === undefined && failed === undefined && total === undefined) return null;
  return (
    <div>
      <span className="r4-studio-key">checks</span>
      <span>
        {`${passed ?? 0} passed · ${failed ?? 0} failed${total !== undefined ? ` · ${total} total` : ''}`}
      </span>
    </div>
  );
}

function VerificationCard(props: { verification: KnowledgeVerificationPayload | null }): ReactElement | null {
  const { verification } = props;
  if (verification == null) return null;
  const summary = verificationNumbers(verification);

  return (
    <div className="r4-studio-detail">
      <div className="r4-studio-detail-head">
        <strong>Knowledge verification</strong>
        <span className={`r4-studio-status r4-studio-status-${summary.tone}`}>{summary.status}</span>
      </div>
      <div className="r4-studio-detail-body">
        {summary.confidence !== undefined && (
          <div>
            <span className="r4-studio-key">confidence</span>
            <span>{(summary.confidence * 100).toFixed(0)}%</span>
          </div>
        )}
        <VerificationChecksRow
          passed={summary.passed}
          failed={summary.failed}
          total={summary.total}
        />
        {summary.blocking !== undefined && summary.blocking > 0 && (
          <div>
            <span className="r4-studio-key">blocking findings</span>
            <span>{summary.blocking}</span>
          </div>
        )}
        {typeof verification.updated_at === 'string' && verification.updated_at && (
          <div>
            <span className="r4-studio-key">updated</span>
            <span>{verification.updated_at}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── §8 health / §12 preflight (reconcile-derived, read-only) ────────────

function HealthCard(props: {
  reconcile: KnowledgeReconcilePayload | null;
}): ReactElement | null {
  const { reconcile } = props;
  if (reconcile == null || !reconcile.counts) return null;
  const counts = reconcile.counts;
  const badges = [
    { key: 'COMPLETE', tone: 'ready' },
    { key: 'EMBEDDING_PENDING', tone: 'pending' },
    { key: 'PARTIAL', tone: 'unknown' },
    { key: 'ORPHANED', tone: 'failed' },
  ];
  return (
    <div className="r4-studio-detail">
      <div className="r4-studio-detail-head">
        <strong>knowledge health</strong>
        <span className="r4-studio-pill">
          {reconcile.catalogue_available === false ? 'canonical-only' : `project ${reconcile.project_id}`}
        </span>
      </div>
      <div className="r4-studio-detail-body">
        {badges.map((b) => (
          <div key={b.key}>
            <span className="r4-studio-key">{b.key}</span>
            <span className={`r4-studio-status r4-studio-status-${b.tone}`}>
              {counts[b.key] ?? 0}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Selected node's preflight state from the reconcile asset map. */
function preflightState(
  reconcile: KnowledgeReconcilePayload | null,
  documentId: string | null,
): { state: string; reasons: string[] } | null {
  if (!reconcile?.assets || !documentId) return null;
  const asset = reconcile.assets.find((a) => a.document_id === documentId);
  if (!asset) return null;
  return { state: asset.state, reasons: asset.reasons ?? [] };
}

// ── Multi-graph selector (0069) ─────────────────────────────────────────

/** One graph row with include / neutral / exclude tri-state + on-map badge. */
function GraphRow(props: {
  graph: KnowledgeGraphMeta;
  state: 'include' | 'exclude' | 'neutral';
  /** True when this graph is currently rendered on the graph window. */
  onMap: boolean;
  onChange: (state: 'include' | 'exclude' | 'neutral') => void;
}): ReactElement {
  const { graph, state } = props;
  return (
    <li
      className={`r4-studio-list-item${state !== 'neutral' ? ' r4-studio-list-item-active' : ''}`}
      title={graph.description ?? graph.name}
    >
      <span className="r4-studio-list-title">
        {props.onMap && <span className="r4-kg-onmap-dot" title="Rendered on the graph window" />}
        {graph.name}
      </span>
      <span className="r4-studio-hint">
        {graph.kind ?? 'graph'}
        {graph.project_id ? ` · ${graph.project_id}` : ''}
        {props.onMap ? ' · on map' : ''}
      </span>
      <span className="r4-kg-select-buttons">
        <button
          type="button"
          className={`r4-btn${state === 'include' ? ' r4-btn-active' : ''}`}
          aria-pressed={state === 'include'}
          title={state === 'include' ? 'Included (click to clear)' : 'Always use this graph'}
          onClick={() => props.onChange(state === 'include' ? 'neutral' : 'include')}
        >
          +
        </button>
        <button
          type="button"
          className={`r4-btn${state === 'exclude' ? ' r4-btn-active' : ''}`}
          aria-pressed={state === 'exclude'}
          title={state === 'exclude' ? 'Excluded (click to clear)' : 'Never use this graph'}
          onClick={() => props.onChange(state === 'exclude' ? 'neutral' : 'exclude')}
        >
          −
        </button>
      </span>
    </li>
  );
}

/** Empty selector state (older gateway / registry unavailable). */
function GraphSelectorEmpty(): ReactElement {
  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>Knowledge graphs</span>
      </div>
      <div className="r4-studio-empty">
        No graphs registered yet (older gateway or registry unavailable)
      </div>
    </section>
  );
}

/** Auto-discovery checkbox in the selector header. */
function DiscoveryToggle(props: {
  discovery: boolean;
  onChange: (next: boolean) => void;
}): ReactElement {
  return (
    <label className="r4-studio-hint" title="Let the agent add relevant graphs automatically">
      <input
        type="checkbox"
        checked={props.discovery}
        onChange={(e) => props.onChange(e.target.checked)}
      />{' '}
      auto-discovery
    </label>
  );
}

/** One-line truth of the current include/exclude selection. */
function SelectionSummary(props: { include: string[]; exclude: string[] }): ReactElement {
  return (
    <div className="r4-studio-hint">
      Selected: [{props.include.join(', ') || '—'}] · Excluded: [
      {props.exclude.join(', ') || '—'}]
    </div>
  );
}

type GraphSelectionState = 'include' | 'exclude' | 'neutral';

/** Constraints → per-graph selection state (id or name match). */
function graphSelectionState(
  graph: KnowledgeGraphMeta,
  constraints: KnowledgeGraphConstraints,
): GraphSelectionState {
  if (constraints.include.includes(graph.name) || constraints.include.includes(graph.graph_id)) {
    return 'include';
  }
  if (constraints.exclude.includes(graph.name) || constraints.exclude.includes(graph.graph_id)) {
    return 'exclude';
  }
  return 'neutral';
}

/** Immutable include/exclude cycle for one graph. */
function cycleGraphConstraints(
  graph: KnowledgeGraphMeta,
  state: GraphSelectionState,
  constraints: KnowledgeGraphConstraints,
): KnowledgeGraphConstraints {
  const include = constraints.include.filter((g: string) => g !== graph.name);
  const exclude = constraints.exclude.filter((g: string) => g !== graph.name);
  if (state === 'include') include.push(graph.name);
  if (state === 'exclude') exclude.push(graph.name);
  return { include, exclude, discovery: constraints.discovery };
}

/** Selector card: which knowledge graphs the agent may/must use.
 *  Rows currently rendered on the graph window carry an on-map dot. */
function GraphSelectorCard(): ReactElement | null {
  const { studio } = useApp();
  const graphs = studio.knowledgeGraphs ?? [];
  const constraints = studio.knowledgeGraphConstraints;

  const renderedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const content of studio.selectedGraphContents ?? []) {
      if (content.graph_id) ids.add(content.graph_id);
      if (content.name) ids.add(String(content.name));
    }
    return ids;
  }, [studio.selectedGraphContents]);

  if (graphs.length === 0) {
    return <GraphSelectorEmpty />;
  }

  return (
    <section className="r4-studio-card">
      <div className="r4-studio-card-header">
        <span>Knowledge graphs</span>
        <DiscoveryToggle
          discovery={constraints.discovery}
          onChange={(next) =>
            setKnowledgeGraphConstraints({
              include: constraints.include,
              exclude: constraints.exclude,
              discovery: next,
            })
          }
        />
      </div>
      <SelectionSummary include={constraints.include} exclude={constraints.exclude} />
      <ul className="r4-studio-list" aria-label="Knowledge graphs">
        {graphs.map((graph) => (
          <GraphRow
            key={graph.graph_id}
            graph={graph}
            state={graphSelectionState(graph, constraints)}
            onMap={renderedIds.has(graph.graph_id) || renderedIds.has(graph.name)}
            onChange={(s) =>
              setKnowledgeGraphConstraints(cycleGraphConstraints(graph, s, constraints))
            }
          />
        ))}
      </ul>
    </section>
  );
}

/** Merge a matching detail payload over the selected loop. */
function mergeLoopDetail(
  selectedLoop: ResearchLoop,
  detail: Record<string, unknown>,
): ResearchLoop {
  return {
    ...selectedLoop,
    iterations: (detail.iterations as ResearchLoop['iterations']) ?? selectedLoop.iterations,
    current_iteration: (detail.current_iteration as number | undefined) ?? selectedLoop.current_iteration,
    phase: (detail.phase as string | undefined) ?? selectedLoop.phase,
  };
}

/** Detail overlay for a selected loop: merge only when the detail matches. */
function loopWithFallback(
  selectedLoop: ResearchLoop | undefined,
  detail: Record<string, unknown> | null,
): ResearchLoop | undefined {
  if (!selectedLoop) return undefined;
  if (!detail || String(detail.loop_id ?? '') !== String(selectedLoop.loop_id ?? '')) {
    return selectedLoop;
  }
  return mergeLoopDetail(selectedLoop, detail);
}

// ── Panel root ───────────────────────────────────────────────────────────

export default function KnowledgePanel(): ReactElement {
  const { studio } = useApp();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedLoopId, setSelectedLoopId] = useState<string | null>(null);

  const graph = studio.knowledgeGraph;
  const nodes = (graph?.nodes as KnowledgeNode[] | undefined) ?? [];
  const edges = ((graph?.edges as KnowledgeEdge[] | undefined) ?? []).filter(
    (edge) => Boolean(edge.from) && Boolean(edge.to),
  );
  /* §1/§3 overview: kind + relation tallies from the payload itself
     (read-side only; no new fetch). */
  const graphStats = useMemo(() => {
    const byKind = new Map<string, number>();
    const docs = { count: 0 };
    const chunks = { count: 0 };
    for (const node of nodes.filter(Boolean)) {
      const meta = (node.metadata ?? {}) as Record<string, unknown>;
      if (String(meta.kind ?? '') === 'document') docs.count += 1;
      if (String(meta.kind ?? '') === 'chunk') chunks.count += 1;
      const kind = semanticKind(node);
      byKind.set(kind, (byKind.get(kind) ?? 0) + 1);
    }
    const byRelation = new Map<string, number>();
    for (const edge of edges) {
      const relation = String(edge.label ?? edge.type ?? 'related');
      byRelation.set(relation, (byRelation.get(relation) ?? 0) + 1);
    }
    return { byKind, byRelation, docs: docs.count, chunks: chunks.count };
  }, [nodes, edges]);
  const loops = studio.researchLoops ?? [];

  /* Live growth: poll only while work is in flight (active loops, or an
     ingest was just accepted). knowledgeState also lands mid-turn after
     knowledge tools, so this only bridges background progress (loop
     iterations, durable ingest jobs) — and stops once everything is terminal. */
  const loopsActive = loops.some((loop) =>
    ACTIVE_LOOP_STATUSES.has(String(loop.status ?? '').toLowerCase()),
  );
  const liveActive = loopsActive || studio.knowledgeIngested;
  useEffect(() => {
    if (!liveActive) return undefined;
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') refreshKnowledge();
    }, KNOWLEDGE_POLL_MS);
    return () => clearInterval(timer);
  }, [liveActive]);

  /* Spawn tracking: ids first seen after the initial snapshot get a pulse
     ring + ticker entry, cleared after KNOWLEDGE_SPAWN_MS. */
  const seenRef = useRef<Set<string> | null>(null);
  const [spawned, setSpawned] = useState<Array<{ id: string; label: string; kind: string }>>([]);
  useEffect(() => {
    if (graph == null) return undefined;
    const seen = seenRef.current;
    if (seen == null) {
      seenRef.current = new Set(nodes.map((node) => String(node.id)));
      return undefined;
    }
    const fresh: Array<{ id: string; label: string; kind: string }> = [];
    for (const node of nodes) {
      if (!node) continue;
      const id = String(node.id);
      if (!seen.has(id)) {
        seen.add(id);
        fresh.push({
          id,
          label: String(node.name ?? node.label ?? id).slice(0, 22),
          kind: String(node.type ?? 'knowledge'),
        });
      }
    }
    if (fresh.length === 0) return undefined;
    setSpawned((prev) => [...fresh, ...prev].slice(0, 4));
    const timer = setTimeout(() => setSpawned([]), KNOWLEDGE_SPAWN_MS);
    return () => clearTimeout(timer);
  }, [graph, nodes]);

  const pulsingIds = useMemo(() => new Set(spawned.map((entry) => entry.id)), [spawned]);

  // Selected knowledge graphs (selector +) rendered as satellite clusters.
  // The project's own default graph is already the main constellation — skip
  // it and any nodes duplicated there.
  const satellites = useMemo(() => {
    const mainIds = new Set(nodes.filter(Boolean).map((node) => String(node.id)));
    return (studio.selectedGraphContents ?? [])
      .filter(
        (content) =>
          content != null &&
          !(content.project_id === studio.currentProjectId && content.kind === 'project'),
      )
      .slice(0, 4)
      .map((content) => ({
        ...content,
        nodes: (content.nodes ?? [])
          .filter(Boolean)
          .filter((node) => !mainIds.has(String(node.id))),
      }))
      .filter((content) => (content.nodes ?? []).length > 0);
  }, [studio.selectedGraphContents, studio.currentProjectId, nodes]);

  const selectedNode =
    nodes.find((n) => n && String(n.id) === selectedNodeId) ??
    satellites
      .flatMap((content) => content.nodes ?? [])
      .find((n) => n && String(n.id) === selectedNodeId);
  const selectedLoop = loops.find((l) => String(l.loop_id) === String(selectedLoopId ?? ''));
  const loopWithDetail = useMemo(
    () => loopWithFallback(selectedLoop, studio.researchLoopDetail),
    [selectedLoop, studio.researchLoopDetail],
  );

  return (
    <div className="r4-studio-knowledge-panel">
      {!studio.gatewayOnline && (
        <div className="r4-studio-offline-banner" role="status">
          <span className="r4-loop-pulse" aria-hidden="true" />
          <div>
            <strong>Offline — showing last-known snapshot</strong>
            <div className="r4-studio-hint">
              Chat + custom-model continue to work; knowledge/live loops are stale until the gateway returns.
            </div>
          </div>
        </div>
      )}
      <GraphSelectorCard />
      <IngestCard projectId={studio.currentProjectId} />
      <section className="r4-studio-card">
        <div className="r4-studio-card-header">
          <span>Knowledge graph</span>
          <button type="button" className="r4-btn" title="Reload" onClick={refreshKnowledge}>
            ⟳
          </button>
        </div>
        {graph == null && (
          <div className="r4-studio-empty">No project selected or no knowledge data</div>
        )}
        {graph != null && nodes.length === 0 && (
          <div className="r4-studio-empty">Empty knowledge graph</div>
        )}
        {nodes.length > 0 && (
          <>
            <div className="r4-studio-hint" aria-label="Graph population">
              {graphStats.docs > 0 && `${graphStats.docs} docs · `}
              {graphStats.chunks > 0 && `${graphStats.chunks} chunks · `}
              {nodes.length} nodes ({[...graphStats.byKind.entries()].slice(0, 6).map(([k, n]) => `${k}×${n}`).join(', ')})
              {edges.length > 0 && ` · ${edges.length} edges (${[...graphStats.byRelation.entries()].slice(0, 6).map(([k, n]) => `${k}×${n}`).join(', ')})`}
            </div>
            <KnowledgeConstellationSigma
              nodes={nodes}
              edges={edges}
              satellites={satellites}
              selectedId={selectedNodeId}
              onSelect={setSelectedNodeId}
              pulsing={pulsingIds}
            />
            {spawned.length > 0 && (
              <div className="r4-knowledge-ticker" aria-label="Newest knowledge nodes">
                {spawned.slice(0, 3).map((entry) => (
                  <span key={entry.id} className="r4-knowledge-ticker-item">
                    <span className="r4-dot" style={{ background: typeColor(entry.kind) }} />
                    ＋ {entry.label}
                  </span>
                ))}
              </div>
            )}
            {satellites.length > 0 && (
              <div className="r4-knowledge-legend" aria-label="Graph provenance legend">
                <span>
                  <span className="r4-dot" style={{ background: '#56ccf2' }} /> this project
                </span>
                {satellites.map((content, index) => (
                  <span key={content.graph_id}>
                    <span
                      className="r4-dot"
                      style={{ background: satelliteColor(index) }}
                    />{' '}
                    {content.name ?? content.graph_id}
                  </span>
                ))}
              </div>
            )}
            {selectedNode ? (
              <KnowledgeInspector
                reconcile={studio.knowledgeReconcile}
                node={selectedNode}
                edges={[
                  ...edges,
                  ...satellites.flatMap((content) => content.edges ?? []),
                ]}
                nodes={[
                  ...nodes,
                  ...satellites.flatMap((content) => content.nodes ?? []),
                ]}
              />
            ) : null}
          </>
        )}
        <VerificationCard verification={studio.knowledgeVerification} />
        <HealthCard reconcile={studio.knowledgeReconcile} />
      </section>

      <section className="r4-studio-card">
        <div className="r4-studio-card-header">
          <span>Research loops</span>
          <button type="button" className="r4-btn" title="Reload" onClick={refreshKnowledge}>
            ⟳
          </button>
        </div>
        {loops.length === 0 && <div className="r4-studio-empty">No research loops yet</div>}
        <ul className="r4-studio-list" aria-label="Research loops">
          {loops.map((loop) => {
            const selected = String(loop.loop_id) === String(selectedLoopId ?? '');
            return (
              <li
                key={String(loop.loop_id)}
                className={`r4-studio-list-item${selected ? ' r4-studio-list-item-active' : ''}`}
                onClick={() => setSelectedLoopId(loop.loop_id ?? null)}
                title={loop.research_goal ?? String(loop.loop_id)}
              >
                <span className="r4-studio-list-title">{String(loop.loop_id)}</span>
                <span
                  className={`r4-studio-status r4-studio-status-${String(loop.status ?? 'pending').toLowerCase()}`}
                >
                  {loop.status ?? 'pending'}
                </span>
              </li>
            );
          })}
        </ul>
        {loopWithDetail ? null : (
          <StartResearchLoopCard
            projectId={studio.currentProjectId}
            runtime={studio.loopRuntime}
          />
        )}
        {loopWithDetail && (
          <ResearchLoopCard
            loop={loopWithDetail}
            runtime={studio.loopRuntime}
            onStop={(loopId) => stopResearchLoop(loopId, studio.currentProjectId ?? undefined)}
          />
        )}
      </section>

      <ScienceCatalogCard projectId={studio.currentProjectId} />
    </div>
  );
}
