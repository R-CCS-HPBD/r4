# ADR-050 — Visibility tiering on knowledge surfaces + session-history knowledge build (WP-22)

**Status:** Proposed (Wave 1 executing same day)
- **Related:** ADR-041 (scope ladder), ADR-026/027 (catalog and corpus planes), ADR-049 (ontology registry).
  User directive 2026-09-13, recorded per-solution after narrowing sub-decisions: hide ambient corpus by READ GATING (no deletion), history build as a chat tool verb (no batch job).

## Context

Three live facts:
1. The K3 facade lanes (catalog, knowledge_graph, central corpus) answer
   per-caller project rows today, but "ambient" content — seeded demo
   corpora, rows minted before projects existed, rows of discontinued
   projects — has no tier marker and is only SOMEWHAT distinguishable
   from live project data.
2. Import/share semantics make some rows legitimately cross-project
   (bundle imports, share grants). Visibility must name the allowed
   families explicitly instead of assuming "project-scoped" is the whole
   story.
3. The companion can open a session with `/acquisition open` and feed
   live data under the write gate, but a chat that STARTED with knowledge
   building OFF has no way to transform the already-accumulated history +
   agent context into knowledge afterwards; users restart and re-feed.

User decision (2026-09-13, three options narrowed):
1. **Hide ambient corpus via read gating — no deletion.**
2. **Session build-from-history as a chat tool verb** (`knowledge_build_from_context`.
3. Wave priority: these three first; live-blocked gaps stay parked.

## Decision (normative)

### D-1 — Three visibility families, one tier column per plane (migration 0092)

`knowledge_nodes.visibility` and `sat_knowledge_objects.visual_visibility`
(both catalog-grade planes) gain values::

- `project` — owned-by-project rows; visible to the owning project's callers
  and nowhere else.
- `system` — platform/system-wide knowledge; visible alongside `project`
  rows to every authenticated caller regardless of project (named, sparse,
  admin-marked — never defaulted on ingest).
- `ambient` — hidden from all read lanes except an explicit admin lane;
  used for seeded/demo corpora and orphaned/discontinued-project rows.
  Never deleted (user decision), never served to regular users.

Every row minted by ingest/upload defaults to `project` — no silent ambient
creation from writes. Ambient marking is an operator action (audit → mark,
see `tools/mark_ambient_corpus.py`; `--apply` requires explicit project-id
list, never unscoped).

### D-2 — Read gating at lanes, not at schema read time

The migration never rewrites visibility of existing rows — everything
retains default `project` until marked. Lanes apply:

```
visible_rows = rows where
    (project_id = caller_project AND visibility IN ('project','system'))
    OR visibility = 'system'
```

and ambient rows are always excluded without admin scope (so even a
non-owner's 'ambient' corpus can not reach their surfaces).

Imported knowledge stays visible under the family rule: rows materialized
by bundle import into the caller's project keep tier 'project' AND carry
adopt import provenance, so "exported from elsewhere, imported here" is
both visible AND provable.

### D-3 — Session build-from-history: verbatim provenance only

Chat-side tool `knowledge_build_from_context` (r4 companion):

- Requires acquisition consent BEFORE any write: mode must be 'open'
  or a matching 'pinned' graph; 'disabled' refuses loudly like every
  write surface (gate shared, no new semantics).
- Packages the SELECTED turns/slices as verbatim session-history artifacts
  (JSONL: session_id, range, verbatim message text, compaction metadata)
  and uploads via the already-gated central durable ingest chain. No
  LLM-synthesized "knowledge claims": context content never silently
  mutates what the user actually said.
- Returned artifact ids provenance: `{"lane": "session-history",
  "session_id": ..., "built_by": "user-initiated"}` — sessions -> lineage.
- The agent path is additive at build_chat-tool level in tools.py; nothing
  enters the write path if the acquisition mode refuses.

## Consequences

- Ambient corpus stops surfacing for ordinary users with ZERO data
  deleted and fully reversible semantics.
- Visibility of imported/shared knowledge becomes a named, provable
  contract instead of a side-effect.
- A user can genuinely chat-without-knowledge-capability first, then
  materialize the session afterwards — without new agent subtleties,
  without re-feeding, and without fabricated "extraction".
- Later waves may add: ambient surfacing as admin lane, imported rows
  showing `imported_from` chips in facade responses, session-history
  template extraction via loop review (ONLY after live usage shows the
  shape).

## Rollout-status (2026-09-13)

- **D-1/D-2/D-3 design ratified**; wave 1 executing now per user consent.
  Progress lands in the same-day AGENTS ledger and the WP-22 package row
  in the gap register (if and when the ledger rows carry a package id).

## Rollout-status (2026-09-13, Wave 1 landed)

- **D-1**: migration `0092_visibility_tiers.sql` — both planes carry the
  tier (project|system|ambient) with default 'project' and the check
  CHECK constraints; indexes added for the lane predicates.
- **D-2**: read lanes (K3 rag + knowledge_graph backends, catalog store
  search) apply the family rule — project rows + system rows serve,
  ambient rows are never served (kept, never deleted — user decision).
  `tools/mark_ambient_corpus.py`: audit → explicit-list marks, no unscoped
  change (pins incl. lane-shape + migration-shape);
  `tests/knowledge/test_visibility_tiering.py` (5).
- **D-3**: companion tool `knowledge_build_from_context` landed
  (`r4/companion/knowledge_tools.py`): verbatim session-history JSONL into
  the SAME acquisition-gated central upload; refuses disabled/pinned
  mismatch/no-project/empty/oversize; provenance lane=session-history +
  built_by=user-initiated; 7 pins + allowed-tools registered in
  research_loop skill.
- *2026-10-24 note (review of knowledge K1 part B; K-1 accepted the same
  day):* the tool's upload is marked as the agent's write (`agent_mutation`,
  `built_by: agent`, optional `proposal_id`): the excerpt is model-typed, so
  it needs an approved proposal and lands proposed. A verbatim keep from the
  session store is the user's `/save` (`session_turns`, knowledge plan
  K1.6).
- Silent-heal: `tests/test_api_catalog_store.py` sys.modules shadow
  loading poisoned catalog routes in full-suite composition (real series
  directory repairs c/w suite 4869→4869 once the visibility wave ran).
- Register rows P1-C7 added; validators green. Register remains 51 gaps,
  21 packages, WP-22 work logged outside the gap board (directive wave).

## Design review (2026-09-13) + Wave 2 rollout

Review of Wave 1 against live Postgres (release plan
R-02) and the
same-day Wave 2 landings (release track wave, repo-side; live converge is a
maintainer step).

**Findings and what landed:**

- **Catalog column defect.** Wave 1's `CatalogMetaStore.search`
  (`umrs/src/umrs/data_catalog/pg_store.py`) filtered on `visibility` but
  reads `catalog_objects` (migration 0054), which 0092 never altered — the
  column existed only on `knowledge_nodes` and `sat_knowledge_objects`. On
  the CI database the lane failed with `column "visibility" does not exist`;
  the `integration`-marked catalog test was skipped so no pin caught it.
  Fix: migration `0093_catalog_objects_visibility.sql` adds the tier column
  to `catalog_objects` (landed 2026-09-13, release track wave).
- **One predicate, not four.** The family rule (D-2) is now a single tier
  predicate in `umrs.knowledge.access_scope`, consumed by the rag, graph,
  embeddings and catalog lanes (landed 2026-09-13, release track wave). The
  per-lane `getsource` pins of Wave 1 are replaced by behaviour tests.
- **Embeddings lane gated.** The semantic/embeddings lane applies the same
  predicate (Wave 1 named only rag/graph/catalog).
- **Unmark path.** `umrs/tools/mark_ambient_corpus.py` gains
  `--mark-project` (return rows to `project`) and covers the catalog plane,
  so ambient marking is reversible as D-1 promised (landed 2026-09-13,
  release track wave). The Wave 1 text named `--mark-ambient`/`--mark-system`
  only.
- **Tool classification.** `knowledge_build_from_context` is classified
  RISKY in the companion permission ladder (never auto-approved in chat
  mode; landed 2026-09-13, release track wave).

**OPEN decisions (not decided by this block; owner: maintainer):**

1. **`system` tier vs grants.** A `system` row is visible to every
   authenticated caller regardless of project — a parallel cross-project
   path beside asset grants (ADR-041). Recommendation: make `system` grant-
   backed (a `system` grant row, so audit/tombstone semantics are shared) or
   rename it to make clear it is an operator broadcast, not a share.
2. **`ambient` as a lifecycle projection + admin lane.** Ambient should be
   derivable from project lifecycle (discontinued/orphaned) rather than a
   hand-marked tier, and needs the admin read lane D-1 promised before an
   operator can review what is hidden.
3. **Group mapping.** No authority for `group:<id>` membership exists
   (ADR-035 grammar; ADR-041 open item). Visibility cannot reference groups
   until one does.
4. **Session-history build inputs.** The verb should read turns by id
   through the server-recognised `session-history` lane, and exclude
   tool-result messages from the verbatim slice by default (they carry
   fetched content, not what the user said).

**Status stays Proposed** (moved closer by wave 2.1; Accepted requires the
remaining items below recorded with dates).

## Decision ratification (owner answers, 2026-09-13)

1. **`system` tier stays URL-key broadcast** (no grant-backed change): the
   lane reads system rows for every authenticated caller, and that is the
   explicitly recorded shape of the tier; assets grants stay their own
   project/user surface (ADR-041). Recorded as the owner's picked option,
   not a default.
2. **`ambient` becomes lifecycle-informed** (implementing now):
   `--lifecycle-report` lists projects with rows whose default graph is
   missing (advisory only) and `--mark-ambient-lifecycle` marks those
   projects ambient after affirmative confirmation (`type 'ambient'`).
   **The admin read lane is landed**: `include_ambient=true` drops the
   ambient exclusion per lane only when the verified caller identity is
   ADMIN — no other ambient visibility exists.
3. **Group mapping grounds first** (deferred): `group:<id>` visibility needs
   the groups/membership authority from ADR-035/041 queues first; noting in
   this wave as an explicit defers-not-decides record.
4. **Session-history inputs**: turn-id accessor deferred to its own wave
   (tool runtime accepts only kwargs); tool-result exclusion landed
   (included by default via include_tool_results=false).

Status remains **Proposed per wave-state**; catalog integration test row
runs DB-backed in CI through 0092/0093 (green since wave-2 rework).

## Wave 3 (2026-09-13, follow-up on note 4)

- **Turn-ids**: the session-history lane needs a server-side accessor
  reading turns by id — deferred as its own wave (the tool runtime today
  accepts only kwargs; any shim would be a fake session view).
- **Tool-result exclusion (Landed)**: role-marked JSONL excerpts filter
  tool/tool_result/function lines by default; `include_tool_results: true`
  keeps them; empty-after-filter is the named error
  `nothing_after_tool_exclusion` (r4 `a8bbc017`; 5 pins).


## Amendment A (2026-09-16): §3 group mapping UN-deferred — authority landed

The §3 record said `group:<id>` needed the groups+membership authority
before any mapping. The authority now exists:

- **Migration 0097**: `groups` (owner-attributed, tombstone-on-retire,
  never DELETE) + `group_members` (presence-only; membership is the current
  fact). Retired groups stop resolving for group-aware grant lookups the
  moment the flag flips — checked member-aware querylanes see
  `status='active'` only.
- **`/umrs/groups` routes** (lane BETA, `/gw/groups/` edge family, matrix row,
  canary, `groups.*` taxonomy): ensure-shape create (creator is the first
  member), mine-list crossing owner+membership, owner-or-admin membership
  ops, retire = owner-or-admin tombstone, growth of a retired group 409s.
- **Grant ladders resolve groups**:
  - Asset grants: `GRANTEE_KINDS += "group"`; create validates live-group
    existence + activity (dangling `group:` must not accrete); `mine`
    unions the caller's ACTIVE group memberships.
  - Source grants: `grantee` column accepts the `group:<id>` grammar
    (prefix = kind, no schema change); create validates live group;
    for-me expands the caller's active membership strings.
- **En-route repairs (same wave)**: the projects·groups store factories
  leaked one pool conn per route call (the 2026-09-09 exhaustion class) —
  both became FastAPI generator dependencies that always return the conn;
  ruff B008 (FastAPI `Depends` idiom) captured into umrs per-project
  ignores mirroring the parent workspace; dup mount of `/umrs/models`
  removed on the side (separate quota-wave commit).
- 17 pins across 5 test files; suites umrs 4977, belt 1303, contracts 179,
  route-inventory 346 rows with zero unknown rows.

Out of scope, honestly named: `session:` qualification stays
companion-trust-only. ~~r4 groups CLI verb next wave~~ LANDED same wave:
`r4 group` (create/list/show/retire/members/member-add/member-remove) +
`r4 quota`, client accessors mirroring the grants shape, `--to-group` on
`r4 grant create`; 9 pins, r4 3194/0.
