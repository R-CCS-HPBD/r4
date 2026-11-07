# ADR-053: umrs import discipline — jobs never imports api; api/__init__ imports no routers; unmounted routers are deleted

**Status:** Accepted
**Date:** 2026-09-14
**Relates to:** structure-hardening-plan-2026-09-14 §D-2/D-3 · ADR-026 (retirement rows precedent) · ADR-014 (retired surface dispositions)

## Context

`umrs/api/__init__.py` eagerly imports 47 router modules; **34 of 61**
`umrs/api/*.py` modules import `umrs.jobs` or `umrs.orchestrator`; three
`umrs/jobs` modules import back into `umrs.api`:

| Back edge | Import site |
|---|---|
| `jobs/cluster_venue.py:33` | `umrs.api._verified_headers.handshake_secret` |
| `jobs/session_gc.py:80` | `..api.companion.get_trust_store` |
| `jobs/data_ingestion.py:52` | `umrs.api.catalog_store` (CatalogStoreUnavailable, get_catalog_meta_store) |

The cycle (`jobs → orchestrator → jobs` through api) forces test files to
load modules *by file path* (`sys.path.insert`, `spec_from_file_location`,
stub-parent `__path__ = []` shims) — verified across both trees:

```
tests/      23 sys.path.insert  33 spec_from_file_location
umrs/tests/ 21 sys.path.insert  23 spec_from_file_location
```

The 2026-09-13 typed-errors wave broke 19 tests in exactly this class — the
stubs' empty `__path__` breaks relative imports inside the spec-loaded
modules. The boundary validator already forbids api/server imports for
`model_registry/mcp/rag/pipeline_sdk/orchestrator/security`
(`tools/import_boundary_validator.py:109-120`); `jobs` was the missing
member, written informally in `jobs/manifest.py:12` and
`jobs/knowledge_adapters.py:195` ("must not import from umrs.api").

Twelve router modules never mount anywhere: `adaptive_planning`, `checkpoints`,
`knowledge_model`, `namespaces`, `recovery`, `routing_feedback`, `step_replay`,
`tool_cache`, `vectors`, `workflow_fork`, `workflow_outcomes`, `workload_identity`.
None has a consumer in `r4/src`, `packages/r4-tui-v3/src`, or
`vscode-extension/src`; their NGINX locations never existed in git history —
they were never production surfaces, only dev-compat via the Python gateway
(retired 2026-09-11 via ADR-014). The orphan detector cannot see them only
because their pins in `PINNED_PUBLIC_SURFACE` / `DYNAMIC_IMPORT_MODULES`
pretend otherwise (`tools/orphan_module_detector.py:91-154`).

## Decision

1. **`umrs.jobs` may not import `umrs.api`* or **`umrs.server`**. The shared
   helpers both sides use live in `umrs.shared` (which exists for this
   exact purpose); api modules re-export from `shared` and api callers are
   unchanged by construction.
2. **`umrs/api/__init__.py` imports no routers.** It carries the module
   docstring and at most a lazy `__getattr__` shim that warns for one
   release. Mounting lives only at the server's explicit registry
   (`umrs/src/umrs/server/http_sidecar.py`).
3. **Unmounted router modules are deleted, not kept warm** — together with
   their tests and the now-false orphan-detector pins — unless an Accepted
   or Proposed-with-rollout ADR names the capability as planned work, in
   which case the module mounts behind the `v1beta` lane with an
   authenticated NGINX location and a route test in this plan. The
   retirement table **below** records each module, its prefix, and the commit
   that carried the decision.
4. **Test-side hacks disappear**: every `sys.path.insert` /
   `spec_from_file_location` / `__path__ = []` in test trees is replaced with
   a plain import and forbidden from returning (a new enforcement validator,
   `hard_fail` grade).

### Why now, and why delete

Unmounted routers are the platform's largest class of dead shipping code:
they cost review attention, produce false "surface" signals in traceability,
and their detector pins are open lies. Git keeps the history; the retirement
table below records each prefix + commit.

## Retirement table (reconciled via Phases 4.1–4.2; every one landed as umrs `d9efd62a` 2026-09-15)

| Module | Prefix | ADR naming as planned work? | Decision | Commit |
|---|---|---|---|---|
| adaptive_planning | /umrs/adaptive | (none — concept mentions only) | delete | umrs `d9efd62a` |
| checkpoints | /umrs/checkpoints | (none — concept mentions only) | delete | umrs `d9efd62a` |
| knowledge_model | /umrs/knowledge-model | (none — concept mentions only) | delete | umrs `d9efd62a` |
| namespaces | /umrs/namespaces | (none — concept mentions only) | delete | umrs `d9efd62a` |
| recovery | /umrs/recovery | (none — concept mentions only) | delete | umrs `d9efd62a` |
| routing_feedback | /umrs/routing-feedback | (none — concept mentions only) | delete | umrs `d9efd62a` |
| step_replay | /umrs/workflows/steps | (none — concept mentions only) | delete | umrs `d9efd62a` |
| tool_cache | /umrs/tool-cache | (none — concept mentions only) | delete | umrs `d9efd62a` |
| vectors | /umrs/vectors | (none — concept mentions only) | delete | umrs `d9efd62a` |
| workflow_fork | /umrs/workflows/fork | (none — concept mentions only) | delete | umrs `d9efd62a` |
| workflow_outcomes | /umrs/workflows/outcomes | (none — concept mentions only) | delete | umrs `d9efd62a` |
| workload_identity | /umrs/workload-identity | (none — concept mentions only) | delete | umrs `d9efd62a` |

## Consequences

- The jobs↔api cycle is gone; tests import normally; `DORMANT_FAMILIES == set()`
  pinned in surface characterization.
- API surface = exactly what http_sidecar mounts (charter for the
  surface-characterization suite).
- The orphan detector's remaining pins now carry evidence links (file:line or
  a Dockerfile/ansible reference) or they do not exist.
- NOT in scope: removing the `__getattr__` shim immediately (one-release
  deprecation, recorded as follow-up in the rollout).

## Alternatives Considered

- **Keep the 12 as optional v1beta mounts** — rejected: no product evidence;
  every mount is a lane-owned surface.
- **Keep pins as documentation** — rejected: `PINNED_PUBLIC_SURFACE` rows are
  truth-hiding by construction; the detector is a truth surface.

## Rollout-status (2026-09-14)

- Proposed; execution per structure-hardening-plan-2026-09-14.md §6 (Phases
  0, 1, 4).
- Phase 1 execution (same day, branch `structure/hardening-2026-09`):
  - (1.1–1.3) landed earlier in the day: back-edges moved into
    `umrs.shared.*`, `umrs.api.__init__` emptied to a lazy `__getattr__`
    shim, `umrs.jobs ↛ umrs.api/server` added to
    `tools/import_boundary_validator.py`.
  - (1.4) test-machinery sweep landed: every `sys.path` splice and
    spec-load harness under `tests/` (15 files), `umrs/tests` (24 files,
    umrs `d1cad708`), and `mcp-servers/tests` converted to plain imports;
    kept loaders (isolated foundation pins, non-packaged script targets,
    cross-repo helper routes) recorded with reasons in
    `tools/test_import_hygiene_allowlist.txt`. Two stale patch-target
    premise updates included (`umrs.server.job_tracking_handler.*` →
    `umrs.shared.pg.*`, `umrs.server.model_registry_handler._load_catalog`
    → `umrs.shared.model_catalog.load_catalog`, matching the Phase-1.1
    moves). Suites: root belt **1308 passed / 193 skipped / 0 failed**;
    umrs **4936 passed / 155 skipped / 0 failed**.
  - (1.5) `tools/test_import_hygiene_validator.py` (AST-based; 11
    allowlisted exceptions) registered **hard_fail** in the maturity
    scorer (scorecard A:22 B:0 / 22 hard / 364 tests) and wired into
    `scripts/ci-lint-local.sh` + the Boundary-Check job in
    `.github/workflows/ci.yaml`; 16 pins in
    `tests/test_test_import_hygiene_validator.py`.
- Phase 4 execution (D-3; same week, umrs `d9efd62a` pushed + parent
  `bb144175`, branch still `structure/hardening-2026-09`):
  - (4.1) twelve-row disposition table — every ADR mention of the
    module names audited; all are concept/context level (per-iteration
    checkpoints in ADR-036 are internal to `jobs.checkpoint_store`,
    `user:<uid>` namespaces in ADR-027/041 mean scope tuples, ADR-024's
    `umrs/knowledge_model` is an internal package, ADR-035's review
    debt is not a planned mount). Decision: **delete all twelve**.
  - (4.2) modules deleted; two external consumers rehomed
    byte-identical (`api/loop.py` owns its checkpoint accessor;
    `IssueRequest` wire model moved to `jobs/workload_identity.py`
    because one test imported the shape); `test_tool_cache_binding.py`
    deleted; `test_ttl_clamp` tool-cache pin trimmed;
    `DORMANT_FAMILIES == set()` pinned. Umrs suite 4969/163-skip green.
  - (4.3) detector reconciliations: orphan-detector pins for the
    retired eight + the stale `knowledge_model` "pending surface" row
    removed (0 candidates post-prune); r4 audit-list rows pruned;
    NGINX untouched (no mount:v1beta rows); retirement table rows cite
    the landed SHA.
  - (4.4 follow-up, umrs `a7612d97`): `DORMANT_FAMILIES` did not empty
    completely — `api/context.py` is the one surviving unmounted router,
    kept deliberately as the unfinished `/umrs/context` capability
    (release plan esc #3; closed for the other four members). The set
    is pinned at most `{"context"}` and the characterization guard
    still bites on any accidental mount. Un-routers helpers
    (`llm_usage`, `model_visibility`, `rag_service`, `scopes`,
    `context_schema`, `agent_quota`, `disk_ledger`, `workflow_auth`,
    `vision_durable`, `assistant_service`) are NOT routers — verified
    no APIRouter — internal helpers consumed by mounted code; no
    disposition needed.

## Related

- [ADR-052](052-one-gate-every-tree.md)
- [ADR-054](054-workspace-membership-and-test-tiers.md)
- structure-hardening-plan-2026-09-14.md
