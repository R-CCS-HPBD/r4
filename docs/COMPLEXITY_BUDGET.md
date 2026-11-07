# Complexity Budget Ledger (2026-08-13, M10 gate)

Policy (standing, per wave instructions): complexity reduction is **not** a
goal in itself. Items below are pre-existing over-target functions (CI
complexity_audit `--max-complexity 9`). Any refactor of these MUST ship a
correctness, audit-visibility, or failure-hardening benefit. Pure
mechanical extraction (cosmetic CC reduction) is **rejected**.

## Budget conventions

| Tier | Rule |
|---|---|
| critical (>25) | Document + assign owner-class; refactor ONLY alongside a concrete correctness fix touching the same logic. |
| warning (10-25) | Prefer additive helpers for NEW branches; existing branches stay. |
| target (10-12) | Watchlist — stable unless a defect lands. |

## Current entries (verified by tools/complexity_audit.py)

### critical
- `umrs/src/umrs/api/endpoints.py:2113` — `provision_endpoint` (98).
  Multi-phase request orchestration. Fix-by-total-rewrite is rejected;
  new gates land as extracted helpers (see M10 wave: `_enforce_serving_mode_supported`,
  `_enforce_recipe_policy`, `_authorize_model_entry_for_caller`,
  `_require_identity_for_mutation`) — helper extraction reduces each new
  gate's add-cost to zero.
- `gateway/gateway/routes/endpoints.py:148` — `provision_endpoint` (38).
  Same shape as UMRS-side; C901 budget owner: gateway layer.
- `umrs/src/umrs/jobs/adapters.py:451` — `EndpointProvisionBackend.submit_job` (33).
- `umrs/src/umrs/api/endpoints.py:975` — `ProvisionRequest._validate_multi_model` (26).
  Pydantic validator (intentionally comprehensive shape checks; still
  unreadable as one block — candidate for split-by-field-group).
- `umrs/src/umrs/server/rivault_launcher.py:499` — `get_slurm_node` (26).
- `umrs/src/umrs/jobs/adapters.py:793` — `EndpointProvisionStatusChecker.check_status` (25).

### warning (10-25)
- `umrs/src/umrs/server/rivault_launcher.py:650` — `_remote_sbatch_args` (18)
- `umrs/src/umrs/server/rivault_launcher.py:1164` — `launch_rivault_model` (17)
- `umrs/src/umrs/api/endpoints.py:1592` — `get_active_endpoints` (15)
- `umrs/src/umrs/api/endpoints.py:1676` — `list_endpoints` (14)
- `umrs/src/umrs/api/endpoints.py:108` — `_authorize_model_entry_for_caller` (12, M10-new)
- `umrs/src/umrs/api/endpoints.py:1983` — `health_check` (12)
- `umrs/src/umrs/jobs/adapters.py:359` — `_readiness_progress_detail` (12)
- `umrs/src/umrs/server/rivault_launcher.py:770` — `extract_node_address` (12)
- `umrs/src/umrs/server/rivault_launcher.py:1097` — `_check_cache_or_reuse_job` (12)
- `umrs/src/umrs/api/endpoints.py:624` — `_refresh_endpoint_readiness` (11)
- `umrs/src/umrs/jobs/adapters.py:88` — `_resolve_model_entry` (11)
- `umrs/src/umrs/jobs/adapters.py:333` — `_backend_progress_detail` (11)
- `umrs/src/umrs/jobs/adapters.py:1249` — `CleanupStatusChecker.check_status` (11)
- `umrs/src/umrs/jobs/adapters.py:1364` — `create_backend_factory.factory` (11)
- `umrs/src/umrs/api/endpoints.py:2883` — `upgrade_endpoint` (10, Q-13 Phase-2-new).
  Security-boundary CLI route with project/identity/concurrency gates separated
  into discrete checks before the flip. The flip orchestration lives in
  `OrchestrationService.upgrade_endpoint`; the HTTP route itself only calls
  through with a strict-witnessed gate, so a split purely by CC is rejected.
- `umrs/src/umrs/orchestrator/service.py:978` — `_run_upgrade_flip` (11,
  Q-13 Phase-2-new). Explicit rollback-on-health-fail semantics require a
  multi-branch flow that matches the provisioning-flip-retire contract.

## Contract

Any PR that touches one of these functions MUST either:

1. raise its audit-visibility (names/guards/logging) without changing
   external behavior, or
2. fix a correctness defect there (then a partial split is justified
   when it pays off directly), or
3. leave complexity unchanged (documented in the PR description).

It MUST NOT perform a mass split purely to lower a number.

## H1 — Planned hardening (security tree, NOT complexity)

- MVP → strict-by-default identity migration: today, strict mode denies
  unresolved identity and mvp mode preserves legacy behavior with warnings.
  The AUTH surface is capped at the compatibility layer; a future
  strict-by-default rollout is tracked as an architectural security item.

## Other registered debt

- `umrs.tenants` (519 lines, zero consumers): experimental pre-wiring of
  per-project schema isolation. NOT a security boundary today (nothing
  routes through it; no enforcement semantics reachable). Keep parked;
  deletion requires arch review per mutation-safety.
