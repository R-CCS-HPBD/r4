# ADR-073: The loop-trace ↔ companion-session join is owned by UMRS

**Status:** Accepted (2026-09-24, agent decision under the maintainer's delegation of the same date; closes gap row D-52's escalated design steer)
**Date:** 2026-09-24
**Relates to:** D-52 (observability: replay and audit a loop run), ADR-036 (loop hosting, approval inbox), ADR-025 (research sessions), ADR-057 (chat-turn authority)

## Context

A loop carries `metadata.trace_id` (= its id) through its jobs, iterations,
spans and minted findings, and `r4 project loop replay` renders it. The
companion session that *started or steered* the loop was invisible to that
trace. Its id stayed on the workstation: loop calls sent no session header,
and the local `event_log` mentions loop ids only inside tool-result text.
The open question was which plane owns the join.

## Decision

1. **UMRS owns the join.** Both halves are durable there: loops live in
   the loop store, and companion sessions in the journal mirror
   (`companion_session_snapshots`, migration 0080). The client never keeps
   a second copy (CLAUDE.md ownership rule: UMRS owns durable state).
2. **The key is the loop's `trace_id`; the link is recorded at start.**
   The companion binds its session id to the client each turn. Loop start
   and approval decisions send it as `x-r4-session-id`, the header the
   knowledge mutation guard already reads (`request_session_id`). UMRS
   stores it as `metadata.companion_session_id`, separate from the ADR-025
   research `session_id`, and only when well-formed (`[A-Za-z0-9._:-]`,
   ≤128).
3. **Both directions are queryable:** loop → session via the status
   metadata and replay ("started from companion session"); session → loops
   via `GET /umrs/loops?companion_session_id=`. The list is owner-scoped
   (gap §12 V-13), so the filter never reaches another user's loops.

## Rejected options

- **R4 owns the join** (local event log gains loop ids): not durable, not
  visible from other clients, and a second store of loop facts on the
  client.
- **A join table:** the link is one fact per loop, known at start. Loop
  metadata already holds such facts (`trace_id`, `owner_user_id`).

## Consequences

- D-52 is Implemented: a loop run can be replayed (iterations, gate
  decisions with their deciding user, OTel spans by trace id) and traced
  back to the conversation that started it, whose durable journal holds
  the turns and tool calls.
- Tails: recording the *deciding* session on approval decisions needs an
  approval-inbox column across its three backends. The header is already
  sent, so only the store side is missing. The local companion
  `event_log` could index loop ids for offline replay.

## Verification pointers

- `umrs/src/umrs/api/loop.py` — `_companion_session`, start stamp, list filter
- `r4/src/r4/client.py` — `companion_session_id`, `_companion_headers`; `r4/src/r4/companion/session.py` — `_bind_client_session`
- `r4/src/r4/_cli/project_knowledge.py` — replay origin lines
- Tests: `umrs/tests/test_loop_owner_scoping.py` (D-52 section), `r4/tests/test_companion_loop_trace_join.py`
