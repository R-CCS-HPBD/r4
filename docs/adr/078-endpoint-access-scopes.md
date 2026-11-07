# ADR-078: Endpoint access scopes — who may use an endpoint is user / project / group / system, separate from who owns it

**Status:** Accepted (2026-10-01, maintainer request; design choices by the agent under maintainer delegation)
**Date:** 2026-10-01
**Relates to:** ADR-041 (one scope grammar + one ladder), ADR-050 §3 (groups authority), ADR-068 (one internal inference API), ADR-062 (model locality), ADR-076 (the always-on decision endpoint that needs it), gap row **D-58** (new)

## Context

Verified 2026-10-01:

- Inference routing matches the caller's project **exactly**:
  `_discover_endpoint` → `EndpointRegistry.select_endpoint` →
  `_find_candidates` (`umrs/src/umrs/orchestrator/endpoint_registry.py`)
  skips every row whose `project_id` differs. `/v1/models` and
  `/umrs/endpoints/active` list the caller's project only.
- `tier: system` only gates *provisioning* (admin) and labels the row; it
  grants no cross-project use. `chat_completions` takes a
  `prefer_system: bool = Query(True, "Prefer system warm endpoints")`
  parameter it never reads — a system fallback was intended and never built.
- The caller's project is the gateway-verified JWT claim
  (`gateway/sidecar/main.py:_resolve_project_id`; the client's
  `X-Project-ID` is overwritten). OIDC tokens without a project claim fall
  back to `JWT_DEFAULT_PROJECT=default`. The maintainer (2026-10-01): users
  work in their own projects, not `default` — so an always-on endpoint owned
  by one project (ADR-076's Nimble) is unreachable for everyone else, and
  every project would provision its own copy on scarce GB10 nodes.
- The pieces to express "who" already exist: the scope grammar
  (`umrs.shared.scope_grammar`: `system | project:<id> | group:<id> |
  user:<id>`), the groups + membership authority (`/umrs/groups`,
  `groups_for_user`, migration 0097), and ADR-041 grants. ADR-041 grants
  are keyed by asset id; an endpoint's id is its job id, which changes at
  every relay handover (ADR-076 D-4) — a per-job grant would silently expire
  every day.

## Decision

**D-1 — Access is a property of the endpoint row.** `metadata["access"]` is a
list of scope strings from the canonical grammar: `user:<id>`,
`project:<id>`, `group:<id>`, `system`. The owning project always has access
and is implicit. A row without the key means owning project only — exactly
the old rule; nothing becomes shared by accident. Policy code:
`umrs.orchestrator.endpoint_access` (pure; foundation).

**D-2 — Routing: own project first, then shared.** `_discover_endpoint`
keeps every existing own-project path; only when they find nothing does it
call `select_shared_endpoint(caller, model)`: least-loaded READY/DEGRADED row
of another project whose access admits the caller (system → everyone;
project → verified project; user → verified user; group → active
membership). Admin does not widen routing. `/v1/models` and
`/umrs/endpoints/active` add the shared rows; a provision with
`prefer_warm` reuses a shared READY endpoint instead of submitting a
duplicate. Group membership is looked up lazily — only when a group scope is
the deciding candidate — and cached per user for 60 s, so the ordinary path
takes no database connection.

**D-3 — Granting.** Sharing spends only the owner's capacity, so the owning
project (or an admin) may name any `user:`, `project:` or `group:`;
`system` — everyone's model list — is admin-only. Set at provision
(`access` on `POST /umrs/endpoints/provision`, `r4 endpoint up --access`),
changed with `PUT /umrs/endpoints/{job_id}/access` (`r4 endpoint access ID
--set … | --private`; owning project or admin; audited
`endpoint.access_changed`). An explicit grant the caller may not make is a
typed 403.

**D-4 — Recipes carry access.** A roster entry may declare `access:`; it
applies when the provisioning caller may grant it (else the endpoint stays
private, logged). Relay successors (ADR-076) inherit it through the same
provision path. `bespoke-nimble-9b` is `access: [system]`.

**D-5 — Use vs lifecycle.** Access governs use only. Deprovision, refresh,
upgrade and re-scoping stay owning-project-or-admin; usage, quota and audit
of each request are recorded against the *caller's* project, as before.

### Why

- One grammar, no new dialect (ADR-041 rule); groups resolve through the one
  authority that already exists.
- A row property survives relay handovers (the successor is provisioned
  from the same recipe); an id-keyed grant would not.
- Own-project-first keeps every existing deployment behaving identically and
  lets a project override a shared model with its own endpoint.

## Consequences

- Positive: one always-on decision model (or any curated model) serves every
  project; projects stop duplicating GB10 endpoints.
- Positive: per-user and per-group sharing without moving projects.
- Cost: shared endpoints appear in other callers' `/v1/models`; `system` is
  admin-curated to keep that list meaningful.
- Locality (ADR-062): shared endpoints are site-local UMRS endpoints, never
  public providers — no new egress.
- NOT in scope: ADR-041 grant rows for endpoints (kept for id-stable
  assets); per-scope quotas or rate limits on shared endpoints; the legacy
  `api/chat.py` discovery path (unchanged).

## Alternatives Considered

- **ADR-041 grants on endpoint ids** — break at every relay handover.
- **Treat every `tier: system` endpoint as global** — silently widens
  existing system endpoints and conflates owner with audience.
- **Provision shared endpoints under a synthetic `system` project** — fakes
  an owner, hides who pays, and still needs a routing change.

## Rollout-status (2026-10-01)

- D-1…D-5 landed in the working tree: `umrs/src/umrs/orchestrator/endpoint_access.py`,
  `EndpointRegistry.select_shared_endpoint`, `api/llm.py` (discovery +
  `/v1/models`), `api/endpoints.py` (provision `access`, shared warm reuse,
  `/active`, `PUT /{job_id}/access`), `api/_endpoint_access.py`; r4
  `Client.set_endpoint_access`, `provision_endpoint(access=)`, `r4 endpoint
  up --access`, `r4 endpoint access`. Tests: umrs `test_endpoint_access.py`
  (27) + existing endpoint/llm suites green; r4 `test_endpoint_access_cli.py` (3).

## Related

- [ADR-041](041-asset-grants-scope-grammar.md), [ADR-050](050-visibility-and-session-history-build.md),
  [ADR-068](068-one-internal-inference-api.md), [ADR-076](076-decision-model-role.md)
- docs/user-manual/06-endpoints.md (Endpoint Tiers → access)
