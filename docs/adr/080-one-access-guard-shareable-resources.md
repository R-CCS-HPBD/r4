# ADR-080: One access guard for shareable resources — user/group/project/system lists on every family

**Status:** Accepted (2026-10-03, maintainer request; design choices by the agent under maintainer delegation)
**Date:** 2026-10-03
**Relates to:** ADR-078 (endpoint access scopes — the precedent this generalizes), ADR-041 (one scope grammar + grants), ADR-050 §3 (groups authority, migration 0097), ADR-074 D-8 (workflow owner-scoped resolution), ADR-079 (personal buckets), gap row **D-59** (new)

## Context

Verified 2026-10-02/03 against the tree:

- **Endpoints** already share per `user:/project:/group:/system` lists (ADR-078, live).
- **Knowledge graphs / scientific catalogs / RAG corpora / workflows / buckets** did not:
  - graphs: three-tier ladder only (system/project/user); `group:` unreachable in the read path (gap: `scope_readable` takes no groups).
  - scientific catalogs: project-only, and `GET /{id}` + `GET /versions/list` had **no scope check at all** — any verified caller read any project's catalog rows (a cross-project read leak, verified in a route test).
  - RAG: project namespaces only.
  - workflows: own or `system` scope only; ADR-041 grant rows for kind `workflow` were creatable but **never consulted** at run resolution (STATE.md open row).
  - buckets: exactly one owner; no sharing path.
- The grammar (ADR-041), the groups authority (0097: platform-wide groups, presence-membership, `status='active'` resolution) and the accepted row-carried list pattern (ADR-078) already exist. Group grants in `asset_grants` are creatable in Python but never authorized anything; `resource_shares` (governance) has zero callers.

**Maintainer ask (2026-10-02):** verify the accessibility guard across endpoint / knowledge-graph / science catalogue / RAG / UMRS workflow / bucket (+ UMRS dbs) with user/group/system-wide sharing; each element may name multiple users AND multiple groups; a user may hold multiple group memberships.

## Decision

**D-1 — One guard, one module.** `umrs/shared/access_scopes.py` holds the policy for every shareable family: parse a stored list (`user:<id> | project:<id> | group:<id> | system`, ≤16 entries; malformed entries grant nothing), validate a requested list (dedupe, cap, `system` admin-only), and admit a `ResourceCaller` (verified identity + lazily resolved, memoized groups). `umrs/orchestrator/endpoint_access.py` (ADR-078) now delegates to it — behavior pinned unchanged by its 28-test suite.

**D-2 — Row-carried lists everywhere; semantics = ADR-078's.** The list governs **use** (reads and runs): graph/catalog content, workflow run/describe/package, RAG query/documents/search/stats, bucket object read+write, endpoint routing (existing). Lifecycle writes (patch/retire/delete/upload/re-scope) stay on each family's existing ladder. Absent or `[]` = the historical posture exactly (fail-closed). Multiplicity: one list names several users, several groups, several projects; one caller's many group memberships each count. Group membership resolves through the single 0097 authority — lazily at route level, in SQL at store level (buckets / RAG corpus), via an injectable resolver in the workflow catalog; retired groups never resolve.

**D-3 — Storage.** Endpoints keep `metadata["access"]`; workflows keep `access` on catalog JSON entries (survives re-upload); knowledge graphs, scientific catalogs and `user_buckets` gain `access JSONB NOT NULL DEFAULT '[]'` (migration 0116); RAG corpora — which have no durable row — get the `rag_corpus_access` registry keyed by corpus project.

**D-4 — Surface.** Every family: `PUT …/access` (write-tier owner or admin; `system` entries admin-only; audited `<family>.access_changed`) + `GET …/access` (any caller who may use the resource). List routes include access-admitted rows (graphs, catalogs, workflows marked `shared: true`, buckets `kind="shared"`). r4 client methods + `r4 knowledge graph-access`, `r4 catalog access`, `r4 workflow access`, `r4 bucket access`, `r4 rag corpus-access` mirror `r4 endpoint access`.

**D-5 — Defect fixes folded in.** (a) The scientific-catalog cross-project read leak is closed (404, no disclosure), pinned by tests. (b) The RAG / bucket / workflow sharing holes close on the same guard instead of three new mechanisms. (c) `step_staging.bucket_owned_by` — the one rule the files API and the HPC staging lane share — admits access-listed buckets, so a colleague's run can stage a shared bucket (ADR-074 D-8 amended to that extent).

**D-6 — ADR-041 grants remain for models only.** Grants (version pins, tombstones) keep their one wired consumer (model read seam). Grant rows for `knowledge_graph`/`endpoint`/`workflow` kinds were never consulted — the row-carried list is the sharing mechanism for the six families named here; removing those dead kinds is a later cleanup, not this wave. No third mechanism is introduced; `resource_shares` stays inert legacy.

### Why

- One grammar, one guard, one groups authority — no new dialect (ADR-041's rule), no per-family re-invention (the drift ADR-078 already paid down for endpoints).
- Row properties survive id churn (ADR-078's reasoning — endpoint relay handovers proved it); id-keyed grants would not, and their read path was never built.
- Project-scoped external storage bindings (ADR-077, the "UMRS dbs" of the ask) deliberately stay **project-locked**: a binding carries credentials and is never shareable by a scope list — that would hand another project's storage credentials across (constitution §2).

## Consequences

- Positive: every named family shares with multi-user/multi-group granularity on one mental model; colleagues run each other's workflows against shared buckets and query shared corpora without moving projects.
- Positive: the catalog read leak is closed; `PUT /access` changes are audited everywhere.
- Cost: shared rows appear in other callers' lists (flagged, never silent); group lookups add one lazy query where group scopes decide.
- NOT in scope: per-scope quotas; ADR-041 grant-row removal for dead kinds; provisioned (site-config) buckets; sharing external storage bindings (refused by design, D-6); expired/time-boxed access entries.

## Alternatives Considered

- **Wire ADR-041 grants as the read path everywhere** — the ratified-but-never-built route: needs a DB migration to admit `group` grantees (0083 CHECK), a `grant_authorizes` rewrite, new corpus identity rows for RAG, and still grants reads only (bucket object writes / workflow runs fall outside). Rejected: two mechanisms doing overlapping jobs; the row list was already accepted for endpoints.
- **Per-family sharing flags** (the pre-ADR-078 status quo) — five dialects again; the exact drift the scope grammar exists to prevent.

## Rollout-status (2026-10-03)

- D-1…D-6 landed repo-side: `umrs/shared/access_scopes.py`, `shared/group_membership.py`, migration `0116_resource_access.sql`; endpoint_access refactored to delegate; guards + `PUT/GET …/access` on graphs, scientific catalogs (+ leak fix), workflows (incl. run resolution, list `shared` marks, upload preservation), buckets (+ `step_staging`/files rule, list `kind="shared"`), RAG (`rag_corpus_access` + `_require_rag_scope` read fallback + `/umrs/rag/corpus-access`); `api/_access.py` glue; r4 client (10 methods) + five CLI verbs + user-manual CLI reference. Tests: umrs suites `test_access_scopes` (15), `test_knowledge_graph_access` (14), `test_scientific_catalog_access` (12), `test_rag_corpus_access` (10), `test_bucket_access` (11 new + ADR-079 suite), `test_workflow_access` (15), endpoint suite unchanged green (28); r4 `test_resource_access_cli` (11). Gates: `ci-lint-local` ALL CHECKS PASSED.
- **Deploy:** migration 0116 applies with the next per-cluster converge (owner).

### Addendum (2026-10-06) — workflow list: admins scoped by default; `all_users` is the explicit audit view

The 2026-10-04 ZMQ follow-up scoped `LIST_WORKFLOWS` to the bound caller but let an admin session's payload through untouched — an empty payload then returned the **entire catalog** (every owner's rows, access lists included), while the HTTP route scoped admins like everyone else. Maintainer-reported symptom: a full cross-project workflow dump from `r4 workflow list`. Fixed: `_scoped_list_payload` scopes every session; the full-catalog audit view is now the explicit `all_users: true` payload key, honoured **only** when the session is admin (stripped otherwise — never a privilege a payload can assert), mirrored as `GET /umrs/workflows?all_users=true` (verified-admin only) and `r4 workflow list --all`. Non-admins see no change. Remaining design fact: catalog entries are **owner-keyed, not project-keyed** — a caller's own workflows from every project still list together; per-project isolation of the catalog is not part of this guard (would be a new catalog field + ADR).

## Related

- [ADR-078](078-endpoint-access-scopes.md), [ADR-041](041-asset-grants-scope-grammar.md), [ADR-050](050-visibility-and-session-history-build.md), [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-077](077-per-project-external-storage-binding.md), [ADR-079](079-researcher-self-service-data-path.md)
