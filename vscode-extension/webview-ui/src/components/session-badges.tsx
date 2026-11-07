/**
 * The console's Sessions pane details for the bridge backend (ADR-103
 * VP-3a): the venue glyph (L local · F server · C cluster), state, sync and
 * archived badges, and the server-side filters (state, venue, archives).
 */

import { post } from '@/lib/acp-bridge';
import { useApp, useAppDispatch } from '@/state/store';
import type { SessionFilterState, SessionSummary } from '@/types/protocol';

const VENUE_GLYPH: Record<string, string> = { local: 'L', frontend: 'F', cluster: 'C' };
const STATES = ['', 'ongoing', 'stopped', 'completed', 'error', 'archived'];
const VENUES = ['', 'local', 'frontend'];

/** "L · ongoing · mirrored · ⛁" for a row (absent fields leave nothing). */
export function sessionBadges(session: SessionSummary): string {
  const parts = [
    session.venue ? VENUE_GLYPH[session.venue] ?? session.venue : '',
    session.state ?? '',
    session.sync ?? '',
    session.archived ? '⛁' : '',
  ];
  return parts.filter(Boolean).join(' · ');
}

/** The filters the console's `s` / `v` / `A` keys cycle, sent to the server. */
export function ServerSessionFilters() {
  const { extras } = useApp();
  const dispatch = useAppDispatch();
  const filter = extras.sessionFilter;
  const apply = (next: Partial<SessionFilterState>) => {
    const merged = { ...filter, ...next };
    dispatch({ type: 'sessionFilterState', filter: merged });
    post({ type: 'sessionFilter', ...merged });
  };
  return (
    <div className="r4-sessions-filter" aria-label="Session filters">
      <select className="r4-select r4-session-filter" value={filter.state} title="State" onChange={(e) => apply({ state: e.target.value })}>
        {STATES.map((s) => (
          <option key={s} value={s}>{s || 'all states'}</option>
        ))}
      </select>
      <select className="r4-select r4-session-filter" value={filter.venue} title="Venue" onChange={(e) => apply({ venue: e.target.value })}>
        {VENUES.map((v) => (
          <option key={v} value={v}>{v || 'all venues'}</option>
        ))}
      </select>
      <label className="r4-session-filter-archives" title="Include archived sessions">
        <input type="checkbox" checked={filter.includeArchived} onChange={(e) => apply({ includeArchived: e.target.checked })} />
        archives
      </label>
    </div>
  );
}
