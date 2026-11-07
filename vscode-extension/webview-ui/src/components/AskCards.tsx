/**
 * The console's inline cards for the bridge backend (ADR-103 VP-2): tool
 * approval (with the server-computed diff), agent question, plan
 * confirmation. Same verbs and keys as the console: y / Enter allow once,
 * a always this session, p always for this project, n reject; plan a / m / n.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useApp } from '@/state/store';
import { post } from '@/lib/acp-bridge';
import type { ApprovalAsk, ApprovalDecision, PlanAction } from '@/types/protocol';
import type { AsksSlice, QuestionAsk } from '@/state/asks';

const DECISION_LABEL: Record<ApprovalDecision, string> = {
  once: 'Allow once (y)',
  always: 'Always this session (a)',
  reject: 'Reject (n)',
};

/** The console's approval keys. */
export const DECISION_KEYS: Record<string, ApprovalDecision> = {
  y: 'once',
  Enter: 'once',
  a: 'always',
  n: 'reject',
  Escape: 'reject',
};

export const PLAN_KEYS: Record<string, PlanAction> = { a: 'execute_auto', m: 'execute_ask', n: 'keep_planning', Escape: 'keep_planning' };

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="r4-ask-diff">
      {diff.split('\n').map((line, i) => (
        <div key={i} className={line.startsWith('+') ? 'r4-diff-add' : line.startsWith('-') ? 'r4-diff-del' : undefined}>
          {line || ' '}
        </div>
      ))}
    </pre>
  );
}

function useAutoFocus<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => ref.current?.focus(), []);
  return ref;
}

function ApprovalCard({ ask }: { ask: ApprovalAsk }) {
  const ref = useAutoFocus<HTMLDivElement>();
  const answer = (decision: ApprovalDecision) => post({ type: 'approvalDecision', id: ask.id, decision });
  const onKey = (event: KeyboardEvent) => {
    const decision = DECISION_KEYS[event.key];
    if (!decision || !ask.options.includes(decision)) return;
    event.preventDefault();
    answer(decision);
  };
  return (
    <div ref={ref} className="r4-ask-card" tabIndex={0} onKeyDown={onKey} role="alertdialog" aria-label={`Approve ${ask.tool}`}>
      <div className="r4-ask-title">
        Approve <code>{ask.tool}</code>
        {ask.agent ? <span className="r4-ask-agent"> · asked by {ask.agent}</span> : null}
      </div>
      {ask.description ? <div className="r4-ask-desc">{ask.description}</div> : null}
      {ask.fenced ? <div className="r4-ask-fence">fenced: {ask.fenceReason || 'this call only'}</div> : null}
      {ask.path ? <div className="r4-ask-path">{ask.path}</div> : null}
      {ask.diff ? <DiffBlock diff={ask.diff} /> : null}
      <div className="r4-ask-actions">
        {ask.options.map((decision) => (
          <button
            key={decision}
            type="button"
            className={decision === 'once' ? 'r4-btn r4-btn-primary' : 'r4-btn'}
            onClick={() => answer(decision)}
          >
            {DECISION_LABEL[decision]}
          </button>
        ))}
      </div>
    </div>
  );
}

function QuestionCard({ ask }: { ask: QuestionAsk }) {
  const [text, setText] = useState('');
  const ref = useAutoFocus<HTMLInputElement>();
  const answer = (value: string | null) => post({ type: 'askUserAnswer', id: ask.id, answer: value });
  return (
    <div className="r4-ask-card" role="dialog" aria-label="Agent question">
      <div className="r4-ask-title">The agent asks</div>
      <div className="r4-ask-desc">{ask.question}</div>
      {ask.options.length > 0 ? (
        <div className="r4-ask-actions">
          {ask.options.map((option) => (
            <button key={option} type="button" className="r4-btn" onClick={() => answer(option)}>
              {option}
            </button>
          ))}
        </div>
      ) : null}
      <form
        className="r4-ask-actions"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) answer(text.trim());
        }}
      >
        <input
          ref={ref}
          className="r4-ask-input"
          value={text}
          placeholder="Your answer"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') answer(null);
          }}
        />
        <button type="submit" className="r4-btn r4-btn-primary" disabled={!text.trim()}>
          Answer
        </button>
        <button type="button" className="r4-btn" onClick={() => answer(null)}>
          Decline
        </button>
      </form>
    </div>
  );
}

function PlanCard({ request }: { request: string }) {
  const ref = useAutoFocus<HTMLDivElement>();
  const run = (action: PlanAction) => post({ type: 'confirmPlan', action });
  const onKey = (event: KeyboardEvent) => {
    const action = PLAN_KEYS[event.key];
    if (!action) return;
    event.preventDefault();
    run(action);
  };
  return (
    <div ref={ref} className="r4-ask-card" tabIndex={0} onKeyDown={onKey} role="alertdialog" aria-label="Plan ready">
      <div className="r4-ask-title">Plan ready{request.trim() ? `: ${request.trim()}` : ''}</div>
      <div className="r4-ask-actions">
        <button type="button" className="r4-btn r4-btn-primary" onClick={() => run('execute_auto')}>
          Run, auto-approve tools (a)
        </button>
        <button type="button" className="r4-btn" onClick={() => run('execute_ask')}>
          Run, ask for each tool (m)
        </button>
        <button type="button" className="r4-btn" onClick={() => run('keep_planning')}>
          Keep planning (n)
        </button>
      </div>
    </div>
  );
}

/** The open asks of one session; null when there are none. */
function openAsks(asks: AsksSlice, sessionId: string | undefined) {
  if (!sessionId) return null;
  const approvals = asks.approvals[sessionId] ?? [];
  const questions = asks.questions[sessionId] ?? [];
  const plan = asks.planConfirm[sessionId];
  const empty = approvals.length === 0 && questions.length === 0 && plan === undefined;
  return empty ? null : { approvals, questions, plan };
}

/** The open cards of the session on screen, oldest first. */
export default function AskCards() {
  const { currentId, asks } = useApp();
  const open = openAsks(asks, currentId);
  if (!open) return null;
  const { approvals, questions, plan } = open;
  return (
    <section className="r4-ask-stack" aria-label="Waiting for you">
      {approvals.map((ask) => (
        <ApprovalCard key={`a-${ask.id}`} ask={ask} />
      ))}
      {questions.map((ask) => (
        <QuestionCard key={`q-${ask.id}`} ask={ask} />
      ))}
      {plan !== undefined ? <PlanCard request={plan} /> : null}
    </section>
  );
}
