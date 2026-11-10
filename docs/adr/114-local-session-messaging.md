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
- NOT in scope (slice 2 and later): sessions on other machines and remote venue
  sessions (through UMRS, reusing the server-owned queue: no second store in
  `r4/` or `gateway/`); waking a background tab (needs a tab-targeted send in
  the console); headless and REPL sessions on the bus; a console list/compose
  view; messages between different users (never through this bus: it is
  private per user by construction).

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
tab-targeted background notices and the headless/REPL decision. It is
unstarted. D-69 remains Partial.

## Related

- [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-104](104-one-mode-one-permission-decision.md),
  [ADR-111](111-local-saved-memory.md), [ADR-046](046-remote-session-credential-egress.md)
- `.github/instructions/companion.instructions.md` (module tree)
