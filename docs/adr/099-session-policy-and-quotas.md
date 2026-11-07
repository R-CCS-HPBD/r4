# ADR-099: Session policy, quotas and local/remote parity

**Status:** Accepted (2026-10-23, under the maintainer decisions M-4, M-5 and M-6 of 2026-10-23; design by the agent under the 2026-09-24 delegation)
**Date:** 2026-10-23
**Relates to:** remote-session platform plan (P-3, §2.3, C-2, C-3, C-5, S-8, E-4, wave RS-1); [ADR-046](046-remote-session-credential-egress.md) (inference proxy, rail, T4 approvals, P9/P10); [ADR-097](097-live-project-membership.md) (live membership; the start's project is checked); ADR-051 D-2 (agent process budget); ADR-057 D-3 (durable chat approvals); ADR-062 (data class gate); ADR-098 (session hosts; reserved, not written)

## Context

Remote sessions run in a sandbox on umrs-front (ADR-046 Amendment 2). What
governs them today is a handful of site environment variables and five
per-start options. Verified in code on `origin/main` `c17842f4` / umrs
`51fb3651`, 2026-10-23:

- **Web access.** The start body takes `web_policy` (`public | allowlist |
  none`) and `web_allow_domains` (`umrs/src/umrs/api/companion_venue.py`
  `VenueStartBody`). An empty value falls to `UMRS_VENUE_WEB_POLICY_DEFAULT`,
  default `public` (`venues/frontend_config.py`, `frontend_service.py`
  `_validated_policy`). Squid enforces it: `allowlist` renders
  `/etc/squid/allow.txt` with squid `dstdomain` semantics (`arxiv.org` = that
  host, `.arxiv.org` = the host and its subdomains), and `none` starts no
  squid (`venues/frontend_podman.py` `render_squid_conf`). The TUI's remote
  source picker preselects `public` and cycles it with `w`
  (`packages/r4-tui-v3/src/components/remote-source-picker-overlay.tsx`);
  `r4 session start --web` is optional and has no domains flag
  (`r4/src/r4/_cli/sessions.py`).
- **Local web access.** The local agent's web tools are `web_fetch` (direct
  `urllib`, no check at all, although its description says "Subject to
  EgressGuard policy") and `web_search` (offered only when a callback is
  wired, which no surface does) in `r4/src/r4/agent/tools.py`, reached by the
  companion through `companion/tools.py` `_agent_builtin_tools`. Shell and
  Python tools have the user's network.
  *(2026-10-30: `web_search` now has a built-in backend,
  `r4/src/r4/agent/web_search.py`: a SearXNG instance named by
  `R4_WEB_SEARCH_URL`, else the local one `r4 search-server up` runs
  ([ADR-102](102-local-searxng-for-web-search.md)); the gate offers the tool
  once one of them or a callback exists, and the callback still replaces it. A web tool the
  session's choice lets run is also outside every skill limit
  (`tool_availability.web_tool_enabled`): before, only the `research` skill
  listed them, so a `public` session refused both on most turns. Wave ledger
  (111).)*
- **Lifetime.** Idle 30 min (`UMRS_VENUE_IDLE_SECONDS`) and a 7-day lease cap
  (`UMRS_VENUE_MAX_LIFETIME_SECONDS`), enforced by the `frontend_session`
  status checker (`jobs/frontend_session_backend.py` `_idle`, `_check_live`).
- **Concurrency.** One budget of 4 "agent processes" per user counts remote
  sessions, companion workers and loop jobs together
  (`api/agent_quota.py`, `UMRS_AGENT_PROCESSES_PER_USER_MAX`), checked at
  start through `FrontendVenueService._quota_check`.
- **Tokens and rate.** The inference proxy has a per-session sliding window,
  off at `UMRS_INFER_RPM=0` (`infer_proxy/app.py` `SessionRateLimiter`;
  `umrs_front_venue_infer_rpm: 0` in group vars). It records no token counts.
  A per-request ledger already exists for UMRS's own LLM proxy:
  `llm_usage_log` (migrations 0050, 0053), written by
  `umrs/api/llm_usage.py` `record_usage`, read by `/umrs/usage/*` and
  `r4 usage`. `model_policies.max_tokens_per_day` (per model) and
  `UserRecord.quota` (per user, `admin/user_store.py`) are stored and read by
  nothing.
- **ask_user and approvals.** Tool approvals in a remote session are durable
  T4 intents in `chat_turn_approvals` (0098), raised by the worker through
  `POST …/rail/approvals` and decided by the owner from any client
  (`r4/src/r4/venue_worker.py` `_approve`, TUI `venue-bridge.ts`). `ask_user`
  is auto-answered: "no interactive user is attached — choose the most
  reasonable option" (`venue_worker.py` `_ask_user`). Locally the TUI bridge
  asks the user (`companion/ui/bridge_server.py` `_on_ask_user`).
- **Settings drift.** The local TUI bridge applies a permission mode the
  remote worker does not: `permission_override` if set, else `autopilot` →
  `TRUST`, else `ASK_ALWAYS` is raised to `AUTO_SAFE`
  (`bridge_server.py` `_apply_permission_mode`). The worker restores only
  `permission_override` and otherwise runs `ASK_ALWAYS`
  (`companion/session_store.py` `_session_from_row`), ignores
  `interaction_mode`, never sets the process-global `workflow_armed` flag,
  and does not know the tools the user approved "always" in the local
  session.

The maintainer decided on 2026-10-23 (verbatim):

- **M-4** "accessibility from the agent need to be selected by users. we can
  ask it to user when openning a session (for both local and remote
  session)".
- **M-5** "it is important to be able to impose those limitation. but we
  don't need to be that strict. wall clock per session can be as today, and
  concurrent session number limit can be 12."
- **M-6** "remote session should be acting the same with users' local
  session settings."

These override the plan's §10 recommendations (project default `allowlist`,
2M tokens/day, 60 rpm, 12 h, 2 sessions).

## Decision

### D-1 The session policy document

Every remote session carries one **resolved session policy**, computed at
start and stored on its `companion_venue_sessions` row (column `policy`) and
in its `frontend_session` job spec (`spec.policy`). RS-1 defines only fields
that something enforces; every other plan §2.3 field (`resources`,
`platform`, `tools`, `mcp`, `skills`, `image`, `placement`) is refused by name
until the wave that enforces it adds it. No more "modeled, not enforced".

| Field | Type, unit | Enforced by | Site default |
|---|---|---|---|
| `web.mode` | `none` \| `allowlist` \| `public` | squid in the egress pod (remote); the r4 web-tool guard (local, D-9) | none: the user chooses (D-2) |
| `web.domains` | list of squid `dstdomain` entries, ≤ 64 | same | `[]` |
| `lifetime.idle_s` | int, seconds | `frontend_session` status checker | `UMRS_VENUE_IDLE_SECONDS` (1800) |
| `lifetime.wall_clock_s` | int, seconds | lease max lifetime + status checker | `UMRS_VENUE_MAX_LIFETIME_SECONDS` (604800 = 7 d) |
| `budget.tokens_per_day_user` | int tokens or null (unlimited) | inference proxy, per user per day | null |
| `budget.tokens_per_day_project` | int tokens or null | inference proxy, per project per day | null |
| `budget.rpm_user` | int requests/min or null | inference proxy, per user | null |
| `sessions.concurrent_user` | int or null | venue start | `UMRS_SESSION_CONCURRENT_PER_USER` (12) |
| `sessions.concurrent_project` | int or null | venue start | null |

Only the web pair is chosen by the user at session open (D-2). The other
fields are limits that admins and project owners set (D-3).

One rule for every count and token budget (amended 2026-10-23, Amendment 1
Q1): `null` is no limit, a positive integer is the limit, and `0` or a
negative number is refused when a rule is written (`sessionpolicy.invalid`).
`0` never means "no limit" in one place and "block everything" in another;
blocking a person is done by disabling the account (RS-0). The env default
`UMRS_SESSION_CONCURRENT_PER_USER` is parsed in one place
(`session_policy.resolve.site_defaults`): empty = 12, a positive integer =
that cap, `unlimited` / `none` / `null` = no cap, anything else (`0`, a
negative number, a typo) is logged and 12 applies.

### D-2 Web access is the user's choice, at session open, local and remote

- The client asks the user, every time a session is opened, local or remote:
  **Off** (`none`), **Allowlist** (`allowlist` + domains) or **Public**
  (`public`). Nothing chooses silently for the user: there is no project or
  site default mode.
- The question shows only the choices the caps allow (D-3, served by the
  preview route in the contract). The domains field is prefilled from the
  user's last allowlist and the project's `suggested_domains`.
- The last choice is remembered per r4 profile and preselected next time. It
  is never applied without the question in an interactive client.
- Command-line runs whose agents have web tools (`r4 team plan` on either
  venue, `r4 teamup create --start`, `run` and `launch`, `r4 recipe run`)
  and the Rich REPL ask on a terminal like a session open: their own
  `--web`, else the root `r4 --web`, answers; `R4_WEB_ACCESS` and the
  remembered choice only preselect. Without a terminal they follow the
  non-interactive order below (amended 2026-10-23, Amendment W-2, W2-2 and
  W2-3: their agents refused every web call, and `--web` was ignored there).
- Non-interactive starts (`r4 -p`, `r4 session start` without a TTY, ACP
  editors, `r4 team plan --venue`, and the **server-hosted plan loop**,
  `POST /umrs/loops` with `iteration_plan`, ADR-092 slice 8b) take the
  choice from, in order: the `--web` flag, `R4_WEB_ACCESS`, the session's
  own earlier choice (a restart), the remembered last choice (each but the
  flag announced in one line). Without any of these a **local** session
  runs with `none` and says so; a **remote** start is refused
  (`sessionpolicy.web_choice_required`). That local `none` (`source:
  default_off`) is not a choice: a remote start from such a session (the
  chat's `team_plan_run(venue="server")`, a server plan loop, `r4 session
  start`, the console's `/venue start`) is refused with the same code before
  anything is sent, or asks on a terminal without preselecting it; the
  console also asks before its first turn (Amendment W-2, W2-1, W2-4;
  Amendment W-3, N2). The bridge's `session.venue_start` refuses it itself,
  before the mirror pins the venue, never leaving it to the server's
  transition setting; the Rich REPL asks on such a session and the console
  launch preset (`r4 --web`) answers for it (Amendment 4, W4-2, W4-3). (2026-10-23, RS-1 fix W2/W5: the
  session's own choice was skipped, and an ambient `R4_WEB_ACCESS` answered
  an interactive client's question; it now only preselects it there.)
- The server-hosted plan loop carries its runs' choice as `plan_web_policy`
  and `plan_web_allow_domains` and is checked against the caps **at loop
  start**: a missing choice is `sessionpolicy.web_choice_required` (400), a
  choice wider than a cap `sessionpolicy.widening_refused` (403), and
  nothing is created. A loop stored before this check (no choice recorded)
  stops before its next round with the reason
  `sessionpolicy.web_choice_required: … Start the loop again with a choice
  …`, never with a refused run mid-round; with
  `UMRS_SESSION_WEB_CHOICE_REQUIRED=false` it runs on the legacy default like
  any start (amended 2026-10-23, Amendment 1 P1).
- The server refuses a start without a web choice
  (`sessionpolicy.web_choice_required`, 400, with the allowed choices in the
  detail) while `UMRS_SESSION_WEB_CHOICE_REQUIRED` is true (the default).
  `false` restores today's fallback to `UMRS_VENUE_WEB_POLICY_DEFAULT`,
  clipped to the caps, for sites with old clients. When that fallback lands
  on `allowlist`, the domains the client sent that fall inside the cap are
  used (else the cap, else the suggested domains).
- `allowlist` with no domains is refused (`sessionpolicy.invalid`): squid
  would deny everything, which is `none` under another name.
- A resumed session keeps its choice. A session saved before RS-1 has none,
  and the client asks before its next turn. `/web` shows and changes the
  choice: locally it applies to the next tool call; a running remote session
  must be restarted to change it (squid is configured at provision).
- Plan S-8 ("new projects default to `allowlist`") is replaced by this
  decision.

### D-3 Limits: who sets them, how they combine

- **Rules.** A limit rule is a partial policy document stored in
  `session_policy_rules`, keyed by `(venue, scope, subject, layer)`:
  `scope` = `site` | `project` | `user`; `layer` = `admin` (any scope) or
  `owner` (project scope only, written by the project owner). `venue` is the
  site name (sites share `umrs_db`; user accounts are per site, 0113); `''`
  applies to every site, and a site-named row beats `''` at the same key.
  When a server cannot name its own site (its site config is unreadable) a
  write or read that would default to "this site" is refused
  (`sessionpolicy.unavailable`): the admin routes and `umrs-server admin
  policy` unless the venue is named (`--venue <site>`) or the all-sites rule
  is asked for explicitly (`--all-sites`), the owner route always. `''` is
  never reached by default (amended 2026-10-23, Amendment 1 P2).
- **Combination**, per field:
  1. the site default (D-1 table, code plus env);
  2. replaced by the admin `site` rule when it sets the field;
  3. replaced by the admin `project` and `user` rules that set it; when both
     set it, the stricter wins (min for numbers, intersection for
     `web.allow` and domain caps). An admin can therefore raise one user or
     one project above the site default, and a project restriction still
     binds a user with a personal raise;
  4. narrowed by the project owner's rule (min / intersection), which can
     never widen;
  5. narrowed by the user's choice (only `web` in RS-1).
- **Project caps bind sessions started in that project.** A session's
  project is the one its start names (checked like any start, ADR-097);
  the same user's session in another project, or with no project, is not
  bound by this project's admin or owner rule. An owner who wants a cap on
  a person everywhere asks an admin for a `user` rule (Amendment 1 P8).
- **Web caps** are `web.allow` (the modes that may be chosen) and
  `web.domains` (a domain cap). A chosen domain is inside a cap entry `c`
  when it equals `c`, or `c` starts with `.` and the chosen domain is
  `c[1:]`, a subdomain of it, or a `.`-entry under it. Any domain cap removes
  `public` from the allowed modes. `web.suggested_domains` is a hint for the
  question, never a limit.
- **Widening is refused.** A choice outside the caps is
  `sessionpolicy.widening_refused` (403) naming the field, the requested
  value, the cap and the layer that set it. An owner rule wider than the
  admin value is refused the same way, so an owner sees what is effective.
- **Refusal on bad input.** Unknown fields, wrong types, negative numbers,
  owner-layer rules outside project scope and `*_project` fields in a user
  rule are `sessionpolicy.invalid` (400).
- **Fail closed.** A start whose rules cannot be read is
  `sessionpolicy.unavailable` (503).
- **Who.** Admins write any rule with `umrs-server admin policy …` or
  `PUT /umrs/admin/session-policy/…` (RS-0's admin JWT or verified-admin
  identity). A project owner (or an admin) writes the project's owner layer
  with `PUT /umrs/projects/{id}/session-policy`.
- **Visibility.** `umrs-server admin session show` and
  `GET /umrs/admin/sessions/{sid}` include the resolved `policy` with its
  `origin` map (which layer set each value); `admin session list` shows the
  web mode. `umrs-server admin policy effective --user U --project P` shows
  what a start would get.
- `UserRecord.quota` stays unread. The per-user limits it modeled now exist
  as `scope=user` rules; the field is documented as superseded rather than
  given a second meaning.

### D-4 Usage is recorded in `llm_usage_log`, by the inference proxy

- The plan's "usage_records" is the existing `llm_usage_log` ledger. A second
  per-request usage table would be a second store of the same fact
  (AGENTS.md rule 10). Migration 0126 adds `venue`, `session_id` and
  `origin` columns. Rows from the inference proxy carry
  `origin = 'infer_proxy'`; UMRS's `/umrs/llm` rows keep `origin = ''`.
- Per proxied call the proxy records: user (`lease.owner_id`), project
  (`lease.project`), session, venue (`lease.venue`), model (requested, and
  served for the site source), source id, path, status, streaming flag,
  `prompt_tokens`, `completion_tokens`, `total_tokens` (and the cached and
  reasoning breakdown when reported), duration, and the event time. Bodies,
  headers and tokens are never recorded.
- Token counts come from the response: the OpenAI-style `usage` object,
  Anthropic `message_start` / `message_delta` usage, and Google
  `usageMetadata`. The proxy does not trust the client to ask for usage: a
  streamed OpenAI-style completion (`openai`, `openai_compat`,
  `azure_openai` sources and the site source) is sent upstream with
  `stream_options.include_usage = true` (amended 2026-10-23, Amendment 1
  Q2); a client that did not ask receives the standard final usage chunk
  (`choices: []`), which OpenAI-compatible clients accept and r4 asks for
  anyway. A `stream` that is not a JSON boolean (`1`, `"true"`, which vLLM
  reads as true) is sent upstream as the boolean it reads as, with
  `include_usage` when that is true (Amendment 2). A one-shot body the tail holds whole is parsed as JSON and only its
  top-level usage counts; an Anthropic stream's input count is read from
  the `message_start` event only (Q6). The
  proxy keeps a bounded head (16 KiB) and tail (64 KiB) of each stream to
  find them, so streaming is not buffered.
- A count the proxy cannot trust to be whole is estimated, never zero
  (Amendment 2): a generation call (any non-GET) whose stream ended without
  usage, or that the relay did not read to its end (the client left before
  the final usage chunk, which an OpenAI-style stream sends last), records
  prompt = request body bytes / 4 and completion = the larger of the
  streamed text bytes / 4 and the number of text events (a non-stream
  body's bytes / 4), tagged `extra.usage = "estimated"`. Reported counts are
  kept and only raised where the estimate is larger (a cut Anthropic stream
  keeps its `message_start` input). A base64 run of 1024 characters or more
  right after a blob marker (a data URI's `;base64,`, or the JSON `"data":"`
  key that Anthropic `source.data`, Google `inlineData.data` and OpenAI
  `input_audio.data` carry base64 under; an inline image, audio or file)
  counts at most 4096 tokens, not its bytes / 4 (Amendment 3; the JSON
  marker added 2026-10-23 after the final review, F1); a `\uXXXX` escape ends
  a run, and text that only looks like base64 is bytes / 4 (Amendment 4,
  P4-1). The estimate applies only to calls the
  provider did not account for.
  Any other answer without usage (a one-shot body, a listing) records zero
  tokens and `extra.usage = "absent"`.
- The insert runs after the response is relayed (the existing background
  task), never in the streaming path. A response that never completes (the
  client disconnects mid-stream, the relay is cancelled), where Starlette
  skips the background task, writes its row from the response's `finally`
  in a worker thread, with the estimate above; the row is written once
  either way (Q2, Amendment 2).

### D-5 Token and rate quotas at the inference proxy, as a typed 429

- Before unsealing, the proxy resolves the lease owner's effective limits
  (D-3, cached 30 s per `(venue, user, project)`) and refuses when:
  - `budget.tokens_per_day_user` is set and the user's `infer_proxy` tokens
    today at this venue are ≥ the limit → `quota.tokens_per_day_exceeded`;
  - `budget.tokens_per_day_project` is set and the project's are ≥ the limit
    → the same code, `scope: "project"`;
  - `budget.rpm_user` is set and the user's calls in the last 60 s are ≥ the
    limit → `quota.rpm_exceeded` (in-process sliding window per
    `(venue, user)`; the bound is per proxy process, as for today's limiter).
- The check runs before a call; a call already in flight can end above the
  limit. "Today" is the calendar day in `UMRS_QUOTA_DAY_TZ` (default `UTC`).
- With no limit set (the default) the proxy makes no extra query.
- A limit is set but usage cannot be read → `quota.unavailable` (503),
  fail closed. The worker retries 503.
- The limits themselves (the 30 s cache) serve their **last good value**
  when a re-read fails, so a database blip does not refuse every call while
  the rules are unchanged (amended 2026-10-23, Amendment 1 Q4). Only a key
  never read before fails closed (`quota.unavailable`). Both events are
  logged and counted (`LimitsCache.stats`: `stale_served`, `unavailable`);
  a refusal also writes the broker audit row.
- `UMRS_INFER_RPM` stays the site's per-session safety valve (`rate_denied`),
  unchanged and still 0 by default.
- The worker's r4 never retries `quota.tokens_per_day_exceeded`; it retries
  `quota.rpm_exceeded` after `Retry-After`. The turn ends with one readable
  line in the transcript (contract §6), which the user sees on any attached
  client.
- Quotas govern remote sessions (everything that reaches the inference
  proxy). A local session calls its provider directly with the user's own
  key, which the platform does not meter. Enforcing the same caps on
  `/umrs/llm` is a later step.

### D-6 Concurrent sessions: 12 per user, remote only

- At venue start (chat sessions and plan-run workers alike) UMRS counts the
  user's live `frontend_session` jobs at this venue (`planned`, `queued`,
  `running`) and refuses when the count is ≥ `sessions.concurrent_user`
  (default 12), and likewise for the project's when
  `sessions.concurrent_project` is set → `quota.concurrent_sessions_exceeded`
  (429).
- `frontend_session` leaves the ADR-051 agent-process budget
  (`AGENT_JOB_KINDS`), which keeps counting companion workers and loop jobs
  (default 4). Without this the old budget of 4 would bind before 12.
- **Local sessions do not count.** The cap protects shared server capacity:
  sandbox CPU and memory, the subnet pool and the lease budget. A local
  session uses the user's own machine. The server also cannot see one: a
  local session is a journal row that exists whether or not anything runs,
  and a heartbeat the client sends could simply be left out, so a count of
  local sessions could not be enforced.
- Starts at one venue are serialised from the count to the job insert and
  the registry row by a Postgres advisory lock on the venue (class
  `0x5343`; the same database serves every worker and container of a
  venue), so parallel starts never overshoot the cap and never share a
  subnet. A process's starts queue on an in-process lock first, so at
  most one of its pooled connections waits on the database lock, and a
  second start of a session already starting in the process answers
  `already_running` at once (Amendment 3). A start that waits more than
  15 s in all answers `unavailable` (503); nothing is reserved. (Amendment 2: the design said
  "two starts racing can overshoot by one"; in fact N racing starts could
  overshoot by N-1.)

### D-7 Wall clock and idle as today, policy-settable

The status checker reads `lifetime.idle_s` from the job's `spec.policy`
when a rule set it; when the policy's origin for it is `default` it reads
the live `UMRS_VENUE_IDLE_SECONDS`, so an operator's new idle value reaches
running sessions as it did before the policy existed (Amendment 2). A
rule equal to the default is recorded with the rule's origin, so a later
raised site value never widens it (Amendment 3). A project owner's value
looser than the default (origin `default`) is kept as a bound
(`policy.bounds["lifetime.idle_s"]`): the checker reads
`min(live UMRS_VENUE_IDLE_SECONDS, bound)`, so a raised site value never
passes the owner's rule either (Amendment 4, P4-2). The
lease's maximum lifetime is the smaller of `lifetime.wall_clock_s` and
`UMRS_VENUE_MAX_LIFETIME_SECONDS`, fixed when the lease is minted, as
before. The defaults equal today's values, so nothing changes until an
admin sets a rule.

### D-8 A remote session behaves like the user's local session (M-6)

- **`ask_user` is a durable question.** The worker raises it through the
  rail (`chat_turn_questions`, 0127) and polls for the answer. The owner
  answers from any attached client (TUI ask-user overlay, `r4 session
  answer`). Heartbeat phase `awaiting_user`. With no answer within
  `R4_VENUE_QUESTION_TIMEOUT_SEC` (default 1800 s, the idle default), the
  question becomes `expired` and the tool tells the model: "the user has not
  answered; do not assume an answer: end the turn and restate the question".
  The question stays in the transcript and the user answers in the next turn.
  (Review fixes, 2026-10-23.) The heartbeat detail names the question
  (`question <id>`), never its text, and admin views drop the detail while
  the phase is `awaiting_user`. A transient poll failure (rail unreachable,
  5xx) is retried with backoff until the timeout; only 401/404 end the wait.
  Every exit without an answer expires the question, and an answer that won
  the race against the expiry is used. A start, a stop and the end of the
  lease mark the session's pending questions `cancelled`. The TUI opens the
  question the heartbeat names (else the newest), opens it when a remote
  session waiting on the user is opened or attached, and `/venue questions`
  shows set-aside questions again.
  (Round-2 review fixes, 2026-10-23.) Admin views also drop the detail while
  the phase is `awaiting_approval`, and the worker's approval heartbeat names
  the tool and intent id only: the card's description carries the call's
  arguments (the `ask_user` question under the default `auto_safe`, a shell
  command). A refused `awaiting_user` or `awaiting_approval` heartbeat never
  ends the wait. The worker clips a question to the rail's bounds (4000
  characters, 24 options of 500) instead of the rail refusing it. The TUI
  closes a remote question's card when the worker stops waiting on it
  (answered elsewhere, expired, the session stopped) and moves any typed
  text to the composer, as it does for an answer to a question already
  closed; Esc on a remote question sets it aside rather than sending a
  stdin answer. `ask_user` stays outside `SAFE_TOOLS` (agent decision under
  delegation): under `auto_safe` it costs an approval card before the
  question, locally and remotely alike, so M-6 holds; making it safe would
  change the local permission rule, fork grants and plan-mode read-only
  tools, which is a separate decision.
  (Round-3 review fixes, 2026-10-23.) The card also closes when the session
  is `stopped` or `failed`: a stop changes the row state only and keeps the
  last heartbeat's phase and detail, and the removed worker sends no final
  heartbeat; an ended session's question is not reopened. A typed answer
  goes back only to an empty composer; a composer holding a draft keeps it
  and a notice carries the answer. (Round-4 review fix.) An answer that
  comes back after the user switched tabs stays a notice on its own tab: the
  returned draft is global and the active composer would take it, so
  another session could receive it (Amendment 4, A4-2).
- **Outdated clients (agent decision under delegation, 2026-10-23).** A
  client older than RS-1 never sends agent settings (`rev` 0) and cannot
  show a remote question. The worker then waits
  `R4_VENUE_QUESTION_TIMEOUT_OLD_CLIENT_SEC` (default 120 s), expires the
  question and answers: "your r4 client is too old to answer remote
  questions; upgrade r4", with the no-answer instruction. An answer that
  arrives in time (from a newer client of the same user) is still used.
- **Auto-answer only if the user's own setting says so.** New user setting
  `ask_user_mode`: `ask` (default) or `auto`. The local bridge honours it
  too, so `auto` behaves the same in both places.
- **Mirrored settings.** One function computes the agent settings the local
  bridge applies (permission mode, interaction mode, write-requires-plan,
  workflow arming, ask_user mode, approval TTL, tools approved "always" in
  this session). The client sends them before every remote start, when it
  opens or resumes a remote session (round 2: a pre-RS-1 start left `rev` 0,
  and a relaunch may change `--yolo`, `ask_user_mode` or the TTL), and on
  every change (`PUT …/venue/settings`, stored in
  `companion_venue_agent_settings`, 0128, with a revision number). The worker
  reads them at bootstrap and before each queued turn, and applies them with
  the same function's counterpart. A change applies from the next turn; the
  worker reads the settings before it pops a queued turn, so a refused lease
  never loses a popped one.
  (Round-3 review fixes, 2026-10-23; agent decision under delegation, within
  M-6.) A resume sends the settings of the session it runs, after any
  adoption of a newer journal copy, never the cached copy it replaced (that
  copy could send `auto_safe` over another client's `/approve ask`). When
  the journal did not answer, the resume sends nothing and the worker keeps
  the last settings sent: the client cannot tell whether its cached copy is
  current, and a stale push can loosen the worker, while keeping the last
  push never does. A restarted tab bridge that rejoins its session
  (`initialize {session_id}`, no `load_session` after it) follows the same
  rule: journal refresh first, push only when it answered (Amendment 4,
  A4-1). The attach-time push waits 5 s (a change push 15 s), so
  a stalled gateway cannot run a resume past the TUI's 30 s call. The server
  keeps the `rev` of a document sent again unchanged and writes no
  `settings.set` audit row for it.
- **Mode switches follow the session-open rule (2026-10-23, kept).** Switching
  to chat or plan mid-session now leaves `auto_safe`, as a resume of the same
  session always did; the old switch applied the Textual default
  `ask_always`, which the next resume undid. An explicit `/approve ask`
  override still gives `ask_always`, locally and remotely.
  *(2026-10-31, [ADR-104](104-one-mode-one-permission-decision.md) D-1: the
  override is retired. The document's `interaction_mode` decides and
  `permission_mode` is written derived from it; a saved
  `permission_override=trust` resumes as autopilot, any other value is
  dropped; `/approve` and `set_permission_mode` are gone. The schema-1 shape
  is unchanged.)*
- **Approvals** keep ADR-046 P9 (durable T4 intents, timeout = deny). "always"
  decided remotely already grants the tool for the worker's session; it now
  also reaches the settings document, so a restart keeps it.
- Interrupt and rewind stay in RS-5 (E-4's other legs).

### D-9 Local enforcement of the web choice

- The choice is stored in session metadata as `web_access`
  (`{mode, domains, chosen_at, source}`).
- The companion's per-session tool executors check it before `web_fetch` and
  `web_search` run. `none` refuses with "web access is off for this session
  (the user's choice); do not retry; the user can change it with /web in
  the console or r4 --web on the command line" (both ways out: the
  command-line surfaces have no `/web`; Amendment W-2, W2-2).
  `allowlist` refuses hosts outside the domains, with the squid `dstdomain`
  matching, and reports the host. Redirects are checked hop by hop.
- The same guard runs inside the remote worker, so the tool message is the
  same in both places; squid stays the remote enforcement.
- Shell, Python and user-configured MCP servers are not filtered locally.
  They run with the user's network after the user's approval; the question
  says so in one line. Remote, squid filters everything. An `r4 -p` the
  agent starts from such a shell is one more unfiltered program: it takes
  its own choice (its flag, `R4_WEB_ACCESS`, the remembered one), which may
  be wider than its parent's. The parent does not export its choice to its
  shells: that would promise a limit the shell itself does not have
  (Amendment W-2, W2-6).
  *Note (2026-10-30, [ADR-101](101-local-tool-sandbox.md) D-6, D-9):* this
  holds while the local tool sandbox is off, which is the default. With it
  on, shell, Python and code-tool processes have no network but R4's
  loopback proxy, which applies this choice per request through the same
  `host_allowed` (`none` and `default_off` start no proxy at all), so the
  local enforcers are two: this guard and that proxy. Local stdio MCP
  servers under the sandbox use their own per-server `sandbox.web` entry
  (default `none`), not the session's choice; hatch runtimes keep host
  networking. The question's local note says which applies. A nested
  `r4 -p` inside a sandboxed shell is filtered like any other child.

### Why

- M-4 makes web access a per-session decision of the person who will read
  the results. A forced project default would decide for them; caps still
  let an admin or owner narrow what may be chosen (P-3).
- M-5 asks for the mechanism with light defaults. Enforcing at the two
  points every remote session must pass, start and the inference proxy,
  needs no new component. Unlimited defaults mean no query on the hot path
  until a limit is set.
- Admin rules that override the site default, combined by "strictest wins"
  only among explicit rules, cover both "give this person more" and "this
  project gets less" without raising everyone's default.
- Reusing `llm_usage_log` keeps one usage ledger, so `r4 usage` and
  `/umrs/usage/*` show remote-session usage with no second reader.
- M-6: the user should not have to know where the agent runs. A question
  the agent cannot ask is a decision made without the user; a mode the
  worker ignores is a permission the user did not grant.

## Consequences

- Positive:
  - every remote start records who chose which web access;
  - admins can cap tokens, rate and sessions per site, project or user, and
    see the policy of any session;
  - remote usage appears in `r4 usage`;
  - remote sessions ask the user and honour the user's modes;
  - local `web_fetch` is finally checked.
- Cost:
  - one more question at every session open;
  - a remote start without a choice is refused, so old r4 clients must
    pass `--web` (or the site sets `UMRS_SESSION_WEB_CHOICE_REQUIRED=false`
    for a transition);
  - one `llm_usage_log` insert per proxied call;
  - a SUM query per call only for users under a token limit;
  - a remote turn can wait up to 30 min for an answer.
- NOT in scope:
  - resource limits per policy (`resources`, RS-2 placement);
  - USD budgets (no price source of record, ADR-046 R2-3);
  - capability bundles, MCP and skills (RS-5);
  - interrupt and rewind over the rail (RS-5);
  - quotas on `/umrs/llm` and the site gateway;
  - changing a running remote session's web access without a restart;
  - network filtering of local shell commands (*2026-10-30: taken up,
    opt-in, by [ADR-101](101-local-tool-sandbox.md), the local tool
    sandbox; gap row D-65*).

## Alternatives Considered

- **Project default web policy (plan S-8, recommendation M-4).** Rejected by
  the maintainer: the user chooses.
- **Pure "min of all caps".** Simple, but an admin could not give one heavy
  user more than the site default without raising everyone.
- **Most specific rule wins (user over project).** A personal raise would
  escape a project's restriction; rejected for "strictest among explicit
  rules".
- **A new `usage_records` table.** It duplicates `llm_usage_log`; rejected
  (rule 10). A daily roll-up table was also rejected: the indexed SUM is
  cheap at this scale and runs only for capped users.
- **`UserRecord.quota` as the user-scope store.** It would split the rules
  across two stores with two write paths (local CLI file plus row, HTTP
  route); one table with one resolver is simpler to audit.
- **Counting local sessions toward 12.** Not observable and not enforceable;
  see D-6.
- **Reusing `chat_turn_approvals` for questions.** Its states and decisions
  (`once | always | reject`) do not fit a free-text answer, and answers are
  conversation content, which admin views must not show.
- **Carrying settings in the snapshot.** The worker owns the snapshot while
  it runs (server wins for `frontend`), so the owner's later changes would
  race it. A separate revisioned row does not.
- **Keep auto-answer for remote `ask_user`.** Rejected by M-6.

## Implementation contract

The four RS-1 lanes build against this section. Each lane works in its own
worktree and changes only the files it owns. A shared file names the owner
of each part.

### 1. Lanes, order, ownership

| Lane | Scope | Migrations |
|---|---|---|
| **P** policy core | D-1, D-2 (server), D-3: schema, resolution, storage, start integration, preview, admin and owner routes, admin CLI, admin visibility | 0125 |
| **Q** quotas | D-4, D-5, D-6, D-7: proxy usage + token/rpm quotas, concurrent-session cap, wall clock and idle wiring, r4 quota notice, admin usage view | 0126 |
| **A** ask parity | D-8: durable questions, agent settings mirror, worker application, ask_user_mode, TUI answer path | 0127, 0128 |
| **W** web choice | D-2 (clients), D-9: r4 CLI and TUI question for local and remote, remembered choice, local guard, passing the choice to the remote start | none |

Migration **0129** is reserved for the integrator; no lane uses it.

*Note (2026-10-24, wave ledger (70)):* the RS-1 integration landed 0125–0128 without using 0129. Knowledge plan K1.2 (`0129_knowledge_artifact_contributions.sql`) now holds it; the next free numbers stay as the multi-site plan reserves them (W0 X-3 → 0130, W1 → 0131–0135).

Merge order **P → Q → A → W**. Each lane builds and tests on its own
against the shapes below, with fakes where it consumes another lane's
output. Only these production wiring lines need the earlier lane merged:
Q's proxy entry point (`effective_limits_cached` from P), Q's
`_quota_check` (P changes the hook to take a `StartCheck`; Q imports
`StartCheck` only under `TYPE_CHECKING` and reads its attributes, so its
tests pass a plain stand-in), and W's TUI preview call (P's route; W tests
it against a fake bridge).

**Owned files** (new unless marked *edit*):

- **P**:
  - umrs: `session_policy/__init__.py`, `session_policy/model.py`,
    `session_policy/resolve.py`, `session_policy/store.py`;
    `api/admin_session_policy.py`; `cli/admin/http_policy.py`;
    `migrations/0125_session_policy.sql`;
  - *edit* `venues/frontend_service.py` (start path, `_session_spec`,
    `admin_status`, `admin_view`); *edit* `venues/venue_sessions.py`
    (`policy` column); `venues/start_checks.py` (see §5);
  - *edit* `api/companion_venue.py`, except `_quota_check`, which Q owns;
  - *edit* `api/projects.py` (owner policy routes);
  - tests: `umrs/tests/test_session_policy_*.py`.
- **Q**:
  - umrs: `quotas/__init__.py`, `quotas/usage_tap.py`,
    `quotas/enforcer.py`, `quotas/session_cap.py`, `api/admin_usage.py`,
    `cli/admin/http_usage.py`, `migrations/0126_llm_usage_log_venue.sql`;
  - *edit* `infer_proxy/app.py`, `infer_proxy/__main__.py`;
  - *edit* `api/llm_usage.py` (Anthropic and Google usage keys; additive);
  - *edit* `api/agent_quota.py` (`AGENT_JOB_KINDS`);
  - *edit* `api/quota_status.py` (sessions and tokens poles);
  - *edit* `api/companion_venue.py` `_quota_check` (that function only);
  - *edit* `jobs/frontend_session_backend.py` (idle, wall clock, spec
    passthrough, `policy=spec["policy"]` on its `upsert_provisioning` call);
  - *edit* `venues/frontend_config.py` (`provision_config`
    `max_lifetime_seconds`);
  - r4: `companion/quota_notice.py`; *edit* `companion/agent.py`
    (`_HARD_ERROR_MARKERS` and the turn-error text only);
  - deploy: *edit* the umrs-front `.env` template lines and group vars for
    `UMRS_SESSION_CONCURRENT_PER_USER` and `UMRS_QUOTA_DAY_TZ`;
  - tests: `umrs/tests/test_quota_*.py`, `r4/tests/test_quota_notice.py`.
- **A**:
  - umrs: `companion_trust/chat_turn_questions.py`,
    `venues/agent_settings.py`, `api/companion_venue_parity.py`,
    `migrations/0127_chat_turn_questions.sql`,
    `migrations/0128_companion_venue_agent_settings.sql`;
  - r4: `companion/agent_settings.py`, `venue_settings.py`; *edit*
    `venue_worker.py`; *edit* `venue_rail.py` (question and settings verbs);
    *edit* `_cli/sessions.py`: only the settings push in
    `_handle_venue_start` and the new `answer` subcommand;
  - *edit* `companion/ui/bridge_server.py`: `_apply_permission_mode`,
    `_on_ask_user`, `set_permission_mode`, `set_interaction_mode`,
    `set_write_requires_plan`, `set_workflow_armed`, `session_venue_start`
    (settings push before start), new verbs `session.venue_questions` and
    `session.venue_answer`;
  - TUI: *edit* `bridge/venue-bridge.ts` (question polling region),
    `bridge/ask-user-bridge.ts`, `components/ask-user-overlay.tsx`,
    `lib/venue-runtime.ts` (`awaiting_user` phase label only);
  - tests: `umrs/tests/test_venue_parity_*.py`,
    `r4/tests/test_venue_parity_*.py`, `packages/r4-tui-v3/tests/remote-ask-user*.test.ts*`.
- **W**:
  - r4: `companion/web_access.py`, `_cli/web_access_prompt.py`; *edit*
    `companion/tools.py` (`_agent_builtin_tools`, `_make_agent_tool_executor`);
    *edit* `companion/session.py` (passing the web-access getter);
    *edit* `cli.py` (root `--web`, `--web-domain`); *edit* `config.py`
    (`web_access_last`, `web_access_domains_last`); *edit* `_cli/sessions.py`
    (flags, prompt, body `web_allow_domains`; everything except A's two
    parts); *edit* `_cli/team_plan.py` (same prompt);
  - *edit* `companion/ui/bridge_server.py`: `initialize`/`_open_session`
    `web_access` param, launch arg `--web-access`, new verbs
    `session.web_access_get`, `session.web_access_set`,
    `session.venue_policy`;
  - TUI: `components/web-access-overlay.tsx`; *edit*
    `components/venue-picker-overlay.tsx`,
    `components/remote-source-picker-overlay.tsx`,
    `bridge/session-bridge.ts`, `bridge/venue-bridge.ts` (`startRemote` and
    the preview call region), `lib/venue-runtime.ts` (except A's label),
    `stores/index.ts` (web-access fields), the composer submit hook, the
    `/web` slash entry;
  - tests: `r4/tests/test_web_access_*.py`,
    `packages/r4-tui-v3/tests/web-access*.test.ts*`, one PTY journey.

**Shared files**, edited only in the named parts:

- `umrs/src/umrs/server/http_sidecar.py`: each lane adds its own
  `include_router` line (P `admin_session_policy`, Q `admin_usage`,
  A `companion_venue_parity`).
- `umrs/src/umrs/cli/admin/main.py`: P adds the `policy` subparser block,
  Q the `usage` block.
- `r4-contracts/src/r4_contracts/errors.py`: P adds the `sessionpolicy`
  family (dict, classmethod, aggregate entry); Q adds three `QUOTA_ERRORS`
  rows; A adds one `COMPANIONVENUE_ERRORS` row.
- `r4/src/r4/companion/ui/rpc_schema.py`, `contracts/bridge/v1/*.schema.json`,
  `packages/r4-tui-v3/src/bridge/rpc-types.gen.ts`: A and W add their own
  verbs and regenerate (`python tools/rpc_schema_export.py --write && python
  tools/rpc_types_gen.py --write`). The integrator regenerates once more
  after merging; the output is deterministic.
- `tools/route_inventory.base.json`: each lane adds its own routes.
- The bridge verb table and allowlist in `bridge_server.py`: each lane adds
  its own lines.

### 2. Data: migrations (DDL sketch)

`0125_session_policy.sql` (P):

```sql
CREATE TABLE IF NOT EXISTS session_policy_rules (
    venue      TEXT        NOT NULL DEFAULT '',
    scope      TEXT        NOT NULL CHECK (scope IN ('site', 'project', 'user')),
    subject    TEXT        NOT NULL DEFAULT '',
    layer      TEXT        NOT NULL CHECK (layer IN ('admin', 'owner')),
    doc        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    set_by     TEXT        NOT NULL DEFAULT '',
    note       TEXT        NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (venue, scope, subject, layer),
    CONSTRAINT session_policy_rules_owner_is_project
        CHECK (layer = 'admin' OR scope = 'project'),
    CONSTRAINT session_policy_rules_site_subject
        CHECK (scope <> 'site' OR subject = '')
);
ALTER TABLE companion_venue_sessions
    ADD COLUMN IF NOT EXISTS policy JSONB NOT NULL DEFAULT '{}'::jsonb;
-- umrs_infer role (0086 pattern, conditional): GRANT SELECT ON session_policy_rules
```

`0126_llm_usage_log_venue.sql` (Q):

```sql
ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS venue      TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage_log ADD COLUMN IF NOT EXISTS origin     TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_llm_usage_venue_user_time
    ON llm_usage_log (venue, user_id, event_time DESC) WHERE origin = 'infer_proxy';
CREATE INDEX IF NOT EXISTS idx_llm_usage_venue_project_time
    ON llm_usage_log (venue, project_id, event_time DESC) WHERE origin = 'infer_proxy';
-- umrs_infer role (conditional): GRANT SELECT, INSERT ON llm_usage_log;
-- GRANT USAGE ON SEQUENCE llm_usage_log_id_seq
```

`0127_chat_turn_questions.sql` (A):

```sql
CREATE TABLE IF NOT EXISTS chat_turn_questions (
    question_id TEXT        PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    turn_id     TEXT        NOT NULL DEFAULT '',
    user_id     TEXT        NOT NULL,
    question    TEXT        NOT NULL,
    options     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    status      TEXT        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'answered', 'expired', 'cancelled')),
    answer      TEXT        NOT NULL DEFAULT '',
    answered_by TEXT        NOT NULL DEFAULT '',
    answered_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_turn_questions_session
    ON chat_turn_questions (session_id, status, created_at);
```

`0128_companion_venue_agent_settings.sql` (A):

```sql
CREATE TABLE IF NOT EXISTS companion_venue_agent_settings (
    user_id    TEXT        NOT NULL,
    session_id TEXT        NOT NULL,
    settings   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    rev        INTEGER     NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, session_id)
);
```

Keyed like `companion_session_snapshots` (corrected 2026-10-23, before any
deploy): session ids are client-chosen, so a `session_id` key let another
user's row claim or block the owner's. Deleting a session deletes its
`chat_turn_questions` and settings rows in the same transaction.

### 3. JSON shapes

**Rule document** (`session_policy_rules.doc`; every key optional; `null` = no
limit at this layer):

```json
{
  "web": {"allow": ["none", "allowlist", "public"],
          "domains": [".arxiv.org", "pypi.org"],
          "suggested_domains": [".arxiv.org"]},
  "lifetime": {"idle_s": 1800, "wall_clock_s": 604800},
  "budget": {"tokens_per_day_user": 2000000, "tokens_per_day_project": null,
             "rpm_user": 60},
  "sessions": {"concurrent_user": 12, "concurrent_project": null}
}
```

Validation: counts and token budgets are integers ≥ 1 or `null`
(`lifetime.*` ≥ 60; amended 2026-10-23: was ≥ 0, see D-1); `web.allow` ⊆ the three modes,
not empty; at most 64 domains, each normalized by
`venues/frontend_podman.normalize_allow_domain` and matching
`^\.?[a-z0-9-]+(\.[a-z0-9-]+)+$`; `*_project` keys refused in `user` rules;
`layer=owner` only with `scope=project`.

**Effective limits** (the fold of D-3 steps 1–4, before the choice), from
`effective_limits(...)` and the preview route:

```json
{
  "schema": 1, "venue": "rccs", "user_id": "alice", "project_id": "example-project-b",
  "web": {"allow": ["none", "allowlist"], "domains": [".arxiv.org"],
          "suggested_domains": [".arxiv.org"]},
  "lifetime": {"idle_s": 1800, "wall_clock_s": 604800},
  "budget": {"tokens_per_day_user": null, "tokens_per_day_project": null,
             "rpm_user": null},
  "sessions": {"concurrent_user": 12, "concurrent_project": null},
  "origin": {"web.allow": "project:admin", "web.domains": "project:owner",
             "lifetime.idle_s": "default", "sessions.concurrent_user": "site:admin"},
  "bounds": {"lifetime.idle_s": 3600}
}
```

`origin` values: `default`, `<scope>:<layer>` (`site:admin`, `project:admin`,
`user:admin`, `project:owner`), `choice`. `bounds` (Amendment 4): for a
numeric field whose origin is `default`, the project owner's looser value
it beat; a reader of the live default takes the smaller.

**Resolved session policy** (`companion_venue_sessions.policy`,
`spec.policy`, admin show): the effective limits with `web` replaced by the
choice, plus when:

```json
{
  "schema": 1, "venue": "rccs", "user_id": "alice", "project_id": "example-project-b",
  "web": {"mode": "allowlist", "domains": [".arxiv.org"]},
  "lifetime": {"idle_s": 1800, "wall_clock_s": 604800},
  "budget": {"tokens_per_day_user": null, "tokens_per_day_project": null,
             "rpm_user": null},
  "sessions": {"concurrent_user": 12, "concurrent_project": null},
  "origin": {"web.mode": "choice", "web.domains": "choice", "lifetime.idle_s": "default"},
  "bounds": {"lifetime.idle_s": 3600},
  "resolved_at": "2026-10-23T09:00:00Z"
}
```

Q and A read only these keys: `lifetime.idle_s`, `lifetime.wall_clock_s`,
`budget.*`, `sessions.*`. A missing key means the site default.

**Start body** (`POST /umrs/companion/sessions/{sid}/venue/start`):
unchanged keys `source_refs`, `default_source`, `project`, plus the web choice
in the existing `web_policy` (`none|allowlist|public`; `""` = no choice) and
`web_allow_domains`. No other key is added.

**Web-access preview** (P; `GET /umrs/companion/sessions/{sid}/venue/policy?project=<p>`,
verified caller, project checked like the start):

```json
{"session_id": "…", "project_id": "example-project-b",
 "choice_required": true,
 "limits": {…effective limits…},
 "current": {…resolved policy of this session's row, or null…}}
```

**Agent settings** (A; `companion_venue_agent_settings.settings`, the body
of `PUT …/venue/settings`, `GET …/rail/settings`):

```json
{
  "schema": 1,
  "permission_mode": "auto_safe",
  "interaction_mode": "chat",
  "write_requires_plan": false,
  "workflow_armed": false,
  "ask_user_mode": "ask",
  "approval_ttl_s": 1800,
  "session_approved_tools": ["file_write"]
}
```

`permission_mode` ∈ `ask_always|auto_safe|trust|deny` (the
`PermissionMode` values); `interaction_mode` ∈ `chat|plan|autopilot`;
`ask_user_mode` ∈ `ask|auto`; unknown keys are ignored by the worker and
kept by the store. `GET …/rail/settings` answers
`{"session_id", "rev", "settings"}`; `rev` is 0 and `settings` is `{}` when
none were sent.

**Questions** (A):

- rail `POST /umrs/companion/sessions/{sid}/rail/questions`
  takes `{"turn_id", "question", "options": [...], "question_id": null}` and
  answers `{"session_id", "question": Q}`;
- rail `GET …/rail/questions/{qid}` answers `{"session_id", "question": Q}`;
- rail `POST …/rail/questions/{qid}/expire` answers `{"question": Q}`;
- owner `GET /umrs/companion/sessions/{sid}/questions?status=pending`
  answers `{"questions": [Q…]}`;
- owner `POST …/{sid}/questions/{qid}/answer` takes `{"answer": "text"}` and
  answers `{"question": Q}`; a question that is not pending is a 409
  `companionvenue.question_closed`.

`Q = {"question_id", "session_id", "turn_id", "question", "options",
"status", "answer", "answered_by", "answered_at", "created_at"}`. The
`answer` is at most 8000 characters; `question` at most 4000.

### 4. Typed errors (`r4_contracts/errors.py`)

| Code | HTTP | Lane | When |
|---|---|---|---|
| `sessionpolicy.invalid` | 400 | P | bad rule doc or choice (detail `field`) |
| `sessionpolicy.web_choice_required` | 400 | P | start without `web_policy` while required (detail `choices`, `domains_cap`, `suggested_domains`) |
| `sessionpolicy.widening_refused` | 403 | P | choice or owner rule wider than the cap (detail `field`, `requested`, `cap`, `capped_by`) |
| `sessionpolicy.forbidden` | 403 | P | owner route by a non-owner, non-admin |
| `sessionpolicy.not_found` | 404 | P | delete of an absent rule, unknown project |
| `sessionpolicy.unavailable` | 503 | P | rules unreadable at start or preview |
| `quota.tokens_per_day_exceeded` | 429 | Q | daily token cap (proxy) |
| `quota.rpm_exceeded` | 429 | Q | per-user rate (proxy) |
| `quota.concurrent_sessions_exceeded` | 429 | Q | session cap at start |
| `companionvenue.question_closed` | 409 | A | answer to a question that is not pending |

FastAPI routes raise them with `raise_typed(PlatformError.<family>(…))`
(`x-platform-error-code` header). The start route converts
`PolicyError(code, message, detail)` to `PlatformError.sessionpolicy`.

**The detail stays server-side** (amended 2026-10-23, Amendment 1 P3).
`PlatformError.to_http_parts` sends the code (header) and the message
(`detail`) only, platform-wide, and RS-1 does not change that. The "detail"
column above names what the server knows and logs, not what a client
receives. A client reads the structured limits from the preview route
(`GET /umrs/companion/sessions/{sid}/venue/policy`): `limits.web.allow`
(the choices), `limits.web.domains` (the domain cap),
`limits.web.suggested_domains`, every numeric limit, and `limits.origin`
(the layer that set each value, i.e. `capped_by`), plus `choice_required`.
Every refusal message stands alone for a person: `web_choice_required`
names the choices, the domain cap, the suggested domains and the r4 flag;
`widening_refused` names the field, the requested value, the cap and the
layer that set it.

**The proxy's 429** (Starlette, Q):

```
HTTP/1.1 429 Too Many Requests
x-platform-error-code: quota.tokens_per_day_exceeded
Retry-After: 31512
content-type: application/json

{"error": "quota.tokens_per_day_exceeded",
 "message": "daily token limit reached for user alice at rccs: 2000310 of 2000000 tokens; resets 2026-10-24T00:00:00Z",
 "scope": "user", "subject": "alice", "limit": 2000000, "used": 2000310,
 "window": "day", "resets_at": "2026-10-24T00:00:00Z"}
```

`quota.rpm_exceeded` uses the same shape with `"window": "minute"` and
`Retry-After` = seconds until the oldest call leaves the window. Every refusal
also writes the existing broker audit row (`_denied(…, 429, code)`).

### 5. Modules and signatures

**P — `umrs/src/umrs/session_policy/`**

```python
# model.py
WEB_MODES: tuple[str, ...] = ("none", "allowlist", "public")
SCOPES = ("site", "project", "user"); LAYERS = ("admin", "owner")

class PolicyError(Exception):
    def __init__(self, code: str, message: str, **detail: Any) -> None: ...
    # code is the sessionpolicy name without the family: "invalid", ...

@dataclass(frozen=True)
class PolicyRule:
    venue: str; scope: str; subject: str; layer: str
    doc: dict[str, Any]; set_by: str = ""; note: str = ""
    updated_at: datetime | None = None
    def to_dict(self) -> dict[str, Any]: ...

@dataclass(frozen=True)
class WebChoice:
    mode: str                      # "" = no choice
    domains: tuple[str, ...] = ()

def validate_rule_doc(doc: Any, *, scope: str, layer: str) -> dict[str, Any]: ...
def domain_within(domain: str, cap: Sequence[str]) -> bool: ...

# resolve.py
def site_defaults(env: Mapping[str, str] | None = None) -> dict[str, Any]: ...
def effective_limits(rules: Sequence[PolicyRule], *, venue: str, user_id: str,
                     project_id: str, defaults: dict[str, Any]) -> dict[str, Any]: ...
def resolve_session_policy(limits: dict[str, Any], choice: WebChoice, *,
                           require_choice: bool, legacy_default: str,
                           now: datetime) -> dict[str, Any]: ...
def check_owner_rule(doc: dict[str, Any], admin_limits: dict[str, Any]) -> None: ...

# store.py
class PolicyRuleStore:                 # in-memory when pool is None (tests)
    def __init__(self, pool: Any | None = None) -> None: ...
    def get(self, venue: str, scope: str, subject: str, layer: str) -> PolicyRule | None: ...
    def put(self, rule: PolicyRule) -> PolicyRule: ...
    def delete(self, venue: str, scope: str, subject: str, layer: str) -> bool: ...
    def applicable(self, *, venue: str, user_id: str, project_id: str) -> list[PolicyRule]: ...
    def list(self, *, venue: str | None = None, scope: str | None = None) -> list[PolicyRule]: ...

def effective_limits_for(store: PolicyRuleStore, *, venue: str, user_id: str,
                         project_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any]: ...

class LimitsCache:                     # 30 s TTL, bounded (1024 keys); for the proxy
    def __init__(self, store: PolicyRuleStore, ttl_s: float = 30.0) -> None: ...
    def get(self, *, venue: str, user_id: str, project_id: str) -> dict[str, Any]: ...

def effective_limits_cached(pool: Any) -> Callable[[str, str, str], dict[str, Any]]:
    """(venue, user_id, project_id) -> effective limits; the proxy's provider."""
```

**P — `umrs/src/umrs/venues/start_checks.py`** (P creates; Q implements a
check):

```python
@dataclass(frozen=True)
class StartCheck:
    owner: str
    project: str
    venue: str          # site name
    session_id: str
    policy: dict[str, Any]   # the resolved session policy (§3)

StartCheckFn = Callable[[StartCheck], None]   # raises HTTPException (typed) to refuse
```

`FrontendVenueService.__init__` takes `quota_check: StartCheckFn | None`
(signature changed from `Callable[[str], None]`). `_launch` order: validate
refs → read the rules (`sessionpolicy.unavailable` on failure) → resolve the
policy with the choice (typed `PolicyError`) → `quota_check(StartCheck(...))`
→ data-class gate → subnet → `_session_spec(..., policy=policy)` → job →
`store.upsert_provisioning(..., policy=policy)` → audit
`venue.session.start` gains `policy_web`, `policy_origin`.
`VenueSessionStore.upsert_provisioning(..., policy: dict | None = None)`:
`None` keeps the stored value. `VenueSessionRow.policy: dict` is in
`to_dict()`, so owner status and the admin views carry it.

**P — routes**

| Route | Auth | Body / query → answer |
|---|---|---|
| `GET /umrs/admin/session-policy` | `require_session_admin` | `?venue=&scope=` → `{"rules": [PolicyRule.to_dict()]}` |
| `GET /umrs/admin/session-policy/effective` | admin | `?user=&project=&venue=` → effective limits |
| `PUT /umrs/admin/session-policy/{scope}/{subject}` | admin | `{"doc", "note", "venue"?}` → rule; `subject` is `_` for `site` |
| `DELETE /umrs/admin/session-policy/{scope}/{subject}` | admin | `?venue=` → `{"deleted": true}` |
| `GET /umrs/projects/{project_id}/session-policy` | verified; owner or admin | → `{"owner_rule": doc or null, "limits": effective limits for the caller}` |
| `PUT /umrs/projects/{project_id}/session-policy` | owner or admin | `{"doc", "note"}` → rule (layer `owner`; `check_owner_rule`) |
| `GET /umrs/companion/sessions/{sid}/venue/policy` | verified | `?project=` → preview (§3) |

Every write is audited as `venue.policy.set` or `venue.policy.delete` with
the actor, the key and the new doc. Routes carry `# auth:` docstring lines
and enter `tools/route_inventory.base.json`.

**P — CLI** (`umrs-server admin policy …`, `cli/admin/http_policy.py`, admin
JWT as in RS-0 `http_admin.py`):

```
umrs-server admin policy list      [--venue V] [--scope site|project|user]
umrs-server admin policy show      --scope S [--subject X] [--venue V]
umrs-server admin policy set       --scope S [--subject X] [--venue V] [--note T]
                                   (--file doc.json | KEY=VALUE ...)   # e.g. budget.tokens_per_day_user=2000000 web.allow=none,allowlist
umrs-server admin policy unset     --scope S [--subject X] [--venue V]
umrs-server admin policy effective --user U [--project P] [--venue V]
```

`KEY=VALUE` with an empty VALUE or `null` sets null. `--venue` defaults to
this site's name; `--all-sites` writes `''`. `admin session show` prints the
`policy` block; `admin session list` gains a `WEB` column.

**P — env**: `UMRS_SESSION_WEB_CHOICE_REQUIRED` (default `true`);
`UMRS_VENUE_WEB_POLICY_DEFAULT` is read only when that is `false`.

**Q — `umrs/src/umrs/quotas/`**

```python
# usage_tap.py
class UsageTap:
    """Bounded observer of a relayed response: head 16 KiB + tail 64 KiB."""
    def __init__(self, *, provider_kind: str, streaming: bool) -> None: ...
    def feed(self, chunk: bytes) -> None: ...
    def usage(self) -> dict[str, int] | None: ...   # _parse_usage_dict keys

def record_proxy_usage(pool: Any, *, lease: Any, session_id: str, model: str,
                       served_model: str, source_id: str, path: str,
                       status: int, streaming: bool, duration_ms: float,
                       usage: dict[str, int] | None,
                       estimated: bool = False) -> None:
    """One llm_usage_log row, origin='infer_proxy' (via api.llm_usage.record_usage)."""
# Amendment 2: UsageTap.metered(request_bytes=, generation=) -> (usage, estimated)
# Amendment 3: metered(..., prompt_tokens=PromptMeter().tokens()); PromptMeter.feed(chunk)
# Amendment 4: a blob run follows b";base64,"; alphabet [A-Za-z0-9+=] and \/

# enforcer.py
@dataclass(frozen=True)
class QuotaDenial:
    code: str            # "quota.tokens_per_day_exceeded" | "quota.rpm_exceeded" | "quota.unavailable"
    status: int          # 429 | 503
    scope: str; subject: str; limit: int; used: int
    window: str          # "day" | "minute"
    resets_at: str; retry_after_s: int; message: str
    def to_response(self) -> JSONResponse: ...

LimitsFn = Callable[[str, str, str], dict[str, Any]]    # (venue, user, project) -> effective limits

class QuotaEnforcer:
    def __init__(self, pool: Any, limits_fn: LimitsFn, *, day_tz: str = "UTC",
                 clock: Callable[[], float] = time.time) -> None: ...
    def check(self, lease: Any) -> QuotaDenial | None: ...
    def tokens_today(self, *, venue: str, scope: str, subject: str) -> int: ...

# session_cap.py
def count_live_sessions(pool: Any, *, venue: str, user_id: str = "",
                        project_id: str = "") -> int: ...
def check_concurrent_sessions(pool: Any, check: StartCheck) -> None:
    """Raise HTTPException(quota.concurrent_sessions_exceeded) at the cap."""
# Amendment 2: the venue start lock (FrontendVenueService(start_lock=...))
def start_lock(pool: Any, venue: str, *, wait_s: float = 15.0) -> ContextManager[None]: ...
# Amendment 3: local_start_lock(venue, *, wait_s) first; start_in_flight(venue, session_id)
```

Proxy wiring: `create_app(..., quota_enforcer: QuotaEnforcer | None = None)`.
`None` means no quota checks, which existing tests rely on. `__main__`
builds `QuotaEnforcer(pool, effective_limits_cached(pool),
day_tz=os.environ.get("UMRS_QUOTA_DAY_TZ", "UTC"))`. In both
`proxy_request` and `_proxy_site`, after the lease verdict and the site
limiter: `denial = enforcer.check(lease)` → `_denied(request, lease,
denial.status, denial.code)` plus `denial.to_response()`. `_relayed` feeds a
`UsageTap` from `_body()` and calls `record_proxy_usage` in `_audit_done`.

`companion_venue._quota_check(pool)` returns
`lambda check: check_concurrent_sessions(pool, check)`. It no longer calls
`enforce_spawn_budget`: the agent-process budget does not apply to
`frontend_session` any more (D-6), and the other spawn points keep calling it.

Status checker: `idle_s = spec.policy.lifetime.idle_s` unless its origin is
`default`, else the config value (Amendment 2), at most
`spec.policy.bounds["lifetime.idle_s"]` (Amendment 4).
`provision_config(..., max_lifetime_seconds=min(config, spec.policy.lifetime.wall_clock_s))`.
`validate_frontend_session_spec` passes `policy` through unchanged.

`GET /umrs/admin/usage?user=&project=&venue=&days=1` (admin) answers
`{"rows": [{"scope", "subject", "day", "prompt_tokens", "completion_tokens",
"total_tokens", "requests", "limit"}]}`. CLI: `umrs-server admin usage show
[--user U] [--project P] [--days N]`. `/umrs/quota/units` gains `sessions`
(current live sessions and `concurrent_user`) and `tokens_today` (current and
`tokens_per_day_user`); on these two poles `limit` is `null` for no limit, as
in the policy (amended 2026-10-23: was 0; the older `cpu_process` and
`disk_bytes` poles keep 0 = uncapped).

**Q — env**: `UMRS_SESSION_CONCURRENT_PER_USER` (default 12; parsed only by
P's `site_defaults`, grammar in D-1), `UMRS_QUOTA_DAY_TZ` (default `UTC`), on
the API servers and `umrs-infer`. Operator table:
frontend-venue operator runbook §9.

**Q — r4**: `companion/quota_notice.py`:

```python
def quota_notice(error_text: str) -> str | None:
    """One transcript line for a typed quota refusal, else None."""
```

`companion/agent.py`: add `"quota.tokens_per_day_exceeded"` and
`"quota.concurrent_sessions_exceeded"` to `_HARD_ERROR_MARKERS`. The turn's
error text, when `quota_notice` matches, is for example "Daily token limit
reached (2,000,000 tokens/day for user alice); resets 2026-10-24 00:00 UTC. A
platform admin can raise it." `quota.rpm_exceeded` stays transient and honours
`Retry-After`.

**A — umrs**

```python
# companion_trust/chat_turn_questions.py
class ChatTurnQuestionsStore:          # conn=None → sqlite path (tests), like ChatTurnApprovalsStore
    def ask(self, *, session_id: str, turn_id: str, user_id: str, question: str,
            options: list[str], question_id: str | None = None) -> dict[str, Any]: ...
    def get(self, question_id: str) -> dict[str, Any] | None: ...
    def pending(self, *, session_id: str) -> list[dict[str, Any]]: ...
    def answer(self, question_id: str, answer: str, answered_by: str) -> dict[str, Any]: ...
    def expire(self, question_id: str) -> dict[str, Any]: ...
    def cancel_pending(self, *, session_id: str, user_id: str) -> int: ...
def cancel_session_questions(pool, *, session_id: str, user_id: str) -> int: ...  # best effort

# venues/agent_settings.py
class VenueAgentSettingsStore:        # in-memory when pool is None
    def get(self, session_id: str, *, user_id: str) -> tuple[int, dict[str, Any]]: ...
    def put(self, *, session_id: str, user_id: str, settings: dict[str, Any]) -> int: ...
```

`api/companion_venue_parity.py`: router prefix `/umrs/companion/sessions`.
The rail routes authenticate with `companion_venue.rail_principal`; the
owner routes check `session_exists(owner, sid)` through the venue service.

```
PUT  /{sid}/venue/settings               owner      body = agent settings → {"rev"}
GET  /{sid}/rail/settings                rail       → {"session_id","rev","settings"}
POST /{sid}/rail/questions               rail       (§3)
GET  /{sid}/rail/questions/{qid}         rail
POST /{sid}/rail/questions/{qid}/expire  rail
GET  /{sid}/questions                    owner      ?status=pending
POST /{sid}/questions/{qid}/answer       owner
```

These sit under the existing `/gw/companion/` gateway lane, so the gateway
needs no change. Admin views never include questions or answers, and drop
the runtime detail while the phase is `awaiting_user`.

**A — r4**

```python
# companion/agent_settings.py
def effective_agent_settings(session: Any, *, launch_mode: str = "",
                             allow_all_tools: bool = False,
                             ask_user_mode: str = "") -> dict[str, Any]: ...
def apply_agent_settings(session: Any, settings: Mapping[str, Any]) -> None: ...
def ask_user_auto_answer() -> str:   # the one auto-answer text, local and remote

# venue_settings.py
def push_venue_agent_settings(client: Any, session_id: str,
                              settings: Mapping[str, Any]) -> int: ...  # PUT, returns rev

# venue_rail.py (VenueRailClient additions)
def get_settings(self) -> dict[str, Any]: ...
def ask_question(self, *, turn_id: str, question: str, options: list[str]) -> dict[str, Any]: ...
def get_question(self, question_id: str) -> dict[str, Any]: ...
def expire_question(self, question_id: str) -> dict[str, Any]: ...
```

`bridge_server._apply_permission_mode` becomes `apply_agent_settings(session,
effective_agent_settings(...))`, so the remote worker and the local bridge
share one rule. `VenueWorker._configure` and the start of `run_once` apply
`rail.get_settings()` when `rev` > the applied rev. `VenueWorker._ask_user`
returns `ask_user_auto_answer()` when `ask_user_mode == "auto"`; otherwise
it raises a question, sends heartbeat phase `awaiting_user`, polls every 2 s
until answered, stopped, or `R4_VENUE_QUESTION_TIMEOUT_SEC` (default 1800; 120 s,
`R4_VENUE_QUESTION_TIMEOUT_OLD_CLIENT_SEC`, when the client never sent
settings), and on timeout calls `expire_question` and returns the no-answer
text (D-8; the old-client text for a `rev` 0 client).
The user setting `ask_user_mode` lives in r4 `Config` (`ask_user_mode: str =
"ask"`, env `R4_ASK_USER_MODE`). Bridge verbs: `session.venue_questions`
`{session_id?}` → `{"questions": [Q]}`; `session.venue_answer`
`{session_id?, question_id, answer}` → `{"question": Q}`. CLI: `r4 session
answer <sid> <question_id> <text>` and `r4 session questions <sid>`. The
TUI's remote watcher polls `session.venue_questions` while the runtime phase
is `awaiting_user` and opens the existing ask-user overlay; its answer goes
to `session.venue_answer`.

**W — r4**

```python
# companion/web_access.py
WEB_MODES = ("none", "allowlist", "public")
@dataclass(frozen=True)
class WebAccess:
    mode: str                      # "" = not chosen yet
    domains: tuple[str, ...] = ()
    chosen_at: str = ""
    source: str = ""               # prompt | flag | env | remembered | default_off
    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> "WebAccess": ...
    def to_metadata(self) -> dict[str, Any]: ...
def host_allowed(host: str, domains: Sequence[str]) -> bool: ...   # squid dstdomain semantics
def check_url(access: WebAccess, url: str) -> str | None: ...     # refusal text, or None
def parse_web_flag(value: str) -> str: ...                         # off→none; none|allowlist|public

# _cli/web_access_prompt.py
def choose_web_access(*, flag: str | None, domains: Sequence[str],
                      remote: bool, limits: Mapping[str, Any] | None,
                      interactive: bool, remembered: WebAccess | None,
                      input_fn: Callable[[str], str] = input) -> WebAccess: ...
```

The companion tool executor for `web_fetch` and `web_search` calls
`check_url(getter(), url)` first. The getter reads the live session's
metadata, so `/web` changes take effect on the next call. `web_fetch`
follows redirects through a checked opener. `r4/src/r4/agent/tools.py`
keeps its handlers.

Flags and settings:

- root `r4 --web {off,none,allowlist,public}` and `--web-domain D`
  (repeatable or comma-separated) go to the bridge as `--web-access
  MODE[:d1,d2]` (handed over in the launch-only `R4_LAUNCH_WEB_ACCESS`,
  which the console and `r4 -p` take out of their environment; 2026-10-23);
- `r4 session start … --web … --web-domain …` and
  `r4 team plan … --web … --web-domain …` (both venues; corrected
  2026-10-23, the flags were never `--venue-web-*`); `r4 teamup` and
  `r4 recipe run` take the root `r4 --web`;
- env `R4_WEB_ACCESS` (same grammar as `--web-access`);
- config `web_access_last` and `web_access_domains_last`, written after
  every interactive choice.

Bridge verbs:

- `initialize` takes `web_access: {mode, domains}` (additive);
- `session.web_access_get` → `{"web_access": {...}}`;
- `session.web_access_set` `{mode, domains}` → the same; a running remote
  session answers `venue.restart_required`;
- `session.venue_policy` `{project?}` → the preview body (§3).

The TUI opens `WebAccessOverlay` in three places:

- after the venue choice of a new session (local or frontend), with the
  remote options limited by `session.venue_policy`;
- on the first submit in a session whose `web_access.mode` is `""`;
- from the `/web` slash command.

The remote source picker shows the chosen value and no longer cycles it, and
`startRemote` sends `web_policy` and `web_allow_domains` from that choice.
`r4 session start` without `--web` asks on a TTY, preselecting
`R4_WEB_ACCESS`, else the session's own choice, else the remembered one.
Without a TTY it uses `R4_WEB_ACCESS`, the session's own choice or the
remembered choice (one notice line), else exits 2 with "choose web access:
--web off|allowlist|public". The remote worker gets the start's choice as
`R4_WEB_ACCESS` in its container and restores it into a copy that has none
(2026-10-23, RS-1 fix W1/W2).

### 6. Test obligations (each fails on the old code)

- **P**:
  - resolution table tests: default, site override, user raise above the
    site, project and user both set (strictest), owner narrows, owner
    widening refused, domain caps remove `public`, choice widening refused;
  - start without a choice → 400 `sessionpolicy.web_choice_required`; with
    the flag off → legacy default;
  - the policy is stored on the row and the spec; admin show carries it;
  - preview route; owner route refuses a non-owner; admin routes refuse a
    non-admin; CLI `set` parses `KEY=VALUE`.
- **Q**:
  - `UsageTap` on OpenAI, Anthropic and Google streams and one-shot bodies,
    and a stream with no usage;
  - one `llm_usage_log` row per proxied call with `origin='infer_proxy'`;
  - with a daily cap reached → 429 and the exact body and headers; rpm
    window; cap unset → no SUM query (spy);
  - lookup failure → 503 `quota.unavailable`;
  - the 13th live session → 429 `quota.concurrent_sessions_exceeded`, and a
    5th session no longer trips `cpu_process_exceeded`;
  - idle and wall clock from `spec.policy`;
  - r4 does not retry `quota.tokens_per_day_exceeded`, and the transcript
    line appears.
- **A**:
  - the worker's `ask_user` creates a question, returns the owner's answer,
    and expires on timeout with the no-answer text (old code: auto-answer);
  - `ask_user_mode=auto` gives the auto-answer locally and remotely;
  - the worker applies `auto_safe` and `autopilot` → `trust` (old: always
    `ask_always`);
  - a settings change between turns applies at the next pop;
  - an answer to a closed question → 409; a non-owner → 404;
  - TUI: a remote question opens the overlay and the answer reaches
    `session.venue_answer`.
- **W**:
  - `web_fetch` refused for `none` and for a host outside the allowlist,
    allowed inside; a redirect off the list is refused (old: unchecked
    `urllib`);
  - `choose_web_access` order (flag, env, remembered, local `none`, remote
    refusal);
  - `r4 session start` sends `web_allow_domains`;
  - TUI: a new session asks, the remote options follow the caps, first
    submit asks on a session without a choice, and the source picker no
    longer cycles `w`;
  - one PTY journey: new local session → web question → `/web`.

## Rollout-status (2026-10-23)

Design only. Nothing is implemented; the lanes above are open. Migrations
0125–0128 are allocated as stated, 0129 is reserved. Deploy after the lanes
merge: converge `check` then `apply`, with
`UMRS_SESSION_WEB_CHOICE_REQUIRED=true`,
`UMRS_SESSION_CONCURRENT_PER_USER=12`, `UMRS_QUOTA_DAY_TZ=UTC` and
`umrs_front_venue_infer_rpm: 0` unchanged. Live leg (RS-1 exit criterion):
`umrs-server admin policy set --scope user --subject <test user>
budget.tokens_per_day_user=1000`, then a remote session's next call ends with
the typed 429 line in its transcript, and `admin session show` prints the
policy.

Rollout note (D-8, 2026-10-23): until every user runs an RS-1 r4, a remote
`ask_user` from a session whose client never sent settings is answered after
120 s with "your r4 client is too old to answer remote questions; upgrade
r4" (`R4_VENUE_QUESTION_TIMEOUT_OLD_CLIENT_SEC` in the worker image env
changes the wait). Migration 0128 is keyed `(user_id, session_id)`; it was
corrected in place because no venue had applied it.

## Amendment 1 (2026-10-23): RS-1 server review fixes

Found by the RS-1 integration review; decided by the agent under the
2026-09-24 delegation. Inline edits above carry "Amendment 1".

| Item | Change |
|---|---|
| P1 | The server-hosted plan loop is a non-interactive start (D-2): `plan_web_policy` + `plan_web_allow_domains` checked at loop start; a stored loop without a choice stops before its round with the named way out. Before: every slice-8b loop was refused at iteration 1 while the choice was required. |
| P2 | An unknown site name never defaults a rule to all sites (D-3). |
| P3 | Typed error detail stays server-side; clients read the preview route; messages stand alone (§4). |
| P5 | The legacy fallback keeps the client's domains inside the cap (D-2). |
| P7 | Migration order, below. |
| P8 | Project caps bind sessions started in that project (D-3). |
| Q1 | One rule for counts and budgets: null = no limit, 0 refused; one env parser (D-1); quota-unit poles answer null. |
| Q2 | The proxy forces `include_usage` on OpenAI-style streams and records a row when a stream never completes (D-4). |
| Q4 | Last-good limits on a failed re-read (D-5). |
| Q6 | Top-level usage only for one-shot bodies; `message_start` only for Anthropic stream input (D-4). |

**Migrations before the new code serves (P7).** 0125–0128 must be applied
before the RS-1 code takes traffic: without 0125 every venue start, the
preview and the admin policy routes answer `sessionpolicy.unavailable`
(503, fail closed, nothing written), and the proxy fails closed for keys it
never read. The umrs-front converge already orders this: it starts the
servers on the new image, applies `python -m umrs.migrations` inside the
fresh `umrs-server-<first cluster>` container ("Apply UMRS DB migrations",
`deploy/ansible/roles/front_end/tasks/umrs-front.yaml`), restarts the
servers once when any migration applied, and only then recreates
`umrs-infer` (`frontend-venue-infer.yaml`, imported later in the same
play). The window between the first start and the apply (seconds) fails
closed as above. A deploy by hand runs `umrs-server admin migrate --db-url
…` (or `python -m umrs.migrations`) before restarting the servers.

## Amendment W-2 (2026-10-23): RS-1 round-2 web-choice fixes

Found by the RS-1 round-2 web review; decided by the agent within M-4
("the user chooses, nothing chooses silently") under the 2026-09-24
delegation. Inline edits above carry "Amendment W-2".

| Item | Change |
|---|---|
| W2-1 | The chat's `team_plan_run(venue="server")` sends the session's choice as `web_policy` + `web_allow_domains` (start and resume); without one it answers `sessionpolicy.web_choice_required` before calling UMRS. Before: it sent none, so UMRS refused every console start, or with the flag off widened the user's `none` to the site default. |
| W2-2 | `r4 team plan` (local venue), `r4 teamup create --start`/`run`/`launch` and `r4 recipe run` take the user's choice (`--web`, `r4 --web`, the question on a terminal, else the non-interactive order). Refusal texts name `/web` and `r4 --web`. Before: their agents refused every web call and `--web` was ignored. |
| W2-3 | The Rich REPL keeps `r4 --web` in its session metadata (never exported as `R4_WEB_ACCESS` to the agent's shells) and asks when the session has no choice; `R4_WEB_ACCESS` preselects. |
| W2-4 | A local `default_off` is not sent as a plan loop's `plan_web_policy`: no choice, so the remote start is refused (D-2). |
| W2-5 | The console asks before every turn on a session without a choice (store `sendChatMessage`: retry, edit-resend, queue flushes, title screen, slash and shell relays), not only a typed send. A held relay is sent as a relay, and dropped with a notice on cancel. |
| W2-6 | Declined as code: a nested `r4 -p` from an agent's shell can be wider than its parent; local shells are unfiltered by design (D-9), so the parent exporting its choice would promise a limit the shell does not have. Documented in D-9. |

## Amendment W-3 (2026-10-23): RS-1 round-3 web-choice fixes

Found by the RS-1 round-3 web review; decided by the agent within M-4
("the user chooses, nothing chooses silently") under the 2026-09-24
delegation. Inline edits above carry "Amendment W-3".

| Item | Change | Why |
|---|---|---|
| N1 | A slash command's follow-up turns (`/retry`, `/code edit`, `/agent chat`, `/search`) on a session without a choice wait for one question together; answered, they are sent in order as relays; cancelled (or the answer fails), they go back to the composer (named in a notice when the composer holds a draft). A second held send joins the open question instead of replacing it. Only `!cmd` and terminal-share output are still dropped on cancel. Amends W2-5. | `/retry` cuts its turn from the session and the transcript before re-sending it, so dropping the held relay lost the user's prompt; a list of several follow-ups kept only the last. The composer is the one place the user can still act on it; machine output stays out of it. Asking before dispatching `/retry` was the other option, but it would need the TUI to know which commands hand turns back. |
| N2 | A local `default_off` is no choice anywhere a remote start reads the session's own: `r4 session start` refuses without a flag, `R4_WEB_ACCESS` or a remembered choice, and on a terminal asks without preselecting it; the bridge's `session.venue_start` sends it as no choice (the server refuses by name; since Amendment 4, W4-2, the bridge refuses it itself); the console's `/venue start` asks, and so does its first turn on such a session. | D-2 already said `default_off` is not a choice, but the session-reuse step promoted it to "this session's choice", so a sandbox started with web off nobody chose. The console is an interactive client, where D-2 never applies a choice without the question; a local `r4 -p` still runs off with its notice. |

## Amendment 2 (2026-10-23): RS-1 round-2 policy review fixes

Found by the RS-1 round-2 adversarial review (policy and quotas); decided by
the agent within M-5 (limits imposable server-side, lenient defaults). None
of the three was a widening or a cross-user hole; each let an imposed limit
bind less than it says. Inline edits above carry "Amendment 2".

| Item | Change | Why |
|---|---|---|
| R2-P1 | A non-boolean `stream` is sent as the boolean it reads as, with `include_usage`; an unaccounted stream or a call cut before its usage chunk records a tagged estimate, not zero (D-4). | Code in the sandbox reaches the proxy directly, so `"stream": 1` or leaving before the last chunk ran a capped user's calls at zero tokens. An estimate keeps M-5's cap binding; the tag keeps it visible, and reported counts are never lowered. |
| R2-P2 | Venue starts are serialised from the count to the row by an advisory lock per venue (D-6). | N parallel starts could exceed the 12-session cap by N-1, and could draw the same subnet. Starts are rare and the locked section is a few database statements; a 15 s wait limit answers 503 rather than blocking. |
| R2-P3 | A default-origin `lifetime.idle_s` follows the live `UMRS_VENUE_IDLE_SECONDS` (D-7). | M-5 keeps idle "as today": before RS-1 a changed site value reached running sessions; copying the default into the policy froze it for up to the 7-day lease. |

## Amendment 3 (2026-10-23): RS-1 round-3 policy review fixes

Found by the RS-1 round-3 adversarial review (policy and quotas); decided by
the agent within M-5. None is a widening of the user's own choice or a
cross-user hole. Inline edits above carry "Amendment 3".

| Item | Change | Why |
|---|---|---|
| N1 | The estimate's prompt side counts each base64 run of 1024+ characters at most 4096 tokens (`PromptMeter`, fed as the request is sent; a run may span chunks); text keeps bytes / 4 (D-4). | A cut stream carrying a 1.5 MB PNG was charged ~500k tokens, again on every interrupted turn while the image stayed in the history: an admin's daily cap locked the user out after a few Esc presses, and usage reports were inflated. 4096 covers a typical vision prompt's image (OpenAI and Anthropic ≤ ~1.6k, Qwen-VL often 1–5k); text never runs 1 KiB without a space or punctuation, so prose and code are unaffected. Limit: a prompt made of long base64-like runs, cut before its usage, is undercounted; the reported count still wins whenever the stream ends. |
| N1 (declined part) | `stream_options.continuous_usage_stats` is not added for site sources. | It changes the answer the user's own code receives (usage on every chunk; a client that sums per-chunk usage over-counts), against M-6 (a remote session acts like the local one). The fixed image charge already bounds the estimate. |
| N2 | `start_lock` takes the process's own lock (bounded by the same 15 s) before a pooled connection; a duplicate start of a session already starting in the process is `already_running` at once (D-6). | Waiters each pinned a pooled connection while polling: ~32 parallel starts used up `UMRS_DB_POOL_MAX`, the holder's own count failed with 503 and every route on that pool failed until they drained. Now one connection per process waits. |
| N3 | On a tie with the site default the fold records the narrowing layer's origin (D-7). | A project owner's `idle_s` equal to the default was recorded as `default`, so a later raised `UMRS_VENUE_IDLE_SECONDS` widened that project's running sessions beyond the owner's rule. A looser owner value and an admin value keep their origin. |

## Amendment 4 (2026-10-23): RS-1 round-4 review fixes

Found by the three RS-1 round-4 re-reviews (ask, web choice, policy);
decided by the agent within M-4, M-5 and M-6 under the 2026-09-24
delegation. None widens a user's choice across users. Inline edits above
carry "Amendment 4".

| Item | Change | Why |
|---|---|---|
| A4-1 | `initialize {session_id}` on a remote session (a restarted tab bridge rejoining its session) refreshes from the journal first and pushes the agent settings only when the journal answered, as `load_session` does; its notice rides the snapshot, and the boot save writes the adopted copy (D-8). | The rejoin pushed the cached copy unrefreshed: after another device's `/approve ask`, the worker ran `auto_safe` again until some later change. Skipping the push entirely was the other option, but a relaunch with `--yolo` or a new `ask_user_mode` must still reach the worker (round 2). |
| A4-2 | An answer to a remote question that comes back closed after the user switched tabs stays a notice on its own tab (D-8). | `setPendingChatDraft` is global and the active composer adopts it: tab A's answer landed in tab B's composer, and Enter sent it to B's session. |
| W4-1 | A failed web-access answer returns the held follow-ups the way a cancelled question does (`returnHeld`): an empty composer takes them, a draft is kept and a notice carries them, and after a tab switch only the notice on the question's own tab carries them (D-2). | The failure path set the composer draft unconditionally, so a `/retry` prompt overwrote a draft typed during the 15 s call (the rule the ask fix N3 just set). |
| W4-2 | `session.venue_start` without the user's choice (none, or a local `default_off`) is refused by the bridge with `sessionpolicy.web_choice_required`, before the mirror (D-2). | It sent `web_policy=""` and left the refusal to the server; with `UMRS_SESSION_WEB_CHOICE_REQUIRED=false` (the transition setting) the server fell back to `UMRS_VENUE_WEB_POLICY_DEFAULT` (`public`), a choice nobody made, and the mirror had already pinned the session remote. `r4 session start` and `team_plan_run` already refused on the client. |
| W4-3 | The Rich REPL and the console launch preset treat a local `default_off` as no choice (`remote_start_choice`), as round 3 did for remote starts: the REPL asks, `r4 --web` answers (D-2). | The REPL kept web off with no question and no notice; the preset was skipped, so the console asked again despite an explicit flag. The second never widened anything; both broke "never applied without the question". A non-interactive `r4 -p` resume still keeps `default_off` (it cannot ask, and re-deriving it gives the same `none` with a notice). |
| P4-1 | The prompt estimate's blob rule applies only to a run right after a data URI's `;base64,`; the run alphabet is standard base64 plus JSON's `\/`, without `\`, `_` or `-` (D-4). | `json.dumps` escapes every non-ASCII character as `\uXXXX` and every character of the escape was in the alphabet: a cut Japanese prompt on a site source read as one run, ~4k tokens for ~15–22k real; words joined by `_` or `-` under-charged the same way, on purpose if wanted. Escaped CJK text now counts its escaped bytes / 4 (about 1.5 tokens per character, an over-estimate, as before Amendment 3); the reported count still wins whenever the stream ends. Encoding the site body with `ensure_ascii=False` was declined: it changes the bytes sent upstream, and a lone surrogate would fail to encode. Limit: a text part forged as a data URI is still charged as an image; it is only an estimate for a call the provider did not account for. |
| P4-2 | `effective_limits` records `bounds`: for a numeric field left at the site default, the project owner's looser value; the resolved policy carries it and the idle checker takes `min(live default, bound)` (D-7). | An owner's `idle_s` above the default lost to it (origin `default`), and the checker then read the live env alone: an operator raising `UMRS_VENUE_IDLE_SECONDS` to 4 h let a running session idle past the owner's 3600 s, breaking "caps may narrow" like Amendment 3's N3. |

## Amendment 5 (2026-10-25): knowledge URL ingest follows the web policy

Maintainer decision, 2026-10-25 (wave ledger (75), "next wave"; built in
wave (79)): server-side knowledge ingest of a URL or a git repository
follows this ADR's web policy, per project and per user, instead of a
second per-project list. Until then the front's
`extra.knowledge_ingest.allowed_domains` (ADR-015) alone bounded it, and all
four fronts ship `["*"]`, so a project or a user whose agent may not browse
could still have the server fetch any public site.

- **Which policy.** The effective limits of the job's owner in the job's
  project at this site (steps 1-4 of D-3: the site default, the admin
  `site`, `project` and `user` rules, narrowed by the project owner's rule),
  read through `effective_limits_for` from `session_policy_rules`. No new
  table, list or store. Step 5 (the person's choice) is the request's own
  `allow_domains`, which can only narrow (ADR-015, K1.1). How much each
  member may do is therefore the project's layer: the project owner narrows
  it with `PUT /umrs/projects/{id}/session-policy`, an admin per project or
  per user with `umrs-server admin policy set`.
- **Mode.** The widest mode the limits allow decides:
  - `none` only: URL and git ingest are refused, and the refusal names the
    layer that set it (`origin["web.allow"]`, which names the domain cap's
    layer when an empty cap intersection removed the other modes).
  - `allowlist`: the host, and every redirect hop, must be inside the
    `web.domains` cap, matched as in D-3 and the local browse guard (D-9):
    `a.org` is that host, `.a.org` is it and its subdomains. The fetcher
    receives the front list intersected with the cap (`.a.org` becomes the
    fetcher patterns `a.org` and `*.a.org`; of two overlapping entries the
    narrower is kept, and a front `host:port` keeps its port). With
    `allowlist` allowed but no cap, the person names the sites per request,
    as at a session open: the request's `allow_domains`, else the URL's own
    host, so redirects stay on the named sites.
    *Note (2026-10-25, review of wave 79):* each named entry must pass the
    session-open domain check (`normalize_domain`; at most 64), written as a
    fetcher pattern: `a.org`, or `*.a.org` for its subdomains (checked as
    `.a.org`). `*`, `*.com`, `com` and the squid `.a.org` form are refused;
    before this note `["*"]` or `["*.com"]` gave public ingest after the
    owner or an admin removed `public` (umrs `34d83f8a`).
  - `public`: the front list alone.
- **Unchanged on top.** The front list always applies (the policy only
  narrows it), and so do the fetcher's guards: global unicast addresses
  only, the pinned connection, every redirect hop re-checked, ports 80/443
  unless the front names `host:port`, the size and time caps,
  `denied_networks`, the content scan.
- **Fail closed.** Rules that cannot be read, or a server that cannot name
  its own site (the all-sites rows alone would skip this site's rules),
  refuse the ingest, as `sessionpolicy.unavailable` does for a start. A
  front without a database configured has no rules, and the site defaults
  apply (`public`), as for the venue service without a pool.
- **Where.** `umrs/src/umrs/knowledge/ingest_web_policy.py` (resolution,
  pattern intersection, refusals); `sources.SourceResolver(requester=...)`,
  to which the ingest job passes its owner and project; `POST
  /umrs/k2/ingest` refuses before queueing (`sessionpolicy.widening_refused`
  403, or `sessionpolicy.unavailable` 503) and the job re-checks every
  source; `GET /umrs/k2/ingest/allowlist` returns `web_policy` (mode,
  domains, the layer that set each, the entries an ingest may fetch, a
  summary) and its `check` applies both lists; `r4 knowledge allowlist`
  prints it for the current project. Tests:
  `umrs/tests/test_k1_ingest_web_policy.py`,
  `r4/tests/test_knowledge_allowlist_cli.py`.
- **Not covered.** `POST /knowledge/external/search` (`knowledge/external.py`)
  sends query text to fixed metadata services; it stays behind its own site
  flag (knowledge-sharing plan K1.1(e)), not this policy. A redirect hop the
  policy refuses is reported by the fetcher with its front-list wording.

## Related

- [ADR-046](046-remote-session-credential-egress.md): the proxy, rail, approvals and session job this builds on
- [ADR-097](097-live-project-membership.md): the start's project is a checked membership
- Remote-session platform plan: §2.3, §4, §5 E-4, §7 RS-1, §10
- `.github/instructions/*.instructions.md` matching the files each lane touches

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
