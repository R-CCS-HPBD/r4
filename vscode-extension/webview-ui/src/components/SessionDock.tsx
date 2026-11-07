/**
 * What the session holds beside the transcript (ADR-103 VP-2b, bridge
 * backend): messages queued behind the running turn, the plan (todos), and
 * the files the session changed. A file opens in VS Code's diff editor;
 * revert asks twice, like the console's /changes `r` → `R`.
 */

import { useState } from 'react';
import { openChange, revertChange } from '@/lib/actions';
import { PLAN_GLYPH, planSummary, type ExtrasSlice } from '@/state/extras';
import { useApp } from '@/state/store';
import type { ChangedFile, PlanStepRow } from '@/types/protocol';

function QueueRow({ items }: { items: string[] }) {
  return (
    <div className="r4-dock-row" aria-label="Queued messages">
      <span className="r4-dock-label">queued {items.length}</span>
      {items.map((item, i) => (
        <span key={i} className="r4-dock-chip" title={item}>
          {item.length > 60 ? `${item.slice(0, 57)}…` : item}
        </span>
      ))}
    </div>
  );
}

function PlanPanel({ steps }: { steps: PlanStepRow[] }) {
  return (
    <details className="r4-dock-panel" open>
      <summary className="r4-dock-label">todos · {planSummary(steps)}</summary>
      <ul className="r4-dock-list">
        {steps.map((step) => (
          <li key={step.id} className={`r4-plan-${step.status}`} style={{ paddingLeft: `${step.depth * 14}px` }}>
            <span className="r4-plan-glyph">{PLAN_GLYPH[step.status] ?? '·'}</span> {step.title}
          </li>
        ))}
      </ul>
    </details>
  );
}

function FileRow({ file }: { file: ChangedFile }) {
  const [armed, setArmed] = useState(false);
  return (
    <li className={file.reverted ? 'r4-file-reverted' : undefined}>
      <button type="button" className="r4-file-open" title="Open the diff" onClick={() => openChange(file.path)}>
        {file.path}
      </button>
      <span className="r4-file-stat">
        {file.created ? 'new · ' : ''}+{file.inserted} −{file.deleted}
        {file.reverted ? ' · reverted' : ''}
      </span>
      {!file.reverted ? (
        <button
          type="button"
          className={armed ? 'r4-btn r4-file-revert-armed' : 'r4-btn'}
          title={armed ? 'Click again to put the file back' : 'Revert this file to before the session changed it'}
          onClick={() => {
            if (armed) revertChange(file.path);
            setArmed(!armed);
          }}
          onBlur={() => setArmed(false)}
        >
          {armed ? 'Confirm revert' : 'Revert'}
        </button>
      ) : null}
    </li>
  );
}

/** `focus` counts `/changes` asks: each one reopens the panel (a new key remounts it open). */
function FilesPanel({ files, focus }: { files: ChangedFile[]; focus: number }) {
  return (
    <details className="r4-dock-panel" key={focus} open={focus > 0 ? true : undefined}>
      <summary className="r4-dock-label">✎ {files.length} file{files.length === 1 ? '' : 's'} changed</summary>
      <ul className="r4-dock-list">
        {files.map((file) => (
          <FileRow key={file.path} file={file} />
        ))}
      </ul>
    </details>
  );
}

function filesFocus(extras: ExtrasSlice, sessionId: string | undefined): number {
  const focus = sessionId ? extras.dockFocus[sessionId] : undefined;
  return focus?.panel === 'files' ? focus.seq : 0;
}

function listOf<T>(map: Record<string, T[]>, sessionId: string): T[] {
  return map[sessionId] ?? [];
}

/** The dock's rows for one session, or null when it holds nothing. */
function dockRows(extras: ExtrasSlice, sessionId: string | undefined) {
  if (!sessionId) return null;
  const rows = { queue: listOf(extras.queue, sessionId), steps: listOf(extras.plan, sessionId), files: listOf(extras.files, sessionId) };
  return rows.queue.length + rows.steps.length + rows.files.length > 0 ? rows : null;
}

export default function SessionDock() {
  const { currentId, extras } = useApp();
  const rows = dockRows(extras, currentId);
  if (!rows) return null;
  return (
    <section className="r4-dock" aria-label="Session">
      {rows.queue.length > 0 && <QueueRow items={rows.queue} />}
      {rows.steps.length > 0 && <PlanPanel steps={rows.steps} />}
      {rows.files.length > 0 && <FilesPanel files={rows.files} focus={filesFocus(extras, currentId)} />}
    </section>
  );
}
