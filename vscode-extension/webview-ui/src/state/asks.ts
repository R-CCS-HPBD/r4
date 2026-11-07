/**
 * The inline cards of the bridge backend (ADR-103 VP-2): tool approvals,
 * agent questions, the plan confirmation and a draft handed back after a
 * refused turn. Pure reducer group, slotted into the store's chain.
 */

import type { ApprovalAsk, InboundEvent } from '@/types/protocol';

export interface QuestionAsk {
  id: string | number;
  question: string;
  options: string[];
}

export interface AsksSlice {
  /** Open approval cards per session, oldest first (each is answered on its own). */
  approvals: Record<string, ApprovalAsk[]>;
  questions: Record<string, QuestionAsk[]>;
  /** A plan waiting for a/m/n, by session (the request it plans). */
  planConfirm: Record<string, string>;
  /** The latest draft to put back in the composer; `seq` makes repeats distinct. */
  restoreDraft?: { sessionId: string; text: string; seq: number; force?: boolean };
}

export const initialAsks: AsksSlice = { approvals: {}, questions: {}, planConfirm: {} };

function without<T extends { id: string | number }>(list: T[] | undefined, id: string | number): T[] {
  return (list ?? []).filter((item) => item.id !== id);
}

function withKey<T>(map: Record<string, T>, key: string, value: T | undefined): Record<string, T> {
  const next = { ...map };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

function onApproval(asks: AsksSlice, event: Extract<InboundEvent, { type: 'approvalRequest' }>): AsksSlice {
  const { type: _type, sessionId, ...ask } = event;
  const list = without(asks.approvals[sessionId], ask.id);
  return { ...asks, approvals: { ...asks.approvals, [sessionId]: [...list, ask] } };
}

function onQuestion(asks: AsksSlice, event: Extract<InboundEvent, { type: 'askUserRequest' }>): AsksSlice {
  const list = without(asks.questions[event.sessionId], event.id);
  const ask = { id: event.id, question: event.question, options: event.options };
  return { ...asks, questions: { ...asks.questions, [event.sessionId]: [...list, ask] } };
}

/** A new message or a stop ends the plan question for that session. */
function closePlan(asks: AsksSlice, sessionId: string): AsksSlice {
  if (!(sessionId in asks.planConfirm)) return asks;
  return { ...asks, planConfirm: withKey(asks.planConfirm, sessionId, undefined) };
}

type Handler = (asks: AsksSlice, event: any) => AsksSlice;

const HANDLERS: Record<string, Handler> = {
  approvalRequest: onApproval,
  approvalResolved: (asks, e) => ({
    ...asks,
    approvals: { ...asks.approvals, [e.sessionId]: without(asks.approvals[e.sessionId], e.id) },
  }),
  askUserRequest: onQuestion,
  askUserResolved: (asks, e) => ({
    ...asks,
    questions: { ...asks.questions, [e.sessionId]: without(asks.questions[e.sessionId], e.id) },
  }),
  planConfirm: (asks, e) => ({ ...asks, planConfirm: withKey(asks.planConfirm, e.sessionId, e.request || ' ') }),
  restoreDraft: (asks, e) => ({
    ...asks,
    restoreDraft: { sessionId: e.sessionId, text: e.text, seq: (asks.restoreDraft?.seq ?? 0) + 1, force: Boolean(e.force) },
  }),
  userMessage: (asks, e) => closePlan(asks, e.sessionId),
  cancelled: (asks, e) => closePlan(asks, e.sessionId),
};

/** The asks after one event; the same object when the event is not an ask. */
export function reduceAsks(asks: AsksSlice, event: InboundEvent | { type: string }): AsksSlice {
  const handler = HANDLERS[event.type];
  return handler ? handler(asks, event) : asks;
}
