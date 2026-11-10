# ADR-114: Messages between one user's R4 sessions — a local session bus, three lead-session tools, the console wake

**Status:** Accepted (2026-11-08, maintainer: "does r4 have a logic that realize communication between sessions? claude already have so r4 needs it" → "yes implement this"; details agent-decided under delegation)
**Date:** 2026-11-08
**Relates to:** ADR-039 (taint fence), ADR-104 (one permission ladder), ADR-111 (lead-session-only tools, the taint rung for memory), ADR-046 (remote sessions, the server-owned turn queue), ADR-108 (work levels), ADR-092 / ADR-072 (one team seam), gap row D-69

## Context

Claude Code lets one running session find another (`ListAgents`) and send it
a message (`SendMessage`); the receiver takes it as a new turn. R4 had
nothing equivalent (verified on `main` at `c5d09177`, 2026-11-08):

- `r4/src/r4/teamup/comms.py` `AgentCommunication` is a ChangeLog the teamup
  orchestrator hands each role in turn inside **one** team session; it is a
  shared log driven by the orchestrator, not peer delivery.
- `teamup/comms.py` `AgentMailbox` (A-804 direct messages) had no caller and
  no test.
- The remote-session queue (`venue_rail.py` `pop_queue`, ADR-046 A2) carries
  the *person's* turns into their own server-side session.
- The approval inbox (ADR-036/066) carries questions from a loop to a person.
- The bridge RPC named `send_message` (`companion-chat.ts`) is the console
  sending the typed turn to its own tab, despite the name.

So a scientist with two console tabs (say a fitting run and the paper draft)
or two terminals could not have one agent hand the other a result or a
request. Two constraints shape the answer: the companion guide's "one loop,
one team engine" (no second team mechanism) and the ownership table (no second
job store, scheduler or queue in `r4/`); and the ADR-039 rule that another
agent's text is untrusted input.

## Decision

**D-1. A local session bus, per user, on disk.** `r4.companion.session_bus`
keeps one private directory (`R4_SESSION_BUS_DIR`, else
`~/.config/r4/session-bus`, mode 0700; files 0600): `sessions/<id>.json`, one
presence entry per live session, and `inbox/<id>/<msg>.json`, one file per
unread message, written to a temporary name and renamed. A session is live
while its heartbeat (the entry's mtime, refreshed every 15 s) is younger than
90 s and, when the entry was written on this host, its pid exists; RCCS shares
`HOME` across hosts, so a pid alone means nothing. Dead entries are pruned with
their inbox. Bodies are capped at 8000 characters, inboxes at 100 unread. The
bus is a delivery channel between one user's own sessions: it schedules
nothing, stores no job, runs no team, and talks to no server.

**D-2. Console sessions are on the bus.** The console bridge
(`ui/session_bus_bridge.py`) registers its active session whenever one becomes
active (create, resume, journal adopt), withdraws the previous one, beats the
heartbeat and leaves at shutdown and at exit. `R4_SESSION_BUS=0` keeps a bridge
off. Headless `r4 -p`, the REPL and remote venue workers are not on the bus in
this slice. The availability gate does not offer them the tools
(`tool_availability._off_session_bus`), so their prompt and tool list are
unchanged; a direct call still refuses by name.

**D-3. Three tools, lead session only.** `session_list` (the user's other live
sessions: id, name, project, cwd, model, mode, host), `session_send` (to an id,
a unique id prefix or a unique name; optional `reply_to`) and `session_inbox`
(read and clear, oldest first). Like the ADR-111 memory tools they are attached
by `CompanionSession`, never through `get_builtin_tools`: team roles, plan
agents, recipes and sub-agents do not message sessions. A `reply_to` must name
a message this session read, and the reply goes only to that message's sender.

**D-4. Another session's words enter only as tainted tool output.** The turn
reminder says how many messages are unread and from whom (sanitized one-line
labels), never their text. The text arrives through `session_inbox`, wrapped
in the T-213 tool-output boundary and registered under a new taint class
`session_message` (`r4_contracts.taint`): the ADR-039 fence asks before a later
call copies it, and the session's sticky untrusted mark is set.

**D-5. A tainted session asks before starting a message.** `session_list` and
`session_inbox` are read-only (`SAFE_TOOLS`). `session_send` asks in chat and
plan modes like any change. A new rung `_rung_relay_taint` (after
`_rung_memory_taint`) asks in every mode, autopilot too, once untrusted content
(RAG, documents, lab files, MCP, web, or another session's message) has entered
the session, so an injected instruction cannot forward itself across the
user's sessions. A reply (`reply_to`) is exempt: it goes back only to the
session that wrote, which already holds that conversation.

**D-6. The console wakes an idle tab.** The bridge's watcher emits
`{"session_inbox": {session_id, unread, from, latest}}` when the unread set
changes. The tab shows one live notice ("✉ n unread messages from …") and,
when it is the active tab and idle, starts one turn per arrival with R4's own
text ("New messages from other R4 sessions arrived. Read them with
session_inbox …"). No sender text reaches that turn. A busy or background tab
is not woken; its next turn carries the reminder. `R4_SESSION_BUS_WAKE=0` turns
the wake off.

**D-7. The terminal can write too.** `r4 session live` lists live sessions;
`r4 session send <id|prefix|name> <text…>` delivers a message from the user's
terminal (`sender_kind: user`, sender `terminal`).

**D-8. `AgentMailbox` is deleted.** It never had a caller. Teamup roles keep the
ChangeLog; sessions use the bus. `ConflictDetector` in the same file is out of
scope here.

### Slice 2.1 amendment (2026-11-10): remote venue and other machines

**D-9. One identity, proven before presence.** The durable companion session id
is the remote address; there is no runtime-id alias and no parent-session
fallback. An owner client binds its local-bus `session_id` at
`PUT /umrs/companion/sessions/{sid}/session-bus`; a venue worker binds through
its existing lease-only rail. In both cases UMRS locks and reads the same
owner-scoped `companion_session_snapshots` row named by `{sid}` and refuses the
bind unless its decoded payload `id`, its `session_id`, and the requested bus
identity are all exactly `{sid}`. The binding carries only the existing bus
labels plus venue, timestamps and a 90-second expiry, under
`metadata.session_bus` **in that same snapshot payload**. Presence listing shows
only unexpired bindings belonging to the verified owner. The console's normal
snapshot writes preserve this server-owned binding just as they preserve the
server queue. A frontend console mirror does not register a second local
identity for the worker's session. `R4_SESSION_BUS=0` does not bind, poll or
advertise anything.

**D-10. ADR-046 P8's existing turn queue is the only remote message store.** A
remote `session_message` is an entry in the same
`companion_session_snapshots.payload.metadata.chat_queue` list that owner-turn
append/pop/clear and the venue rail already mutate transactionally. There is no
new table, database, outbox, retry store, gateway state, daemon, scheduler or
team mechanism. An entry adds `kind="session_message"`, its client-generated
`msg-*` id, recipient, sender labels/kind, optional `reply_to`, server
`added_at`/`expires_at`, and delivery/read markers; ordinary `{id, content,
client_id, added_at}` turns keep their existing append/pop/clear behavior. The
id, sender and recipient are validated against durable sessions of the one
verified owner. The recipient named in the entry must equal the queue owner's
session id. Unknown, cross-user, archived/stopped/completed/error, self, or
identity-mismatched recipients are refused without disclosure. An exact durable
id may accept a message while its previously bound receiver is offline; no
presence is fabricated and a never-bound or mirror-off session is not remotely
addressable.

**D-11. Expiry, replay and read are queue transitions.** The server, not the
sender, stamps expiry (24 hours by default, `UMRS_SESSION_MESSAGE_TTL_SECONDS`).
Expired session entries are removed during queue work; ordinary turns are not
expired. Repeating the same message id to the same recipient with the identical
sender/body/reply tuple returns the existing receipt without another delivery;
a different tuple is a conflict. Read entries remain in this same queue as
non-unread deduplication markers until expiry (or the owner's explicit queue
clear), so replay suppression survives a worker restart without an inbox table.
Digest mismatches never overwrite a marker. The unread cap and 8000-character
body cap are the local bus's 100/8000 limits. `reply_to` is valid only when the
sender's queue holds that already-read message and its original sender is the
reply recipient. `session_inbox` returns bodies only through the existing
`session_message`-tainted tool output and marks entries read atomically in queue
order. Notices and reminders continue to carry counts and sanitized labels,
never plaintext bodies or sender-authored wake text. These owner/lease routes
are private surfaces, not a public inbox export or an address-discovery API.

**D-12. Venue delivery preserves FIFO without turning a message into a user
turn.** The existing worker rail's queue-pop operation skips expired/read or
already-notified session entries. For the oldest unnotified message it marks
the entry notified, leaves it durable and unread, and starts one turn containing
only R4's fixed instruction to call `session_inbox`; the body never becomes a
user prompt. `session_inbox` then reads/marks messages FIFO while ordinary
turn entries retain their relative order. An ordinary turn ahead of a message
runs first; an older ordinary turn behind a notified message is not blocked by
that durable unread marker. Cancellation before reading leaves the entry
unread, and cancellation/stop never replaces its body with a wake prompt.
Queue clear cancels every pending item using the existing semantics. Local
bridges poll their own bound queue from the existing bus watcher and merge
local and remote unread rows by send time. Queue outages are reported and never
fall back to a different recipient/store. Local-only bus behavior, direct-call
refusal, relay permission rungs, stable-prefix mode and both bus/snapshot
opt-outs are unchanged. S2.2 is specified by D-13 below; S2.3
headless/REPL presence remains undecided and unimplemented.

### Slice 2.2 amendment (2026-11-10): explicit tab-targeted notices

**D-13. A notice belongs to its named tab and bound session, never to an
implicit active tab.** The console's per-bridge listener must supply a non-empty
open tab id, and the `session_inbox` payload's `session_id` must equal that tab's
currently bound session. Unknown, unnamed, closed, unbound or stale-session
targets drop the notice; none falls back to the active tab and none broadcasts
to every tab. Delivery uses the existing tab-targeted store scope so a background
arrival updates only its owner's one live notice and tab-attention bookkeeping.

This does **not** add background work. A busy or background target does not issue
`send_message`; activating a tab with an already-seen unread set also does not
replay a wake. Only a newly arrived, not-yet-coalesced notice for the active idle
target may start a turn, with the same fixed R4 prompt and once-per-`latest`
coalescing as D-6. `R4_SESSION_BUS_WAKE=0` still leaves the notice but disables
that turn. Extra payload fields cannot supply a wake prompt or body: message
text still enters only through the `session_message`-tainted `session_inbox` tool.
D-1…D-12, one-user identity, local/remote queues, taint and relay permissions are
unchanged. S2.3 headless/REPL presence is not part of this amendment.

### Why

- Files under the user's own config home need no daemon, no port and no
  credential, survive a bridge crash (stale entries expire), and work the same
  on Linux and macOS. The one-file-per-message rename makes concurrent writers
  safe without locks.
- Reusing the existing taint fence instead of a new trust path keeps one rule:
  anything another agent wrote is untrusted, however friendly the sender.
- Waking only with R4's own words means a sender can make the receiver *look*,
  never make it read an instruction as the user's.
- Lead-session-only tools keep teams inside the one team seam (ADR-072/092):
  the bus connects sessions, it does not orchestrate them.

## Consequences

- Positive: two console tabs or terminals of one user can hand each other
  results and requests; scripts can message a running session
  (`r4 session send`).
- Cost: one daemon thread per bridge (2 s poll); a session in autopilot that
  has read the web asks before messaging another session first.
- NOT in scope after the 2026-11-10 S2.1/S2.2 amendments: automatic work in a
  busy or background tab (D-13 delivers only its notice); headless and REPL
  sessions on the bus; a console list/compose view; messages between different
  users (never through this bus: it is private per user by construction). Remote
  venue/other-machine identity and queue behavior remain D-9…D-12; S2.3 remains
  a separate decision.

## Alternatives Considered

- **Wire teamup's `AgentMailbox`** — it addresses roles inside one team
  session, not independent sessions, and would grow a second team mechanism.
- **A local socket or daemon** — needs a broker process and port management for
  no gain over atomic files at this message rate.
- **UMRS first** — every message would need the edge and a login, and local
  tabs would stop working offline; kept for the cross-machine slice.
- **Inject the message as the receiver's user turn** — makes another agent's
  text look like the user's; rejected for D-4/D-6.

*2026-11-09, ADR-104 amendment 3:* autopilot auto-answers the local
`session_send` taint prompt with an audit note; chat/plan retains it. Tainted
message framing, reply targeting, lead-session-only tools and receiver policy
remain unchanged.

## Rollout-status (2026-11-08)

Slice 1 landed repo-side on `wave/session-messaging` (r4 + console; no umrs
change): D-1…D-8. Tests: `r4/tests/test_companion/test_session_bus.py` (29:
the bus, the tools, the relay rung, the bridge attachment and watcher, the CLI,
lead-session wiring); `packages/r4-tui-v3/tests/session-inbox.test.ts` (5: the
channel, the notice, one wake per arrival, no sender text, no wake when busy,
in the background or switched off). Live leg open: two console tabs on one
workstation exchanging a message (owner's try).

## Rollout-status (2026-11-10): local live receipt and wake fix

**Slice 1's local live leg is now verified.** Two real source-built consoles
ran independent bridge processes against an isolated HOME and a local fake
OpenAI-compatible model. First-use web access was off; no personal config,
network model, deployment or cluster was involved. The receiving console's
actual `session_inbox` initially failed with
`tool_contract:refused_this_turn`: the active skill filter omitted the three
lead-session tools. `ALWAYS_ON_TOOL_NAMES` now includes them, as for the
ADR-111 memory tools (off-bus availability filtering and team-role exclusion
remain unchanged). The fail-before regression is
`test_active_skill_filters_keep_the_lead_session_messaging_tools`.

After that fix, the same test proved:

- `session_list` discovered the other independent session;
- `session_send` passed the real one-time approval and delivered
  `BUS-LIVE-RESULT-7`;
- the receiving idle tab displayed the unread notice, auto-started its wake,
  and successfully read that exact body (no tool-contract refusal);
- `r4 session send` returned `BUS-LIVE-RETURN-8`, which the first tab woke and
  read successfully;
- the successful tool results carry the `session_inbox` taint wrapper;
  screenshot evidence contains notice and wake text only, not sender-authored
  wake instructions.

Unit evidence is now 30 session-bus tests and 5 console inbox tests. Final PTY
evidence is `/tmp/opencode/session-bus-pty-evidence-12` on the validation host;
the full consolidated gate is recorded in wave ledger (260).

The separate slice-2 plan
tracks remote/other-machine delivery through ADR-046's server-owned queue,
tab-targeted background notices and the headless/REPL decision. D-69 remains Partial.

## Rollout-status (2026-11-10): S2.1 repo-side, live acceptance pending

S2.1 is implemented on `wave/session-messaging-s2-1`; UMRS
`0eb22a2938e218d423af2b2e6866f9e187fe1cea` is on UMRS-org's
`ai4s-platform` branch and the parent PR carries its submodule pointer.

- D-9's exact durable/local identity check and expiring owner presence share the
  existing snapshot payload. Owner clients bind via JWT; venue workers bind via
  the lease rail only. No runtime-id alias or parent fallback exists.
- D-10…D-11 keep messages and their notification/read/replay markers in the
  existing `chat_queue`, with recipient validation, offline delivery, expiry,
  idempotency, ordering, cancellation and cross-user refusal. Snapshot mirrors
  preserve both server-owned fields. The companion error taxonomy adds the
  named refusals; no migration or new store was introduced.
- D-12's R4 owner adapter merges remote and local presence/unread rows and the
  venue adapter fixed-wake path never treats sender text as a user turn.
  Long-running workers renew through their existing heartbeat. Local-only bus,
  opt-out, direct-call refusal, relay permission and stable-prefix tests remain
  green; S2.2 and S2.3 are untouched.

Fail-before evidence: `/tmp/opencode/s21-umrs-fail-before.log`
(**7 failed / 65 passed**) and `/tmp/opencode/s21-r4-fail-before.log`
(**7 failed / 32 passed**), with one additional worker identity-mismatch test
added during the final review. Final focused evidence: **95** R4 messaging and
compatibility tests and **126** UMRS route/queue/scope tests. The final
worktree-local `bash scripts/ci-lint-local.sh --files …` run printed **ALL
CHECKS PASSED**, with the full R4 suite and **11,890 UMRS passes / 327 skips**;
`--umrs-changed` also passed. Serial Codacy analysis found no new issues (two
older reimport notes in touched files). Logs are under `/tmp/opencode` with the
`s21-` prefix.

**No live venue or other-machine acceptance is claimed.** The remaining owner
leg is a separately authorized deployment followed by two venue-backed consoles
exchanging one message with the queue receipt and both journal histories. D-69
stays Partial; S2.2 is recorded separately below and headless/REPL presence
(S2.3) remains unimplemented.

## Rollout-status (2026-11-10): S2.2 repo-side named-tab delivery

S2.2 is implemented on `wave/session-messaging-s2-2`, stacked on PR #840 head
`d14d65976345da7f6d7504c9eb7cbc9156d48939` (which contains main `34be4f31`).
UMRS remains `0eb22a2938e218d423af2b2e6866f9e187fe1cea`; there is no submodule,
queue, schema, venue, gateway or credential change in this slice.

D-13 closes two fail-before gaps in the console handler: a background arrival
could write the active tab's notice when handled outside the routing wrapper,
and an unnamed/unknown/stale-session target could notify or wake the active tab.
The handler now requires an exact named-tab/session match and scopes its notice
through the existing `applyOnTab` primitive. The normal per-bridge router and
idle-active wake are otherwise unchanged; no background or activation-triggered
turn, broadcast, sender-authored prompt or body-carrying notice was added.

Fail-before evidence: `/tmp/opencode/s22-console-fail-before.log` (**2 failed /
6 passed**) on the old code. Final focused evidence: **8 console inbox tests**
and **35 Python local/remote session-bus tests**, plus 40 venue-worker/real-bridge
compatibility tests. Full console check: typecheck, **2939 passed / 37 skipped /
0 failed**, and smoke. The required changed-files gate printed **ALL CHECKS
PASSED (0 warnings)**; the pre-commit gate is recorded in wave ledger (266).
D-69 stays **Partial**: S2.1's separately authorized live venue
receipt and S2.3 remain open, and this wave ran no interactive/live acceptance,
merge, deployment, host action or public export.

## Related

- [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-104](104-one-mode-one-permission-decision.md),
  [ADR-111](111-local-saved-memory.md), [ADR-046](046-remote-session-credential-egress.md)
- `.github/instructions/companion.instructions.md` (module tree)
