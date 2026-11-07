# ADR-041: Asset grants — version-pinned sharing with tombstone-on-revoke, on one scope grammar

**Status:** Proposed (with implementation in WP-06 round 1, same PR)
- **Date:** 2026-09-07
- **Related:** ADR-011 (data catalog + scopes), ADR-027 (scopes + discovery),
  `umrs.shared.scope_grammar` unification commit c8ebb316 (trust/asset-label
  grammar), ratified P10 clause "grants over re-scope is the intent".

## Context

Sharing today is **re-scope**: `publish_model` rewrites the row's scope
(`crud.py` `publish_model`, UPDATE scope/copy), provenance of who-handed-it-to-
whom is lost, and revocation is impossible without yanking the whole scope
tier. There is no grant resource: no object/grantee tuple, no version pin, no
expiry, no tombstone-on-revoke. Gap G-P10-1 (Missing) records this; the
ratified P10-C2 claim is "sharing happens by version-pinned grants with
tombstone on revoke, not by copy/re-scope".

Independently, "scope" speaks three dialects across assets (G-P10-2): models
enforce the `ModelScope` enum + NULL-owner invariant, knowledge graphs carry
`owner_id`/`project_id`/`kind` strings, endpoints infer a tier from
`project_id` (`_default_owner_type`). Two ladders already agree on the
semantics (model registry `ACLEnforcer`, knowledge `access_scope`), and a
commit level (trust labels, asset index) already parses the
`system | project:<id> | group:<id> | user:<id> | session:<id>` label grammar
in `umrs.shared.scope_grammar`. What is missing is **one** normalization of
asset *records* into that vocabulary and **one** authorization ladder every
asset family delegates to.

## Decision

### Grant resource (migration 0083)

`asset_grants(grant_id, asset_kind, asset_id, grantee_kind, grantee_id,
version_pin, expires_at, tombstoned_at, created_by, created_at)`.

- `asset_kind ∈ {model, knowledge_graph}`; `grantee_kind ∈ {user, project}`
  (CHECK constraints). Both lists extend by migration, not by free text.
- **Revoke = tombstone.** `tombstoned_at` is set on revoke; rows are never
  DELETEd, so the grant/revoke audit trail survives. A partial unique index
  (`WHERE tombstoned_at IS NULL`) allows exactly one live grant per
  (asset, grantee, pin); re-granting after a revoke is always possible.
- **Version pin.** `NULL` follows the current asset. A non-NULL pin authorizes
  only an exact version match; an asset that exposes no version can never
  satisfy a pin — **fail-closed by design** (pinning is for versioned asset
  kinds; models/graphs are unpinned until a versions table lands).
- **Grants authorize READS only.** Create/list/revoke require static write
  authority on the asset (the unchanged ownership ladder); writes are never
  grant-authorized, so grants cannot compound into ownership.

### One authorization ladder (`umrs.shared.scope_grammar`)

The canonical grammar module gains the ladder leg:

- `AssetScope(kind, owner_id, project_id)` + `normalize_asset_scope()` maps
  every asset representation (`ModelScope` value, graph `kind`,
  endpoint owner tier) into the label grammar's vocabulary.
- `scope_readable` / `scope_writable` is the single ladder: admin does
  everything; anonymous stays permissive (mvp dev mode); SYSTEM read-all /
  admin-write; USER owner-only; PROJECT members, with the knowledge-side
  long-settled rules folded in (global project rows readable by any
  authenticated caller; `system` namespace shared-read/admin-write;
  `user:<uid>` namespaces bound to that user).
- `knowledge.access_scope.graph_readable/graph_writable` now **delegate** to
  the ladder (semantics pinned unchanged by equivalence tests). No new dialect
  module was introduced — the 2026-09-02 review rule ("a new store that
  invents a third dialect gets rejected") holds.

### Fetch-path authorization seam

`ACLEnforcer` gains an optional `grant_reader` seam: when the static tier
denies a read, live grants are consulted (`_grant_allows`). No reader attached
behaves exactly as the legacy static ladder (fail-closed default unchanged).
Writes never consult the seam.

### Surface

- Routes `/umrs/grants` (create/list-for-asset incl. tombstones/ mine / revoke),
  identity via gateway-verified headers; 401 without identity, 503 without pool,
  404-shaped for assets the caller cannot write.
- Production edge: `/gw/grants* → /umrs/grants*` (auth + identity forwarding,
  mirroring `/gw/loops/`).
- Client `create_grant / list_grants / list_my_grants / revoke_grant`; CLI
  `r4 grant create|list|mine|revoke`.

## Consequences

- G-P10-1 → Partial: grant lifecycle complete for models + knowledge graphs
  (lifecycle/tombstone/idempotency tests, cross-project grant coverage);
  console badges and more asset kinds remain round 2.
- G-P10-2 → Partial: one normalization + one ladder govern models, knowledge
  graphs and catalog namespaces; the endpoint-registry tier inference still
  lives in its module (delegation to the ladder) and no column migrations —
  both named round-2 legs.
- `publish_model` (re-scope) is untouched in round 1; superseding it with
  grant-based publish is a follow-up decision, not an invariant yet.

## Verification

- `umrs/tests/test_asset_grants.py` (35) — lifecycle, authorization matrix,
  pin fail-closed, store SQL shapes, ACL seam incl. writes-never-granted.
- `umrs/tests/test_scope_grammar.py` (+28) — normalization, ladder matrix,
  graph-delegation equivalence, label/ladder vocabulary round-trip.
- `umrs/tests/test_grants_api.py` (9) — route lifecycle: create→mine→list→
  revoke→tombstone persists; 401/503/404; idempotent revoke.
- `r4/tests/test_grant_client.py` (7) — payload/params/routes + CLI xor check.

## Rollout-status (2026-09-12, WP-06 round 2 leg-b)

- **asset_kind extension landed** (umrs `104d9591` + b1556b1c pins):
  migration `0090` (CHECK drop+re-add per Postgres constraint semantics)
  admits `endpoint` and `workflow`. `bundle` stays deliberately out: no
  durable bundle-owner record exists yet, and fabricating one would lie.
- Endpoint grants writable via the in-process registry chain (caller project
  OR recorded owner_id; admin by ladder), workflow grants writable via the
  workflow catalog (owner; system scope lands on admin).
- Open legs unchanged: console badges (UI package) + version-pin consumers
  (need the versions-table wiring decision; VectorVersionStore bootstrap
  config has not been decided yet).

## Rollout-status (2026-09-12, WP-06 r2 legs b+c)

- **leg-b landed** (umrs `104d9591` + b1556b1c): endpoint + workflow kinds
  admitted by 0090; the two ladders implement writable checks naming the
  catalog/endpoint authority (never a second dialect). `bundle` stays out
  deliberately (no durable owner rows).
- **leg-c landed** (umrs `fb48893a`): VectorVersionStore is now pool-injected
  at JobService boot time (never a fresh dsn per call) so the WP-10 r2
  venue-pin enrichment can read `venue_active_version` — model pins of the
  attempt manifest gain their venue resolution on every venue with the
  wiring enabled; an un-wired venue stays spec-pins-only honestly.
- Open: console badges (UI package, not backend) + the corpus mortality
  sweep operators (not code).
