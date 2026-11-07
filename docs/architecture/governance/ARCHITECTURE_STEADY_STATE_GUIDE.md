# Architecture Steady-State Guide

*Generated: 2026-05-13 — Architecture Stewardship Phase*
*Status: CANONICAL — The platform is now in STEADY-STATE OPERATIONAL GOVERNANCE.*

## Purpose

This document defines the architectural steady state of the AI4S Platform + UMRS
stack. It serves as the authoritative reference for "do not destabilize the
platform" decisions — identifying what is stable, what is transitional, what is
intentionally duplicated, and what categories of work are safe vs forbidden.

The platform has completed all active reconstruction phases and transitioned into
operational stewardship mode. UMRS operates as a standalone runtime platform;
R4 is an optional upper-layer client. See `UMRS_CORE_RUNTIME_SPEC.md` and
`R4_CLIENT_ROLE_DEFINITION.md` for formal boundary definitions.

---

## 1. What Is Now Considered Stable

These components have been validated, tested, and declared operationally sound.
They should NOT be modified without explicit justification and full test pass.

### Runtime Infrastructure (Stable)

| Component | Location | Validation |
|-----------|----------|-----------|
| Durable Job System | `umrs/jobs/` (store, scheduler, monitor, reconciler) | 100 UMRS domain tests |
| MessageQueue (ZMQ) | `umrs/core/MessageQueue.py` | Transport metrics + integration tests |
| DurableJobRuntime bridge | `umrs/server/durable_job_runtime.py` | Bridge event metrics |
| Job tracking handler | `umrs/server/job_tracking_handler.py` | End-to-end job tests |
| Site config + profiles | `umrs/shared/loaded_config_profiles.py` | config_drift_validator |
| HTTP Sidecar | `umrs/server/http_sidecar.py` | Gateway integration tests |
| Mini-RiVault launch | `umrs/mini-rivault-llm/serve-model.slurm` | HPC deployment validation |

### Enforcement Infrastructure (Stable)

| Component | Location | Validation |
|-----------|----------|-----------|
| 9 validators | `tools/*.py` | 61 enforcement tests |
| Import boundary rules | `tools/import_boundary_validator.py` | Hard-fail in CI |
| Contracts fence | `tools/contracts_fence.py` | Zero-dependency guarantee |
| DTO equivalence check | `tools/dto_equivalence_check.py` | Cross-package consistency |
| Schema version tracker | `tools/schema_version_tracker.py` | Schema drift detection |
| ADR linkage | `tools/adr_linkage_validator.py` | Governance cross-reference |

### Package Boundaries (Stable)

| Boundary | Rule | Enforced By |
|----------|------|-------------|
| r4 → UMRS | No direct imports | import_boundary_validator |
| Gateway → UMRS | No direct imports | import_boundary_validator |
| r4-contracts | Zero dependencies | contracts_fence |
| UMRS internal layers | No upward imports (foundation → api) | UMRS internal boundary check |

---

## 2. What Remains Transitional

These components are functional but scheduled for evolution:

| Component | Current State | Target State | Trigger |
|-----------|--------------|-------------|---------|
| DTO mirror files (Skills, Recipes, Security, MCP) | Dual maintenance | Single source in r4-contracts | Step 5 convergence PR |
| `dto_equivalence_check` validator | Guards mirror consistency | Retired | After DTO mirrors removed |
| `stale_name_validator` | Guards against `aisci` references | Optionally retirable | 6-month post-clean window |
| Legacy endpoint aliases | Active compatibility | Removed | CLI v1.2 + 2 release cycles |

**Governance**: Transitional components are tracked in
`docs/COMPATIBILITY_LIFECYCLE_POLICY.md` with explicit retirement gates.

---

## 3. What Is Intentionally Duplicated

These apparent duplications are BY DESIGN and must NOT be "simplified":

| Duplication | Why It Exists | What Would Break |
|-------------|--------------|-----------------|
| DTO definitions in r4-contracts AND umrs | Package boundary isolation; r4-contracts is stdlib-only | Import boundary violations; circular deps |
| ZMQ transport AND HTTP sidecar | Different access patterns; ZMQ for data/binary, HTTP for REST | File transfer would lose streaming; Gateway would lose REST |
| Reconciler AND Monitor | Different timescales; reconciler=boot, monitor=runtime | Crash recovery would be coupled to polling |
| Advisory lock AND attempt state machine | Lock=exclusivity, state=history | Lost audit trail OR race conditions |
| `json_config` in server/ AND shared/ | Different scopes (server-only vs library-accessible) | Import boundary violation |
| DurableJobRuntime AND direct async service | Thread bridge for Gen1 sync caller compatibility | Gen1 callers would break |

**Rule**: Before proposing to merge duplicated code, verify it's not in this list.
If it is, the duplication is load-bearing.

---

## 4. What Must Remain Operationally Conservative

These components prioritize reliability over elegance:

| Component | Conservative Choice | Why |
|-----------|-------------------|-----|
| Scheduler | Poll-based (1s) instead of event-driven | Simpler failure mode; Slurm doesn't push events |
| Monitor | Poll-based (5s) instead of webhooks | Backend-agnostic; works with Slurm, K8s, Compose |
| Config | Boot-only (no hot-reload) | Eliminates runtime race conditions |
| Reconciler | Run-at-boot (not continuous) | Minimizes Postgres load; only needed after crash |
| MessageQueue | No auto-reconnect | Job system provides retry; transport is stateless |
| Advisory locks | Non-blocking (try, not wait) | Prevents deadlocks; job retried next cycle |
| Boot sequence | Strictly sequential | Eliminates ordering dependencies |

**Rule**: Proposals to make these components "smarter" (event-driven, hot-reload,
auto-reconnect) require architecture review demonstrating that the added
complexity does not introduce new failure modes.

---

## 5. Safe Future Work Categories

The following types of changes are safe within the current architecture:

### Category A: Additive Extensions (No Review Required)

- New job types (new entry in backend factory)
- New metrics emissions
- New HTTP sidecar endpoints
- New config keys (with sensible defaults)
- New validator rules (within existing validators)
- New test cases
- Documentation improvements
- New ADR documents

### Category B: Bounded Modifications (PR Review Required)

- New enforcement tests for existing validators
- New reconciler checks
- Config default changes (with migration note)
- Validator threshold adjustments
- Error message improvements
- Logging level changes

### Category C: Structural Extensions (Architecture Review Required)

- New validator tools
- New package boundary rules
- New thread/asyncio task creation
- New persistence backends
- New transport protocols (additive)
- Schema migrations (must bump SCHEMA_VERSION)

### Category D: Cross-Cutting Changes (Design Document Required)

- New packages in the monorepo
- New cross-package dependencies
- Boot sequence modifications
- Shutdown sequence modifications
- Threading model changes
- New deployment topologies

---

## 6. Forbidden Mutation Categories

The following changes are NEVER permitted without explicit ADR amendment:

| Forbidden Change | Protected By | Consequence |
|-----------------|-------------|-------------|
| Remove ZMQ transport | Mutation safety rule | Architecture violation |
| Remove DurableJobRuntime bridge | Mutation safety rule | Gen1 compatibility break |
| Add dependencies to r4-contracts | contracts_fence validator | Build isolation violation |
| Import UMRS from r4 or Gateway | import_boundary_validator | Circular dependency |
| Change execution_authority after job creation | Execution authority invariant | Data integrity violation |
| Add network egress to inference containers | Security invariant | Data exfiltration risk |
| Remove `TRANSFORMERS_OFFLINE=1` | Security invariant | Model download in inference |
| Overwrite existing env vars in SiteConfig | Config authority invariant | Silent config corruption |
| Remove advisory lock mechanism | Correctness invariant | Race conditions in scheduling |
| Hot-reload configuration at runtime | Determinism invariant | Race condition exposure |
| Run multiple schedulers per process | Singleton invariant | Double-scheduling |
| Mix cleanup with feature work in PRs | Governance rule | Unclear revert paths |

---

## 7. Expected Review Triggers

These changes AUTOMATICALLY require architecture review:

| Trigger | Reason | Reviewer |
|---------|--------|---------|
| New `[project.dependencies]` in any pyproject.toml | Dependency direction may violate | Architecture owner |
| New cross-package import path | Boundary may be violated | import_boundary_validator author |
| New/modified Postgres schema | Must bump SCHEMA_VERSION | Schema owner |
| New container port exposure | Security surface expansion | Security reviewer |
| Changes to enforcement tools (`tools/*.py`) | Validator trustworthiness | Validator author |
| Changes to instruction files (`.github/instructions/`) | Governance scope change | Architecture owner |
| New ADR or ADR amendment | Architectural decision | Architecture owner |
| Changes to `umrs/jobs/` or `umrs/server/` | Runtime core modification | Platform team |
| Changes to `MessageQueue.py` | Transport infrastructure | Transport owner |
| Changes to boot/shutdown sequence | Lifecycle modification | Platform team |
| New environment variables in production configs | Operational coupling | Ops team |

---

## 8. Operational Health Indicators

The platform is healthy when:

| Indicator | Expected State | Alert If |
|-----------|---------------|----------|
| All 9 validators pass | 0 errors, 0 warnings | Any failure |
| 61 enforcement tests pass | All green | Any failure |
| 100 UMRS domain tests pass | All green | Any failure |
| Orphan module count | Stable (currently 30) | Increases by >3 |
| ADR linkage warnings | 0 | Any warning |
| Import boundary violations | 0 | Any violation |
| Bridge event metrics | Expected patterns | Unexpected activity on dormant bridges |
| Scheduler retry_burst | 0 in normal operation | Any occurrence |
| Transport errors | < 1/min baseline | Sustained increase |

---

## 9. Stewardship Cadence

| Activity | Frequency | Deliverable |
|----------|-----------|-------------|
| Validator pass (all 9) | Every PR | CI gate |
| Enforcement test suite | Every PR | CI gate |
| UMRS domain test suite | Every PR | CI gate |
| Orphan module review | Monthly | Updated CLEANUP_EXECUTION_PLAN.md |
| Bridge activity review | Monthly | Ops review notes |
| Compatibility lifecycle check | Quarterly | Updated COMPATIBILITY_LIFECYCLE_POLICY.md |
| Architecture stewardship review | Quarterly | Updated this document |
| ADR review | Per decision | New ADR document |
| Security audit | Semi-annually | Security audit report |
| HPC deployment validation | Per release | HPC test results |

---

## 10. Document Cross-References

| Document | Purpose | Update Trigger |
|----------|---------|---------------|
| [RUNTIME_FAILURE_INJECTION_REVIEW.md](RUNTIME_FAILURE_INJECTION_REVIEW.md) | Failure mode classification | New failure modes discovered |
| [VALIDATOR_STABILITY_REVIEW.md](VALIDATOR_STABILITY_REVIEW.md) | Validator fragility assessment | Validator changes |
| [RUNTIME_TOPOLOGY_REFERENCE.md](RUNTIME_TOPOLOGY_REFERENCE.md) | Thread/component ownership | Architecture changes |
| [CLEANUP_EXECUTION_PLAN.md](CLEANUP_EXECUTION_PLAN.md) | Module lifecycle governance | Cleanup activity |
| [OPERATIONAL_DETERMINISM_GAPS.md](OPERATIONAL_DETERMINISM_GAPS.md) | Nondeterminism inventory | New gaps found |
| [COMPATIBILITY_LIFECYCLE_POLICY.md](COMPATIBILITY_LIFECYCLE_POLICY.md) | Bridge/shim governance | Retirement decisions |
| [DEPLOYMENT_DETERMINISM_REVIEW.md](DEPLOYMENT_DETERMINISM_REVIEW.md) | Deployment reproducibility | Deploy changes |
| [RUNTIME_AUTHORITY_REFERENCE.md](RUNTIME_AUTHORITY_REFERENCE.md) | Decision authority matrices | Ownership changes |

---

## 11. Guiding Principles

1. **Stability over simplification**: A working system with known quirks is
   better than a "clean" system with unknown behavior.

2. **Observability before retirement**: Never remove what you cannot measure.
   Add metrics first, observe, then decide.

3. **Isolation over coordination**: Attempt-scoped isolation (S3 prefixes,
   advisory locks) is preferred over cleanup/coordination protocols.

4. **Explicit over implicit**: Document ownership, document invariants,
   document the "why" behind apparent duplication.

5. **Additive over reductive**: New capabilities are additive. Removal requires
   evidence. Evidence requires time.

6. **Conservative by default**: When in doubt, don't change it. The cost of
   a missed optimization is lower than the cost of a broken invariant.
