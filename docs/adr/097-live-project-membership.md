# ADR-097: Live project membership — the token proves who, the server decides which project

**Status:** Accepted (2026-10-22, maintainer); Amendment D-7 (ZMQ rail) 2026-10-22, agent under delegation; Amendment D-8 (a disabled account's tokens) 2026-10-23, agent under delegation
**Date:** 2026-10-22
**Relates to:** ADR-051 (durable projects registry), ADR-086 D-6 (departs from it: `users.projects` stays the authority), ADR-080 (one access guard for shareable resources), ADR-086 (circles), H-1 (signed memberships header), maintainer request of 2026-10-22

## Context

A user's project membership is baked into the login token when it is minted,
and nothing reads it again until the token expires.

- **Minting.** Every minter copies the account's project list into the
  claims: `umrs admin token generate-jwt` / `user add --issue-token`
  (`umrs/src/umrs/cli/admin/main.py` `_build_jwt_payload`, `projects[0]`
  becomes `project_id`, the rest `project_memberships`; default lifetime 30
  days) and the SAML session (`gateway/sidecar/saml_sp.py` `_mint_session_jwt`).
- **Gateway.** The sidecar takes `project_id` from the token
  (`gateway/sidecar/main.py` `_resolve_project_id`) and signs it as
  `X-Verified-Project-ID` (`_verified_project_headers`). nginx overwrites the
  client's `X-Project-ID` with that value on every `auth_request` lane
  (`gateway/nginx/conf.d/server.conf`, `proxy_set_header X-Project-ID
  $auth_project_id`). About 25 UMRS route modules trust the signed headers
  (`verified_headers_trusted`).
- **The account record.** `umrs admin user add-to-project` /
  `remove-project` edit the venue's user record (`umrs/src/umrs/admin/user_store.py`
  `add_project`), written both to `<user_files>/<name>.json` and to the
  `users` row keyed `(venue, username)` (migrations 0110, 0113). No request
  path reads it.
- **The client.** r4 already sends the project the user selected as
  `X-Project-ID` (`r4/src/r4/_http.py` `build_headers`); `r4 project switch`
  and the TUI's `project_switch` change it. The gateway ignores it.
- **Circles are already live.** `circle:` access is resolved per request from
  `circle_members` (`umrs/src/umrs/shared/circle_membership.py`).
- `JWT_REQUIRE_PROJECT_MEMBERSHIP` in the sidecar env template has no reader
  anywhere (dead configuration).

What this breaks (2026-10-22): a project member's account lists
`['default', 'example-project-b']` but the token was minted with `default`, so the user
could not work in `example-project-b` and the console never listed it. A user added to a
project needs a new token, and a removed user keeps access until the token
expires.

## Decision

- **D-1 Source of truth.** A user's project memberships are the venue's live
  user record (`UserStore.get(user).projects`, the `users` row first) plus
  the active projects the user owns (`projects.owner_id`). Token claims stop
  being the authority once D-4 reaches `enforce`.

  Circle affiliation (ADR-086 D-6) is NOT a membership. ADR-086 D-3 lets
  any verified caller create a personal circle in any project, so counting
  affiliation would let anyone act in any project. Affiliation stays what it
  is today: it lists a project and lets you read its row and roster. This
  departs from ADR-086 D-6's plan to retire `users.projects`. That list is
  what admins grant with, so it stays the authority. Retiring it needs a
  granting mechanism that a user cannot self-serve.
- **D-2 UMRS exposes it.** `GET /umrs/projects` (already caller-scoped)
  adds `memberships`: the verified caller's live project ids, sorted. Nothing
  else in that response changes.
- **D-3 The gateway decides the project per request.**
  - nginx hands the client's `X-Project-ID` to the auth check as
    `X-Requested-Project-ID`.
  - No requested project, or the token's own project → that project.
  - Another project → accepted when the caller is an admin or the project
    is in the caller's live memberships. Otherwise 403 `project_not_member`.
    When the lookup is unavailable, 503 `membership_unavailable` (fail closed).
  - The sidecar signs the chosen project as `X-Verified-Project-ID` and the
    live list as `X-Verified-Project-Memberships`, with the existing HMACs.
    UMRS routes do not change.
- **D-4 Modes.** `GATEWAY_PROJECT_MEMBERSHIP` is one of:
  - `token` (default in code): today's behaviour; the requested project is ignored.
  - `live`: D-3 switching. The token's own project is still accepted when
    the live list lacks it, and that drift is logged.
  - `enforce`: the token's own project must also be a live membership,
    else 403 `project_membership_revoked`. A lookup failure is 503 for every request.

  Deploy sets `live` first, and `enforce` after a soak with no drift logged.
- **D-5 Cache.** The sidecar caches each user's live list for 30 s,
  bounded in size. A membership change takes effect within 30 s, with no
  new token.
- **D-6 Client.** r4's login sync adopts `memberships` as a third leg,
  next to owned and circle-derived projects, so the console lists every
  project the user may select. Nothing else changes on the client.
- **D-7 Scope.** `/validate`, `/validate-optional` and `/validate-download`
  use one resolver. The ZMQ/CURVE rail is not covered: its session binds the
  token's projects at registration (`umrs/src/umrs/api/zmq_sessions.py`).
  That rail gets the same rule in a follow-up wave. *(2026-10-22: done; see
  "Amendment D-7" below.)*

### Why

- Revocation must work. A removed member keeps access for up to 30 days
  under token claims.
- Every membership change was a credential hand-off: an admin mints, delivers
  and the user logs in again. That is slow and error-prone, and it moves
  secrets around more often.
- The token and the account record disagreed with nothing to reconcile them.
  Circles were live; projects were not.
- The membership data sits next to each venue's gateway. One cached lookup
  per user per 30 s is cheap at this scale. Short-lived self-contained
  tokens pay off at a scale and distance the platform does not have.

## Consequences

- Positive: adding or removing a member takes effect within 30 s. A user in
  several projects selects one per request; the client already sends it.
  Old tokens keep working in `live`.
- Cost: one UMRS request per user per 30 s per gateway process. In `enforce`,
  a UMRS outage blocks requests that would have passed on the token alone.
  The decision moves into the sidecar, which grows a small cache and an
  outbound call.
- NOT in scope:
  - the ZMQ/CURVE rail (D-7 follow-up; covered by Amendment D-7, 2026-10-22);
  - a cross-venue membership table (user records stay per venue);
  - short-lived tokens with refresh;
  - removing `project_id` from newly minted tokens (it stays the default project).

## Alternatives Considered

- **Re-mint the token on every change.** This is what happens today, and it
  is rejected for the reasons above.
- **Check membership in each UMRS route.** About 25 modules trust the signed
  project. Changing each one is a large, uneven blast radius; one decision
  point in the gateway keeps them unchanged.
- **Short-lived tokens with refresh.** These fix staleness but not the
  hand-delivered admin tokens. Kept as a later step through OIDC/SAML.
- **Circle affiliation as membership (ADR-086 D-6).** Rejected: personal
  circles are self-service in any project (ADR-086 D-3), so anyone could
  enter any project.
- **A new `project_members` table.** It would duplicate the `users.projects`
  column that `add-to-project` already writes per venue. A cross-venue table
  is a separate decision.

## Rollout-status (2026-10-22)

Proposed, with implementation started on `wave/live-membership`:

- D-2 UMRS `memberships` field, with tests.
- D-3/D-4/D-5 sidecar resolver, modes and cache, with tests.
- D-3 nginx forwarding, with config tests.
- D-6 r4 login sync leg, with tests.

Deploy sets `GATEWAY_PROJECT_MEMBERSHIP=live`. `enforce` waits for a soak.
D-7's ZMQ leg is open.

Evidence:
- `umrs/tests/test_api_projects.py::TestLiveMemberships` (5 tests).
- `gateway/sidecar/test_sidecar.py`, the ADR-097 block (13 tests).
- `gateway/tests/test_adr097_live_membership_nginx.py` (4 tests).
- `r4/tests/test_login_project_sync.py::TestLiveMemberships` (3 tests).
- The real gateway config passes `nginx -t` on nginx 1.28.1. An end-to-end
  run of the real nginx config and sidecar against a UMRS stand-in showed:
  - a selected member project reaches the upstream;
  - a non-member project gets a JSON 403 `project_not_member`;
  - with UMRS down, a switch gets a JSON 503 `membership_unavailable`.

## Rollout-status (2026-10-22, acceptance)

Accepted by the maintainer on 2026-10-22 ("accept ADR-097"). Implementation
is #537 (D-1..D-6; deploy default `live`). The follow-up circle-share check
is #538 (ledger (50)). Open: D-7 (the ZMQ/CURVE rail) and the switch to
`enforce`, which waits for a soak with no "membership drift" in the sidecar
logs.

## Amendment D-7 (2026-10-22): the ZMQ/CURVE rail

Implemented on `wave/rs0-zmq-project` (umrs branch `rs0-zmq-project`), as
item S-1 of wave RS-0 of the
remote-session platform plan.
The plan's start was ratified by the maintainer on 2026-10-22. The
decisions below were delegated to the agent; the reasoning is recorded with
each one.

### What was open

D-7 left the ZMQ rail out. Every project-scoped ZMQ command took its
project from the payload unchecked: a bound CURVE session could submit a
job, list jobs or run a workflow in any project by naming it.
`SessionGrants.projects` was never read. The workflow-status family
(`api/workflow_auth.py` `zmq_caller_from_payload`) also treated a
client-supplied `_identity_verified` as gateway-verified. r4's HYBRID mode
sends these commands over HTTP, which D-3 covers. DIRECT and HPC_LOCAL use
ZMQ.

### Decision

- **D-7.1 One check, at the one dispatch point.**
  `CommandRegistry.dispatch` (`umrs/src/umrs/server/commands/registry.py`)
  runs it after the role check, for every client command. The rule lives in
  `server/commands/project_check.py`. Handlers do not change.
  - **Why:** the gateway decides in one place for the same reason (see
    Alternatives). A command added later is covered without touching it.
- **D-7.2 What a payload names.** The check reads the top-level
  `project_id`, `params.project_id`, and the project a handler falls back
  to when none is named:
  - JOB_SUBMIT falls back to `default` (`payload.get("project_id", "default")`);
  - RUN_WORKFLOW falls back to `_workflow_project_id(params)`.

  Every named project must pass. The commands registered today that name
  one are JOB_SUBMIT, JOB_LIST, RUN_WORKFLOW, WORKFLOW_EXECUTIONS,
  WORKFLOW_STATUS, WORKFLOW_RESULTS, WORKFLOW_CANCEL, WORKFLOW_ERRORS,
  MODEL_REGISTER and MODEL_LIST. MODEL_REGISTER also keeps its own
  model-catalog ACL.
  - **Why the fallback is checked:** otherwise a caller who is not a
    member of `default` could still write into it by omitting the id.
  - **Why ids are compared exactly:** they are compared as the handler
    stores them, unstripped. A padded id fails closed.
  - An absent, `null` or empty id names nothing. JOB_LIST and the status
    family then add no project filter.
- **D-7.3 Same membership source.** The check calls `live_memberships`
  (`umrs/api/projects.py`), the function behind D-2's `memberships`. It
  reads the venue's user record plus owned active projects, and drops a
  retired row (D-1). The lookup uses the process's default Postgres pool,
  is cached per user for 30 s and bounded (D-5), and does not cache
  failures. `UMRS_ZMQ_MEMBERSHIP_CACHE_TTL_SEC` sets the TTL.
  - **Whose memberships:** the bound CURVE session's user (`entry.user_id`).
    Without a session (auth modes other than `session`), the client_id is
    used.
- **D-7.4 Same admin bypass.** An admin acts in any project, with no
  lookup. "Admin" is the server's own role verdict (`get_auth_context`):
  the CURVE session's grants, or an admin token presented to the server.
  It is never a payload claim. The service role is not exempted: no ZMQ
  path mints it, and the gateway has no such bypass.
- **D-7.5 Same modes.** `UMRS_ZMQ_PROJECT_MEMBERSHIP` takes D-4's values.
  The session's token-bound projects (`SessionGrants.projects`, taken from
  the JWT at registration) stand where the token's own project stands in D-4:
  - `token`: only the token's projects, with no lookup.
  - `live`: a live membership; a token project that is no longer live still
    passes and is logged as "ZMQ membership drift".
  - `enforce`: live memberships only; a revoked token project gets
    `project_membership_revoked`.
  - **Why `live` is the default in code, and an unknown value means
    `live`:** the gateway's code default `token` kept behaviour that was
    already safe. Here, "today's behaviour" is the hole D-7 closes, so the
    default must close it. `live` is also the gateway's deploy default, so
    both rails act the same with no deploy change.
- **D-7.6 Same unknown-membership handling.** An unreadable lookup returns
  `None`: Postgres is unconfigured, unreachable, or `live_memberships`
  returned None. That is treated as unknown, never as "no memberships":
  - in `live`, a token project passes, and any other project gets
    `membership_unavailable`;
  - in `enforce`, every project gets `membership_unavailable`.
- **D-7.7 Same refusal codes.** The refusal is the registry's typed JSON
  error reply: `{"status": "error", "code": ..., "message": ...}`. The codes
  and messages are the gateway's:
  - `project_not_member`: "not a member of project '<id>'";
  - `project_membership_revoked`: "no longer a member of project '<id>'";
  - `membership_unavailable`: "project membership could not be checked".

  The handler does not run.
- **D-7.8 `_identity_verified` is never taken from the wire.** Only one
  path sets it: the HTTP trigger route
  (`api/workflows.py` → `handle_workflow_command(..., trusted_identity=...)`).
  That route checks the gateway HMAC first and calls the handler **in
  process**. The flag never crosses ZMQ, so the server needs no wire-level
  binding for it, only a strip:
  - the registry drops the key from every client payload before any
    handler runs;
  - `handle_workflow_command` drops it unless `trusted_identity` is set.
    This covers in-process callers that pass payloads through, such as
    ingest triggers.

  As a result, the ZMQ status family reads a project claim as
  `mvp_claim`/`unverified`, never as `gateway_verified`.
  - **Not done here:** treating a project that passed D-7.1 over a bound
    session as verified provenance. That would open the strict-mode status
    family to honest ZMQ owners. It widens access, so it waits for its own
    review. Until then, r4 HYBRID mode (HTTP) is the path in strict mode.

### Consequences

- A spoofed project on the ZMQ rail is refused. A member's call and an
  admin's call are unchanged.
- A ZMQ client that set `_identity_verified` itself loses the access it
  forged. Honest clients never sent it: r4 and the UMRS client do not.
- A ZMQ caller who is not a member of `default` can no longer submit a job
  or run a workflow without naming a project.
- Cost: one Postgres lookup per user per 30 s per UMRS process. In `live`,
  a Postgres outage refuses only projects outside the session's token.

Evidence: `umrs/tests/test_zmq_project_check.py` (55 tests). Run against
the unchanged registry and workflow handler, 27 fail, and they are the
spoof, mode and forged-flag cases. The full umrs suite and the
umrs-changed gate pass.

## Amendment D-8 (2026-10-23): a disabled account's tokens stop working

Implemented on `wave/hard-disabled-token` (umrs branch
`hard-disabled-token`), a follow-up of RS-0 S-2 in the
remote-session platform plan.
The decisions below were delegated to the agent; the reasoning is recorded
with each one.

### What was open

RS-0 made `umrs-server admin user disable` end what the account holds:
leases, jobs, CURVE sessions and remote sessions. The account's JWTs kept
working:

- **The gateway.** In `live`, the sidecar admitted the token's own project
  even when the live list lacked it (D-4 drift). A disabled account's live
  list holds only the projects it owns (`_record_projects` returned `[]`
  for it, but `projects.owner_id` still counted), so its ordinary HTTP
  calls passed until the token expired, 30 days by default. Admins were
  never looked up (D-3), so an admin JWT outlived its owner's disable in
  every mode.
- **UMRS.** The admin routes RS-0 added (`api/admin_sessions.py`) and the
  endpoint registry's admin check they reuse (`endpoints._caller_is_admin`)
  read the `admin` role from the JWT alone. The operator CLI calls them
  directly, not through the gateway.

### Decision

- **D-8.1 UMRS reports the caller's standing on the existing lookup.**
  `GET /umrs/projects` gains `disabled`: `false`, `true`, or `null` when the
  account record could not be read. For a disabled account it answers
  `memberships: []`, owned projects included. The status stays 200.
  - **Why a field, not a typed 403:** the sidecar treats any non-200 as a
    failed lookup. An older sidecar would then admit a disabled account's
    token project in `live`, exactly as before. With the field, an older
    sidecar reads only `memberships: []`: in `live` it refuses every
    project but the token's own (`project_not_member`), and in `enforce` it
    refuses that one too (`project_membership_revoked`). An older UMRS sends
    no `disabled`, and a new sidecar reads that as "enabled". Both mixes
    are no worse than today. A 200 also lets the sidecar cache the verdict
    exactly as it caches memberships.
  - **Why owned projects are dropped:** a disabled account acts in no
    project. The ZMQ check (D-7.3) reads the same `live_memberships` and
    follows.
  - The record is read like the account sweep reads it
    (`account_cascade.read_record`), and a disable in either lane wins. An
    unknown user is "not disabled", as before.
  - *(2026-10-23, review fixes.)* The caller is resolved the way
    `UserStore.resolve_user` resolves it: the username, then the
    `oidc_subject` mapping. Under JWKS/SAML the token `sub` is the IdP's
    subject (a numeric RIKEN ID, or a mixed-case name), which is no valid
    username; the first version read it as "unknown, not disabled". The
    record's `projects` (D-1) are read through the same mapping.
  - *(2026-10-23, re-review fixes.)* Precedence is `resolve_user`'s: a
    username (the `users` row, then the JSON file) outranks another
    account's `oidc_subject`. The first fix took a JSON `oidc_subject`
    match before reading the row named by the identity, so a disabled
    `alice` that existed only as a row read as the enabled `bob` whose
    `oidc_subject` was `"alice"`. One directory scan per lookup: an unknown
    identity scanned twice and a subject lookup re-scanned.
  - *(2026-10-23, re-review fixes.)* Owned projects (D-1) count under every
    id of the account: its username, its `oidc_subject` and the identity it
    called with (`account_cascade.owner_ids`), in `GET /umrs/projects`, the
    account sweep and the session guard (`refusal_for`). A project created
    through the gateway is owned by the token `sub`, the IdP subject; after
    the first fix mapped the sub to the username, the sweep in `enforce`
    read that project as `project_membership_revoked` and ended its leases,
    sessions and jobs every 30 s.
  - *(2026-10-23, round-3 fixes.)* An id never names two accounts. An id
    other than the record's username that is another existing account's
    username (its JSON file or its `users` row) is left out of
    `owner_ids`: `bob.oidc_subject == "alice"` made the projects `alice`
    owns bob's live memberships, so the session guard admitted bob to
    them under `enforce`. `UserStore.create` and `set_oidc_subject` now
    refuse such a subject, and `create` refuses a username that is
    already another account's subject (typed `UserStoreError`; the admin
    CLI prints it and exits 2). A subject no account carries as its
    username (an IdP id, or a name not yet taken) still owns its projects.
  - *(2026-10-23, review fixes.)* The JSON file is read FIRST, and that
    read never raises. A disable there is evidence even while the `users`
    row cannot be read (the first version read the row first, so a
    Postgres outage hid a JSON disable and answered `disabled: null`). Only
    when neither lane shows a disable and the row is unreadable is the
    answer `null`. *(Re-review.)* The identity's own file answers a
    disable at once; a disable on a file matched by `oidc_subject` answers
    once the row read confirms no username outranks it, or while that row
    cannot be read.
- **D-8.2 The sidecar refuses a disabled account.** In `live` and
  `enforce`, `resolve_request_project` looks up the caller's standing first,
  for admins too (they still need no membership). A disabled account gets
  403 with `X-Auth-Error: account_disabled`, before any project rule. nginx
  relays the code in its JSON 403 body (`@error403`) unchanged.
  - **Why 403, not 401:** the token is authentic, and logging in again
    cannot help. A 401 invites a client to retry the login.
  - **Where it applies:** `/validate`, `/validate-download` and the
    sidecar's own `/audit` refuse. `/validate-optional` treats the account
    as anonymous, as it treats every refusal. `/auth/web-login` still sets
    the cookie; every later request through `/validate` is refused.
- **D-8.3 Same cache.** The verdict is cached with the memberships: one
  lookup per user per `GATEWAY_MEMBERSHIP_CACHE_TTL_SEC` (30 s). A disable,
  or a re-enable, takes effect within that window. Failures are not cached.
- **D-8.4 Same failure rules.** An unreadable standing (the lookup failed,
  or `memberships`/`disabled` is `null`) is never read as "disabled":
  - a non-admin follows D-3/D-4 unchanged: in `live` the token project
    passes and any other project is 503 `membership_unavailable`; in
    `enforce` every request is 503;
  - an admin needs no membership, so in `live` the request passes as
    before, and in `enforce` it is 503 `membership_unavailable`, because
    D-4 fails every request closed there.
- **D-8.5 `token` mode does no lookup.** It stays the pre-ADR-097
  behaviour, so a disable takes effect there only at token expiry.
  - **Why:** `token` is the code default, kept for gateways without a
    reachable UMRS. A standing lookup there would add an outbound call that
    does not exist today. Failures are not cached, so with UMRS unreachable
    that would mean a lookup, and its timeout, on every request. Every
    deployed gateway runs `live` (`gateway-sidecar.rccs.env.j2`). A venue
    that needs revocation runs `live` or `enforce`.
- **D-8.6 UMRS refuses a disabled admin.** `endpoints._caller_is_admin`
  and `admin_sessions.require_session_admin` check the account behind an
  admin credential: the JWT's `sub`, or the gateway-signed `X-User-ID`.
  - A disabled account is not an admin. The registry routes then treat it
    as a non-admin caller. The admin session routes answer a typed 403
    (`companionvenue.forbidden`, "account '<id>' is disabled"), audited as
    `venue.admin.denied` with reason `account_disabled`.
  - **Evidence only (fail open):** an absent or unreadable record keeps the
    verdict (`account_cascade.account_disabled`).
  - **Why fail open:** the account sweep follows the same rule. A
    user-store outage must not lock the operator out of the admin routes
    used to clean up during one. The lookup runs only for a credential that
    already claims admin, so non-admin calls read nothing new.
  - *(2026-10-23, review fixes.)* The JSON-first read of D-8.1 applies
    here too: a JSON disable refuses the admin while the row is
    unreadable. An unreadable record logs one traceback a minute at most
    (`account_cascade.warn_unreadable`), not one per admin request. The
    direct-JWT project check (`endpoints._claim_can_access_project`, used
    by `_project_id_authorized_by_claims`) applies the same rule: a
    disabled account's admin JWT scopes to its own claimed projects, so
    asking for another project is a 403. *(Re-review.)* It reads the
    claims first, so an admin in a claimed project reads no account record.
- **D-8.7 A light lookup** *(2026-10-23, review fix)*. The sidecar asks
  `GET /umrs/projects?standing_only=true`: UMRS then answers `memberships`
  and `disabled` (plus `standing_only: true`) without the project listing.
  - **Why:** an admin's listing is every project row, run on each lookup
    (one per admin per 30 s). It could outlast
    `GATEWAY_MEMBERSHIP_TIMEOUT_SEC` (3 s), and a timed-out lookup is an
    unreadable standing (D-8.4): a disabled admin then passed in `live`.
  - **Compatibility:** an older UMRS ignores the parameter and answers the
    full listing, which carries the same two fields. A request without the
    parameter gets the same response as before.

### Consequences

- A disabled account's gateway tokens are refused within 30 s in `live` and
  `enforce`, admin tokens included. A disabled admin's JWT no longer works
  on the UMRS admin routes, which the gateway does not serve.
- Cost: admins are now looked up too, one UMRS request per admin per 30 s
  per gateway process. In `enforce`, a UMRS outage now blocks admins as
  well as everyone else.
- Not covered:
  - `token` mode (D-8.5);
  - UMRS routes other than the admin checks, which trust the gateway's
    signed identity (D-3);
  - a disabled non-admin's JWT sent to UMRS directly, not through the
    gateway: its claimed projects still scope it there (the admin case is
    covered, D-8.6);
  - the ZMQ rail, where RS-0's keystore sweep ends a disabled account's
    CURVE sessions within 60 s;
  - minting: `umrs-server admin token generate-jwt` already refuses a
    disabled account, but the SAML session (`saml_sp._mint_session_jwt`)
    reads no account record and still mints one. The gateway then refuses
    that token on every request.

Evidence:
- `gateway/sidecar/test_sidecar.py`, the Amendment D-8 block (11 tests, and
  the admin test now expects one lookup). Run against the unchanged sidecar,
  10 fail: 9 of the new tests and the admin test. The two that pass pin
  behaviour kept on purpose: an older UMRS without `disabled`, and `token`
  mode with no lookup.
- `umrs/tests/test_api_projects.py::TestDisabledStanding` (5),
  `umrs/tests/test_rs0_admin_sessions.py::TestDisabledAdmin` (5) and
  `umrs/tests/test_rs0_endpoint_admin_scope.py::test_a_disabled_admins_jwt_is_not_an_admin`.
  Run against the unchanged UMRS, 9 of these 11 fail. The two that pass pin
  the re-enable and the fail-open rule.
- Review fixes (2026-10-23): `umrs/tests/test_api_projects.py::TestDisabledStandingReviewFixes` (6),
  `umrs/tests/test_rs0_admin_sessions.py::TestDisabledAdminReviewFixes` (3),
  `umrs/tests/test_rs0_endpoint_admin_scope.py::test_a_disabled_admins_jwt_cannot_ask_for_a_foreign_project`,
  and two in `umrs/tests/test_account_cascade.py` (the IdP subject, the
  rate-limited warning). Run against the lane's first UMRS commit, 9 of
  these 12 fail; the three that pass pin the fail-open rule and the
  unchanged full listing. In `gateway/sidecar/test_sidecar.py`, the
  `standing_only` test fails against the lane's first sidecar; the older-UMRS
  test passes on both.
- Re-review fixes (2026-10-23): `umrs/tests/test_account_cascade.py::TestIdpSubjectOwnership` (4),
  `::TestResolutionPrecedence` (4),
  `umrs/tests/test_api_projects.py::TestDisabledStandingReviewFixes::test_projects_owned_under_any_of_the_accounts_ids_count`
  and `umrs/tests/test_rs0_endpoint_admin_scope.py::test_an_admin_in_its_own_claimed_project_reads_no_account`.
  Run against the lane's second UMRS commit, 9 of these 10 fail; the one
  that passes pins the subject-mapped JSON disable during a Postgres outage.

## Related

- [ADR-051](051-self-service-projects-agent-resource-poles.md), [ADR-080](080-one-access-guard-shareable-resources.md), [ADR-086](086-circles-project-scoped-context-audience.md)
- `docs/admin/operations/` user and project administration runbooks

*(2026-11-06: a member's account name replaced for the public export (repo-rename plan P5b-2).)*

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
