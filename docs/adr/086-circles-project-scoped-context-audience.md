# ADR-086: Circles — project-scoped context + audience cells

**Status:** Accepted

> **Rollout (dated 2026-10-19, cleanup C-4):** the "(wave 2026-10-05 executing)" hanger is removed from the status line — the wave landed and is live (wave-ledger 2026-10-05 (2), plus the 2026-10-07 identity-chip addendum and the 2026-10-19 four-rung roster-ladder amendment with the console write verbs). The index reads Accepted; no body text changes are made by this note.
**Supersedes:** the ADR-050 §3 groups authority (migration 0097, `/umrs/groups`)
**Amends:** ADR-050 §3, ADR-080 (one access guard), ADR-041 grantee ladder

## Context

The 0097 groups authority answered "who may be named as an audience" but was
platform-global, which made its name ambiguous next to projects, and left
three questions without a home:

1. Onboarding named **projects** (`users.projects`, minted into JWT
   `project_memberships`) while sharing named **groups** — two people-poles.
2. The projects registry (0095) is owner-only, so "who belongs to a project"
   had no authority (a client `project add` verb shipped unimplemented
   server-side constants and died as dead code).
3. Roster mutation was owner-or-platform-admin only — no delegation tier,
   though `group_members.role` was the designed seam.

The design conversation concluded: projects are the container pole
(budgets, storage bindings, data class, attribution on every work row) and
the people pole must be **project-scoped cells** that bound the contexts of
works and act as the smallest addressable sharing audience. That cell is
the **circle**.

## Decision

1. **Circles are project-scoped.** Migration 0119: `circles` PK
   `(project_id, circle_id)` (FK to 0095 projects), owner-attributed,
   tombstoned on retire (never DELETE). `circle_members` presence-only,
   `role IN ('member', 'admin')`. A retired circle stops resolving for
   access lookups immediately, exactly as retired groups did.
2. **Grammar grows `circle:<project_id>/<circle_id>`** (one slash, both
   parts non-empty; validated in `scope_grammar.parse`). `group:` stays
   parseable but **admits no one** in the one guard and is **rejected on
   writes** (fail-closed supersession; 0097 tables remain for audit).
3. **Personal circle = project entry.** `circle_id == owner_id` is the
   caller's private cell in that project. Creating it is casual (ADR-051
   parity — any verified caller, any project). It never gains other
   members; the owner is never removed.
4. **Named circles need standing.** Create requires the project owner, a
   platform admin, or an affiliated caller (member of any active circle in
   that project). Minting for another user (`for_user`) is admin-only.
5. **Roster ladder is four-run** (amended 2026-10-19 — was three-run:
   the project owner had no rung). Mutations: circle owner, any member
   with `role='admin'`, the containing **project's owner**, or a platform
   admin. Retire stays owner-or-platform-admin (circle admins manage
   people, not lifecycle); the project owner rung covers the roster only,
   never lifecycle (addendum below).
6. **Project affiliation derives from circles.** A user's projects are the
   projects holding at least one of their circles, surfaced by
   `GET /umrs/circles/mine` (`projects` field). No account-carried list is
   added; retiring `users.projects`/`project_memberships` claims and the
   model-registry `model_project_memberships.json` shadow map is a
   follow-up wave (see Out of scope).
7. **One authority, three shapes.** SQL authority
   `umrs.shared.circle_membership.circle_refs_for_user` returns
   `project/circle` ref strings; the API wrapper `circles_for_user`;
   direct-SQL EXISTS joins (rag corpora, user buckets); the workflow
   catalog's ZMQ resolver hook — same patterns as the 0097 wave.

## Consequences

- Roster delegation exists now: a circle owner names circle admins; the
  platform admin stops being the bottleneck for team churn.
- Discoverability without leak: affiliated callers, the project owner and
  admins see a project's circle roster; unaffiliated callers see only
  their own rows; unknown circles 404 (never a smudged empty payload).
- Grantee kinds: ADR-041 `GRANTEE_KINDS` is `("user", "project", "circle")`;
  a circle grantee stores the `<project>/<circle>` ref and is validated
  live at creation (unknown → 404, retired → 409, malformed → 400).
- Gateway routes `/gw/circles*` mirror `/umrs/circles*` (the `/gw/groups`
  edge is removed; repo-side until the next converge).
- TUI/console: `/circles [project]`, `/circles <project> <id>` ride
  `circles.list`/`circles.members` bridge rails; `/groups` is gone.

## Out of scope (follow-ups, named for the gap analysis)

- **users.projects retirement** — JWT `project_memberships` minting,
  workflow `X-Verified-Project-Memberships` checks and the admin CLI
  `user add-to-project` still ride the account field; circles derive a
  read parallel (`/circles/mine`) but claims are untouched this wave.
- **`model_project_memberships.json`** shadow authority in the model
  registry handler — unchanged; fold into the derivation wave.
- **Companion trust circle writes** — `_authorize_scope_write` refuses
  `circle:`/`group:` scopes 422 (trust rows stay user-pinned).
- **`X-User-Groups` header claim** (`shared/isolation.py`) is a separate,
  un-cross-checked authority line — evaluate against circle refs in the
  claims wave.
- **Dead client `project add` branch** in `client_umrs.py`
  (`TaskCmd.ADMIN_*_PROJECT_MEMBER` never existed) — removal rides the
  claims wave.
- **Deploy template** `frontend-multi-hpc/gateway.nginx.conf` never grew
  the groups/circles route; add circles when that template is reconciled
  with the live server.conf.

## Verification

umrs: `tests/test_api_circles.py` (14 pins), `test_access_scopes.py` (17),
grants/endpoint/workflow/KG/catalog/corpus/bucket/asset waves re-pointed
and green. r4: `test_circle_and_quota_client.py`. Guard semantics:
`group:` parses but admits no one and is rejected on writes.

## Addendum (2026-10-19) — project-owner roster rung + TUI write verbs

Maintainer decision (accepted 2026-10-19): a project admin must be able to
put accounts in and out of project circles, and the console needs the verbs,
not only the CLI.

- **`_manager_or_admin` gains a fourth rung.** The containing project's
  owner may add/remove members of any circle in their project, beside the
  circle owner, circle admins and the platform admin. The rung is roster
  only: retire stays owner-or-platform-admin, a personal circle still never
  gains members, and a retired circle still refuses growth — those
  conflicts fire before the ladder, for every role.
- **TUI write verbs.** `/circles` grows `enter <project>` (personal cell),
  `create <project> <id> [name…]`, `add <project> <id> <user> [--role
  admin]`, `remove <project> <id> <user>`, riding new bridge verbs
  `circles.create` / `circles.member_add` / `circles.member_remove` (classed
  MUTATE in the bridge concurrency table). Reads (`/circles [project]`,
  `/circles <project> <id>`, `members`) are unchanged. Retire stays
  CLI-only (`r4 circle retire`) — a tombstone is a lifecycle act, not
  casual console fare.

## Addendum (2026-10-07) — identity chip names the credential state, never masquerades the profile

The chip's no-JWT fallback rendered `{profile}/{project}` (e.g. `default/r4-dev`), which read as an account and hid that nobody was logged in (maintainer report). Now the bridge reports `credential: "jwt" | "api-key" | "none"` alongside the display-only `username` (decoded without verification, never authoritative — unchanged), and the chip renders:

- JWT held: `{username}@{project} ○{project/circle}[ +N]` (unchanged)
- Credentials exist but none decode: `{project} · key-only({profile})`
- Nothing held (or lookup failed): `{project} · signed-out`
- Bridge predating the field (stale): the legacy `{profile}/{project}` shape only

