import { useApp } from '@/state/store';

const LABELS: Record<string, string> = {
  connecting: 'Connecting…',
  ready: 'Ready',
  disconnected: 'Disconnected',
};

import { post } from '@/lib/acp-bridge';

function classifyContext(pct: number): string {
  if (pct >= 95) return '  ⚠';
  if (pct >= 80) return '  •';
  return '';
}

function BudgetBadge() {
  const { budgets, currentId } = useApp();
  if (!currentId) return null;
  const budget = budgets[currentId];
  if (!budget) return null;
  const { context_used_tokens: used, context_limit_tokens: limit } = budget;
  const pct = limit > 0 ? Math.round((used / limit) * 100) : 0;
  const title =
    `LLM calls: ${budget.llm_calls_used}` +
    (budget.llm_calls_limit != null ? `/${budget.llm_calls_limit}` : '') +
    ` · rounds: ${budget.tool_rounds_used}/${budget.tool_rounds_limit}` +
    ` · context: ${used}/${limit} tokens (${pct}%)`;
  return (
    <span className="r4-budget-badge" title={title} data-testid="budget-badge" data-hard={pct >= 95}>
      ctx {pct}%{classifyContext(pct)} · {budget.tool_rounds_used}/{budget.tool_rounds_limit}r
    </span>
  );
}

/** VP-4b: `team:on · …` while `/teamup on` has armed the next message. */
function TeamArmedChip() {
  const { extras, currentId } = useApp();
  const label = currentId ? extras.teamArmed[currentId] : '';
  if (!label) return null;
  return (
    <span className="r4-status-chip" data-state="warn" title="Your next message starts a team (/teamup off cancels)">
      {label}
    </span>
  );
}

/** VP-3b: `remote:running · lease 47m` for a session on the server (/venue). */
function VenueChip() {
  const { extras, currentId } = useApp();
  const venue = currentId ? extras.venue[currentId] : undefined;
  if (!venue?.chip) return null;
  return (
    <span className="r4-status-chip" data-state={venue.state} title="This session runs on the server (/venue)">
      {venue.chip}
    </span>
  );
}

/** VP-3a: the session's sandbox chip (`sbx:on|off|n/a`, local sessions) and
 * web access (ADR-101, ADR-099); /sandbox and /web change them. */
function PolicyChips() {
  const { extras, currentId } = useApp();
  const policy = currentId ? extras.policy[currentId] : undefined;
  if (!policy) {
    return (
      <>
        <TeamArmedChip />
        <VenueChip />
      </>
    );
  }
  return (
    <>
      <TeamArmedChip />
      <VenueChip />
      {policy.sandboxChip ? (
        <span className="r4-status-chip" title="Local tool sandbox (/sandbox)">
          {policy.sandboxChip}
        </span>
      ) : null}
      {policy.web ? (
        <span className="r4-status-chip" title="The agent's web access in this session (/web)">
          web: {policy.web}
        </span>
      ) : null}
    </>
  );
}

export default function StatusBar() {
  const { status, connected, endpoints, lastError, streaming, currentId } = useApp();

  const handleLogin = () => {
    post({ type: 'login' });
  };

  // V4 status merge: the left segment reports live agent activity when a turn
  // streams (most dynamic), else the connection label.
  const liveStatus = currentId && streaming[currentId] ? 'assistant — streaming…' : LABELS[status] ?? status;

  return (
    <footer className="r4-statusbar">
      <span className={`r4-dot r4-dot-${status}`} />
      <span data-testid="status-live">{liveStatus}</span>
      <span className="r4-statusbar-spacer" />
      <PolicyChips />
      <BudgetBadge />
      {lastError ? (
        <span className="r4-status-error" title={lastError}>
          {lastError}
        </span>
      ) : (
        <span className="r4-status-meta">
          {connected ? (
            `${endpoints.length} endpoint(s)`
          ) : (
            <>
              Gateway offline{' '}
              <button
                type="button"
                className="r4-sign-in-btn"
                onClick={handleLogin}
              >
                Sign in to R4
              </button>
            </>
          )}
        </span>
      )}
    </footer>
  );
}
