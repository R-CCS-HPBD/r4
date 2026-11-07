# ADR-057: Chat-turn identity + durable-chat authority (T4 frame)

**Status:** Proposed (design wave 2026-09-17; legs to land in waves tagged below)
**Implements:** GAP-COMP-3 & G-P6-2 follow-through (production plan §F2;
ADR-050 tail's deferred turn-id accessor)
**Relates to:** ADR-036 (session authority), ADR-036-amendment (loop hosting),
ADR-046 (remote session credentials), ADR-050 (verbatim-history build)

## Context

Today's chat-session authority lives client-local: a per-r4-process SQLite
row (`SessionStore`) whose mirror is only a whole-session snapshot
projection (companion_mirror WP-04: one blob per session with no per-turn
structure). Turn identity lives ONLY inside ephemeral agent step-records
(`turn_id = f"t{self._turn_seq}"` — a process-local sequence). Three
consequences that stop being tolerable:

1. `fork(from_turn)` cannot address the boundary at all: no persistent turn
   index exists to point at, and a fork over a stale snapshot would slice
   the wrong prefix.
2. Chat-turn **pending approvals** ride in-flight tool intents in each r4
   process's `PermissionManager._pending` dicts — disconnected clients can't
   enumerate them, and any restart drops every parked decision (the G-P6-2
   residual).
3. Any server-hosted chat-vs-agent answer has no durable surface to query:
   no turn-level record exists to decide against, only whole-session blobs.

## Decision

### D-1: Turns are assigned retainable identifiers at commit
Each session-turn is assigned a canonical id by the authority side AT the
moment the turn is written:

- Format `t<seq>` matched per-session (monotonic by the committed-write
  count); ids are stable across restart/resume — a resume never remaps ids
  older than the current sequence.
- The current sequence lives ON the session record (`turn_seq` counter on
  the journal row), so a resume never re-invents positions of older turns.
- Honest-empty: a turn without a committed write has NO id; ids are never
  synthesized retroactively (pre-T4 sessions keep an unnumbered-set marker —
  never a synthetic number after the fact).

### D-2: Server accessor is the read authority (never a client)
Two routes under the companion-session umbrella (ADR-036's family):

- `GET /umrs/companion/sessions/<sid>/turns` — ordered turn index (id +
  commit timestamp + per-turn digest, never a truncated quote).
- `GET /umrs/companion/sessions/<sid>/turns/<turn_id>` — full turn payload
  (commit section + prompt + tool intents + decision refs).

Both rows are owner-scoped and verified-identity-gated; a foreign or unknown
submission 404s exactly like the sibling routes (per ADR-036's rule that
no cross-owner snapshot exists anywhere).

### D-3: `fork(from_turn)` slices at the turn boundary
`SessionStore.fork(session_id, new_id, *, from_turn: str | None, new_name="")`:

- `from_turn=None` → whole-copy, byte-identical behavior (byte-compatible
  default).
- `from_turn=<turn_id>` → fork retains **only messages up to and including
  the named turn's commit** (a strict prefix slice).
- An unknown `<turn_id>` raises the NAMED error `fork.turn_not_found`
  (prefix slicing never degenerates into a silent full-copy).
- Server-side source-of-truth rides the WP-04 mirrored payload (server-side
  slicing for remote whose pace of put on clients outruns the local table);
  the local SQL table is used only as a fallback for local-venue rows
  (mouth-wired from mirrored snapshot for anything outside that venue).

### D-4: Pending chat approvals are durable intents
`chat_turn_approvals` mirror intent → decision server-side with the same
contract as the loop inbox (ADR-036):

- pending rows = owner (or intent-assigned) only.
- decision tokens: `once | no | always`; second decision attempts 409
  exactly like the loop path.
- Decisions are write-only-tombstone; no body leak anywhere.
- `durability=postgres` shape matches the adjacent tables.

### D-5: Legacy + edge cases
- Pre-T4 sessions carry no turn ids; the turns route returns rows with a
  `legacy` tag only for unnumbered phases (never a synthetic index).
- Two clients racing the same intent: decision is writer-defined-er (first
  write wins, matching the existing approval-inbox contract).
- Venue order (`local` vs `frontend`): mirrored payload keeps ordering per
  the mirrored snapshot; restore order local-venue-from-source-of-cost fixed
  consistent with WP-04's writer-set rules.

## Consequences

- Chat-session authority transitions from client-local to shared-durable for
  new sessions: the session mirror ledger masters reading; local SQLite
  stays the write cache and never the truth owner for pre-existing rows.
- `fork(from_turn)` becomes deterministic (names, not positions).
- Pending-chat approvals survive a restart across clients (nothing on the
  client side becomes unrecoverable).
- Any client-side tool-pending dict helper dies toward zero; first-run reads
  go through the durable pending list (project-shaped).

## Legs
- **2026-09-17 — Leg 1 landed** (r4 `_start_turn`/`_finish_turn` stamp
  `metadata.turn_id = t<turn_seq>` on the turn's user+assistant messages;
  counter persisted in `session.metadata["turn_seq"]`; restored rows keep
  original ids byte-exact; umrs `/umrs/companion/sessions/{sid}/turns`
  + `/turns/{turn_id}` project from the payload blob — entries without
  markers stay absent, no synthesis), umrs `@c6360a89`; r4 pins
  `test_session.py::TestTurnIds` (2) + umrs pins
  `TestTurnAccessor` (5).
- **2026-09-17 — Leg 2 (client slice) landed**: `CompanionSessionStore.
  fork(from_turn=)` prefix-slices at the named turn
  (`_slice_messages_to_turn`), records `metadata["forked_from_turn"]`,
  resets the fork's own `turn_seq`; unknown ids raise `KeyError(turn_id)`
  — the `fork.turn_not_found` named class; pins
  `test_session_fork.py::TestForkFromTurn` (3).
 (each lands as its own named wave)

1. **Leg 1** — accessor routes + `turn_seq` persistence on the snapshot row.
2. **Leg 2** — `SessionStore.fork(from_turn)` slice client-side (+ named
   error for unknown turn ids).
3. **Leg 3** — `chat_turn_approvals` durable intent table + decisions route.
4. **Leg 4** — `r4 session fork` verb (local-SQL + server-payload
   end-points per venue; --json compatible).
5. **Leg 5** — TUI affordances (set-of-turn fork picker + pending list
   rendered from the durable list, not client dicts) — the TUI session's
   own rolling wave owns this leg.

## Alternatives considered

- Keep the intent matching entirely client-local — the exact defect that
  forces duplicate sessions after a laptop restart (rejected; WP-04's
  mirroring choices already found the wrong answer useful).
- Retro-number every existing message/turn by snapshot path — rejected:
  it inventes meaning over uncommitted data, conflicts writes in any shape,
  and retro synthesis fails us at the exact moment it can't yield.
- Live per-turn mirror-put (instead of whole-session blob commits) —
  smaller request surface but latency-shadowing, and the same accessor is
  needed anyway (follow-up design registered; not folded here).
