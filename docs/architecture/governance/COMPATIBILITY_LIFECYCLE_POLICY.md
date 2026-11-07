# Compatibility Lifecycle Policy

*Generated: 2026-05-13 — Architecture Stewardship Phase*

## Purpose

This document formalizes lifecycle governance for all transitional architecture
components: compatibility shims, bridges, migration validators, convergence
scaffolding, fallback logic, and transitional re-exports.

---

## Lifecycle Classifications

| Class | Definition | Retirement Requires |
|-------|-----------|-------------------|
| **PERMANENT** | Architectural invariant; never retires | Architecture rewrite (not planned) |
| **RELEASE-BOUND** | Retires when a specific version ships | Version gate + consumer migration |
| **MIGRATION-ONLY** | Retires when migration step completes | Step completion + test pass |
| **ARCHIVAL** | Dead but preserved for reference | Confirm zero consumers + delete |

---

## Complete Inventory

### PERMANENT Components (Never Retire)

| # | Component | Location | Purpose | Protected By |
|---|-----------|----------|---------|-------------|
| 1 | **DurableJobRuntime bridge** | `umrs/server/durable_job_runtime.py` | Gen1↔Gen2 coexistence | Mutation safety rule |
| 2 | **ZMQ→durable handler** | `umrs/server/job_tracking_handler.py` | ZMQ transport → durable jobs | ZMQ permanence |
| 3 | **Skills DTO re-export** | `r4-contracts/src/r4_contracts/skills.py` | Cross-package type sharing | Import boundary validator |
| 4 | **Recipes DTO re-export** | `r4-contracts/src/r4_contracts/recipes.py` | Cross-package type sharing | Import boundary validator |
| 5 | **Security DTO re-export** | `r4-contracts/src/r4_contracts/security.py` | Cross-package type sharing | Import boundary validator |
| 6 | **OrchestratorBackend protocol** | `umrs/jobs/scheduler.py` | Backend abstraction | Active integration point |
| 7 | **MessageQueue transport** | `umrs/core/MessageQueue.py` | ZMQ data/control planes | Mutation safety rule |
| 8 | **r4-contracts zero-dep fence** | `r4-contracts/pyproject.toml` | Dependency isolation | contracts_fence validator |

**Policy**: These components are NEVER candidates for removal. Any proposal
to retire them requires ADR amendment + architecture review.

---

### RELEASE-BOUND Components

| # | Component | Location | Retirement Gate | Min Stabilization |
|---|-----------|----------|-----------------|-------------------|
| 1 | **Legacy endpoint aliases** | `umrs/api/endpoints.py` | All CLI consumers > v1.2 | 2 release cycles after v1.2 ships |
| 2 | **`stale_name_validator`** | `tools/stale_name_validator.py` | 3 months after last `aisci` reference removed | 6 months (reintroduction risk) |

**Policy**: Release-bound components can only be retired after:
1. The gate version has been released
2. The minimum stabilization window has passed
3. Zero consumers detected in production
4. PR-based removal with full validator pass

---

### MIGRATION-ONLY Components

| # | Component | Location | Retirement Gate | Prerequisite |
|---|-----------|----------|-----------------|-------------|
| 1 | **Skills DTO mirror** | `umrs/src/umrs/skills/schema.py` | Step 5 convergence | UMRS imports from r4-contracts |
| 2 | **Recipes DTO mirror** | `umrs/src/umrs/recipes/schema.py` | Step 5 convergence | UMRS imports from r4-contracts |
| 3 | **Security DTO mirror** | `umrs/src/umrs/security/models.py` | Step 5 convergence | UMRS imports from r4-contracts |
| 4 | **MCP DTO mirror** | `umrs/src/umrs/mcp/models.py` | Step 5 convergence | UMRS imports from r4-contracts |
| 5 | **`dto_equivalence_check.py`** | `tools/dto_equivalence_check.py` | Same as items 1-4 | Mirror files removed |
| 6 | **DTO equivalence tests** | `tests/test_dto_equivalence.py` | Same as item 5 | Validator removed |

**Policy**: Migration-only components retire in the SAME PR as their prerequisite:
1. Execute the migration step
2. Remove the transitional component
3. Remove the associated validator/tests
4. Full test pass confirms no regression

**Step 5 Convergence PR sequence:**
```
1. Add r4-contracts as UMRS dependency
2. Update UMRS imports: from r4_contracts.skills import Skill (etc.)
3. Delete mirror files (items 1-4)
4. Delete dto_equivalence_check.py (item 5)
5. Delete test_dto_equivalence.py (item 6)
6. Update BRIDGE_REDUCTION_ANALYSIS.md
7. Run all validators + tests
```

---

### ARCHIVAL Components (Dead, Awaiting Removal)

| # | Component | Location | Evidence | Status |
|---|-----------|----------|----------|--------|
| 1 | ~~`debug_502.py`~~ | ~~`umrs/mini-rivault-llm/`~~ | Zero imports | **REMOVED 2026-05-13** |
| 2 | ~~`debug_bentoml_direct.py`~~ | ~~`umrs/mini-rivault-llm/`~~ | Zero imports | **REMOVED 2026-05-13** |
| 3 | ~~`mq_zeroMQ.py`~~ | ~~`umrs/core/`~~ | Superseded by MessageQueue.py | **REMOVED 2026-05-13** |

**Policy**: Archival components are removed immediately upon identification.
No stabilization window required (they have zero consumers by definition).

---

## Retirement Readiness Matrix

| Component | Ready? | Blocker | Estimated Effort |
|-----------|--------|---------|-----------------|
| Legacy endpoint aliases | ❌ Not yet | CLI v1.2 not shipped | Low (alias removal) |
| stale_name_validator | ❌ Not yet | Stabilization window not elapsed | Trivial (delete tool) |
| Skills/Recipes/Security mirrors | ❌ Not yet | Step 5 not executed | Medium (import migration) |
| MCP DTO mirror | ❌ Not yet | Step 5 not executed | Low (simple import change) |
| dto_equivalence_check | ❌ Not yet | Depends on mirrors above | Trivial (delete tool) |
| DurableJobRuntime bridge | ❌ NEVER | Permanent architecture | N/A |
| ZMQ handler | ❌ NEVER | Permanent architecture | N/A |
| DTO re-exports | ❌ NEVER | Permanent architecture | N/A |

---

## Stabilization Timeline

```
NOW (2026-05-13)
  │
  │  ← Archival items removed (DONE)
  │
  ├── [Future: Step 5 PR]
  │     ├── UMRS imports from r4-contracts
  │     ├── Mirror files deleted
  │     ├── dto_equivalence_check retired
  │     └── Bridge entries updated
  │
  ├── [Future: CLI v1.2 ships]
  │     │
  │     └── [+2 releases: Legacy aliases removable]
  │
  └── [Future: 6 months post-aisci-clean]
        └── stale_name_validator optionally retirable
            (recommend keeping — zero maintenance cost)
```

---

## Compatibility Governance Rules

### Rule 1: No Silent Removal

Every compatibility component removal MUST:
- Be a standalone PR (no mixed feature work)
- Include validator pass evidence
- Include test pass evidence
- Document rollback procedure
- Reference this document's retirement gate

### Rule 2: Minimum Stabilization Windows

| Component Class | Minimum Window |
|----------------|---------------|
| PERMANENT | ∞ (never remove) |
| RELEASE-BOUND | 2 release cycles after gate |
| MIGRATION-ONLY | 0 (same PR as migration) |
| ARCHIVAL | 0 (remove immediately) |

### Rule 3: Observability Before Retirement

Before retiring any bridge or shim:
1. Check `umrs_bridge_events_total` for the bridge — must show zero activity for 30 days
2. Check `umrs_bridge_last_activity_timestamp` — must be stale
3. If metrics not available for that bridge, add instrumentation FIRST

Exception: ARCHIVAL and MIGRATION-ONLY components (provably dead or replaced in same PR).

### Rule 4: Rollback Requirements

| Component Class | Rollback Method | Time to Recover |
|----------------|-----------------|-----------------|
| PERMANENT | N/A (never removed) | N/A |
| RELEASE-BOUND | `git revert` | Minutes |
| MIGRATION-ONLY | Revert entire Step PR | Minutes |
| ARCHIVAL | `git revert` (but should never need to) | Minutes |

### Rule 5: Fallback Logic Governance

Any component with fallback behavior (try X, if fails do Y) must:
- Log when fallback activates (WARNING level minimum)
- Emit a metric on fallback activation
- Document the fallback path in this file
- Have a test that exercises both primary and fallback paths

Current fallback patterns:

| Component | Primary | Fallback | Logged? | Metric? |
|-----------|---------|----------|---------|---------|
| `prometheus_client` import | Prometheus metrics | No-op stubs | No | N/A (no-op) |
| `torch` import | CUDA device | CPU device | Yes (INFO) | No |
| Embedding model load | Load from path | Deferred (first-request) | Yes | No |
| ZMQ send | Immediate send | Timeout → exception | Yes | ✅ `transport_errors` |
| Postgres connect | Direct connect | Retry with backoff | Yes | No |

---

## Compatibility Shim Inventory (Active)

### Bridge Review Cadence

| Review Type | Frequency | Scope | Owner |
|-------------|-----------|-------|-------|
| Bridge activity check | Monthly | All bridges with metrics | Platform team |
| Compatibility surface audit | Quarterly | All PERMANENT components | Architecture owner |
| Retirement readiness sweep | Per release | RELEASE-BOUND components | Release manager |
| Migration progress check | Bi-weekly (during active migration) | MIGRATION-ONLY components | Migration owner |

### Review Checklist (Monthly Bridge Activity)

For each instrumented bridge:
1. Check `umrs_bridge_events_total{bridge="<name>"}` — any activity in 30 days?
2. Check `umrs_bridge_last_activity_timestamp{bridge="<name>"}` — stale?
3. If zero activity AND bridge is NOT classified PERMANENT → flag for retirement review
4. If activity detected on PERMANENT bridge → confirm normal (expected behavior)
5. Document findings in monthly ops review

---

### Observability Prerequisites for Retirement

Before ANY component can be retired, the following observability must be in place:

| Prerequisite | Required For | Evidence |
|-------------|-------------|----------|
| Activity counter metric | All bridges, shims | `*_events_total` counter exists and is scraped |
| Last-activity timestamp | All bridges, shims | `*_last_activity_timestamp` gauge exists |
| 30-day zero-activity window | RELEASE-BOUND retirement | Prometheus query confirms 30 days of zero |
| Consumer enumeration | DTO re-exports | Import boundary validator confirms zero external consumers |
| Error rate baseline | All runtime components | Pre-retirement error rate documented for comparison |
| Rollback test | RELEASE-BOUND | Revert PR prepared and tested (not merged) |

**Hard rule**: If a component lacks activity metrics, you MUST add instrumentation
BEFORE initiating retirement. The instrumentation must be in production for at
least 30 days before retirement can proceed.

---

### Permanent Compatibility Surface Classification

The following components are classified as **permanent compatibility surfaces** —
they serve ongoing interoperability needs and will NEVER be retirement candidates:

| Surface | Purpose | Why Permanent |
|---------|---------|---------------|
| ZMQ control/data transport | Client ↔ Server communication | Architectural invariant (ADR-001) |
| DurableJobRuntime bridge | Gen1↔Gen2 thread coexistence | Load-bearing runtime infrastructure |
| ZMQ→durable handler | Transport → persistence adaptation | Bridges permanent transport to permanent storage |
| r4-contracts DTO re-exports | Cross-package type sharing | Import boundary enforcement requires them |
| OrchestratorBackend protocol | Backend abstraction interface | Enables multi-target execution |
| MessageQueue socket abstraction | ZMQ socket lifecycle | Encapsulates transport complexity |
| SiteConfig env overlay | Environment-driven configuration | Deployment determinism requirement |
| Advisory lock mechanism | Job scheduling exclusivity | Correctness invariant for distributed scheduling |

**Governance**: Removing any permanent compatibility surface requires:
1. New ADR documenting the rationale
2. Architecture review approval
3. Migration plan with zero-downtime guarantee
4. 90-day deprecation notice
5. All consumers migrated before removal

---

### Rollback Expectations

| Component Class | Rollback Method | Max Recovery Time | Data Loss Risk |
|----------------|-----------------|-------------------|---------------|
| PERMANENT | N/A (never removed) | N/A | N/A |
| RELEASE-BOUND | `git revert` of removal PR | < 5 minutes (CI/CD) | None (Postgres state unchanged) |
| MIGRATION-ONLY | Revert entire Step PR | < 5 minutes (CI/CD) | None (revert restores mirrors) |
| ARCHIVAL | `git revert` (rarely needed) | < 5 minutes | None |

**Rollback invariants**:
- All removal PRs must be independently revertable
- Reverted state must pass all validators
- No manual data migration required for rollback
- Rollback never requires Postgres schema changes

---

### Retirement Evidence Thresholds

| Evidence Type | Threshold | Measurement |
|--------------|-----------|-------------|
| Zero activity | 30 consecutive days | Prometheus `rate()` query |
| Zero consumers | All scan roots clear | Import boundary validator |
| Zero config references | All deployment configs | Config drift validator |
| Zero test dependencies | Test suite passes without module | `pytest --ignore=<module>` |
| Owner confirmation | Explicit written approval | PR comment or issue |
| Version gate passed | Target version released | Release tag exists |
| Stabilization elapsed | Calendar time since gate | Date comparison |

**Combined threshold for retirement PR approval**:
- ALL evidence types applicable to the component's class must be satisfied
- Evidence must be documented in the retirement PR description
- Reviewer must verify evidence independently (not trust submitter alone)

---

### Required Operational Metrics Before Deprecation Decisions

No deprecation decision may be made without the following metrics being available
AND collected for at least 30 days:

| Metric | Purpose | Query Example |
|--------|---------|---------------|
| `umrs_bridge_events_total` | Usage frequency | `sum(rate(umrs_bridge_events_total{bridge="X"}[30d]))` |
| `umrs_bridge_last_activity_timestamp` | Staleness | `time() - umrs_bridge_last_activity_timestamp{bridge="X"}` |
| `umrs_transport_errors_total` | Health baseline | `rate(umrs_transport_errors_total[7d])` |
| `umrs_job_attempts_total` | Workload context | `sum(umrs_job_attempts_total[30d])` |
| `umrs_reconciler_runs_total` | Recovery frequency | `sum(umrs_reconciler_runs_total{outcome="ok"}[30d])` |

**If metrics are unavailable**: The component CANNOT be deprecated. Add
instrumentation first, wait 30 days, then reassess.

---

### Package Re-exports

| Source | Re-exported In | Purpose | Permanent? |
|--------|---------------|---------|-----------|
| `umrs.skills.schema` | `r4_contracts.skills` | Cross-package skill types | ✅ Yes |
| `umrs.recipes.schema` | `r4_contracts.recipes` | Cross-package recipe types | ✅ Yes |
| `umrs.security.models` | `r4_contracts.security` | Cross-package security types | ✅ Yes |
| `r4.__init__` | N/A (self-export) | Developer convenience | ✅ Yes (advisory) |

### Protocol Bridges

| Bridge | Direction | Purpose | Permanent? |
|--------|-----------|---------|-----------|
| DurableJobRuntime | Gen1 → Gen2 | Legacy client compatibility | ✅ Yes |
| job_tracking_handler | ZMQ → Durable | Transport adaptation | ✅ Yes |

### Validator Transitional Components

| Validator | What It Guards | Retires With |
|-----------|---------------|-------------|
| `dto_equivalence_check` | Mirror consistency | Step 5 |
| `stale_name_validator` | aisci contamination | 6-month window (optional) |

---

## Decision Framework: Is It Time to Retire?

```
Is component class PERMANENT?  ──YES──►  NEVER RETIRE
        │ NO
        ▼
Is component class ARCHIVAL?  ──YES──►  REMOVE NOW
        │ NO
        ▼
Has retirement gate passed?  ──NO──►  WAIT (track in this doc)
        │ YES
        ▼
Has stabilization window elapsed?  ──NO──►  WAIT
        │ YES
        ▼
Are there zero consumers in production?  ──NO──►  WAIT
        │ YES
        ▼
Does bridge metric show 30 days inactivity?  ──NO──►  ADD METRIC FIRST
        │ YES
        ▼
RETIRE in dedicated cleanup PR
```

---

## Production Contract Stability

The following contracts are production surfaces. Breaking changes require
the full retirement lifecycle (announce → warn → soft-fail → hard-fail →
remove) with a minimum of 2 release cycles.

### Schema Contracts

| Schema | Owner | Migration Path | Stability |
|--------|-------|---------------|-----------|
| Jobs / attempts tables | UMRS | Numbered SQL migrations | **Stable** — additive only |
| Model registry tables | UMRS | Numbered SQL migrations | **Stable** — additive only |
| Context storage tables | UMRS | Numbered SQL migrations | **Stable** — additive only |
| MCP registry tables | UMRS | Numbered SQL migrations | **Stable** — additive only |
| Egress audit tables | UMRS | Numbered SQL migrations | **Stable** — additive only |
| Gateway audit tables | Gateway | Ad-hoc creation (needs migration) | **Transitional** |
| RAG per-tenant tables | UMRS | Dynamic creation | **Transitional** |

**Additive-only rule**: Stable schemas may add columns (with defaults),
add tables, and add indexes. Column removal, type changes, and table drops
require the full retirement lifecycle.

### API Contracts

| Endpoint Pattern | Owner | Stability |
|-----------------|-------|-----------|
| `/gw/llm/v1/*` | Gateway | **Stable** — OpenAI-compatible surface |
| `/gw/rag/*` | Gateway → UMRS | **Stable** |
| `/gw/mcp/*` | Gateway → UMRS | **Stable** |
| `/umrs/jobs/*` | UMRS | **Stable** |
| `/umrs/models/*` | UMRS | **Stable** |
| `/umrs/context/*` | UMRS | **Stable** |
| `/umrs/endpoints/*` | UMRS | **Stable** |
| `/health` | UMRS, Gateway | **Stable** |

### Wire Format Contracts

| Format | Owner | Stability |
|--------|-------|-----------|
| Job spec JSON (submit payload) | UMRS | **Stable** — additive |
| Endpoint registry JSON | UMRS | **Stable** — additive |
| Model registry JSON | UMRS | **Stable** — additive |
| ZMQ control frames | UMRS | **Stable** — versioned |
| ZMQ data frames | UMRS | **Stable** — versioned |
| Audit log JSON | Gateway | **Transitional** (needs schema normalization) |

### Storage Reference Contracts

| Reference | Format | Stability |
|-----------|--------|-----------|
| Job artifact path | `s3://bucket/jobs/{job_id}/attempt_{n}/…` | **Stable** |
| Upload path | `s3://bucket/uploads/{project_id}/…` | **Stable** |
| Model weight path | `{MODEL_ROOT}/{model_name}/` | **Stable** |
| Deployment profile ID | `hpc` \| `k8s` \| `dev-server` | **Stable** |

### Breaking Change Process

For any contract marked **Stable**:

1. **Announce**: Add deprecation notice in code, release notes, and this doc
2. **Dual support**: Old and new formats accepted simultaneously (minimum 2 releases)
3. **Migration tooling**: Provide automated migration for stored data (schemas, S3 paths)
4. **Warn period**: Validator emits warnings for old format usage (30 days)
5. **Hard-fail**: Validator blocks old format (60 days after announcement)
6. **Remove**: Old format support removed (next release after hard-fail)

For contracts marked **Transitional**: breaking changes are allowed with a
single-release deprecation notice and migration path.
