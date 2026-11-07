# Steady-State Engineering Policy

> **SUPERSEDED — pending retirement (banner added 2026-10-18, platform hardening plan H-11.3)** — pre-2026-09-22 design reset (2026-05). [CONSTITUTION.md](../../../CONSTITUTION.md) binds; its proposed amendment [ADR-094](../../adr/094-constitution-amendment-numbered-principles.md) (Proposed) would carry three of its rules (INV-28, CC-4, CC-6; maintainer question MQ-3) and retire this file to `docs/history/`. The "CANONICAL — Binding" status below is no longer true; never cite this file as current. Its retirement is the maintainer's decision (M-H5). See [docs/README.md](../../README.md) §4.

*Established: 2026-05-13*
*Status: CANONICAL — Binding Governance Document*

---

## Declaration

**The AI4S Platform (R4 + UMRS) is now in STEADY-STATE OPERATIONAL GOVERNANCE mode.**

This means:
- Active reconstruction phases are COMPLETE
- All architectural boundaries are FINAL
- Runtime ownership is SETTLED (see `UMRS_CORE_RUNTIME_SPEC.md`)
- Client/runtime separation is ESTABLISHED (see `R4_CLIENT_ROLE_DEFINITION.md`)
- Transport infrastructure (ZMQ + HTTP sidecar) is PERMANENT
- Compatibility bridges (DurableJobRuntime) are PERMANENT until explicit ADR retires them

Changes are governed by conservative mutation policies. The default posture is
"preserve stability" rather than "pursue improvement."

---

## 1. Acceptable Change Categories

### Category A: Free Changes (No Special Review)

Changes that are safe within existing architecture:

- Bug fixes that don't alter public API surface
- New test cases
- Documentation corrections
- Log message improvements
- New metrics emissions (additive)
- New job types (via backend factory)
- New HTTP sidecar endpoints (additive)
- New config keys with sensible defaults
- Performance improvements with equivalent behavior

### Category B: Bounded Changes (PR Review)

Changes that touch existing behavior but don't alter architecture:

- Config default adjustments (with migration note)
- Validator rule additions within existing validators
- Error handling improvements
- Reconciler check additions
- New CLI subcommands (using `r4.Client`)
- Logging level changes
- Test fixture improvements

### Category C: Structural Extensions (Architecture Review)

Changes that extend the system's surface area:

- New validator tools
- New package boundary rules
- New thread or asyncio task creation
- New persistence backends
- New transport protocols (additive only)
- Schema migrations (bump SCHEMA_VERSION)
- New deployment topology support
- New UMRS module directories

### Category D: Cross-Cutting Changes (Design Document Required)

Changes that affect multiple packages or fundamental assumptions:

- New packages in the monorepo
- New cross-package dependencies
- Boot sequence modifications
- Shutdown sequence modifications
- Threading model changes
- New deployment environments
- Security boundary changes

---

## 2. Forbidden Change Categories

These changes are NEVER permitted without explicit ADR amendment:

| Forbidden Change | Rationale |
|-----------------|-----------|
| Remove ZMQ transport | Permanent infrastructure; data-plane depends on it |
| Remove DurableJobRuntime bridge | Gen1 compatibility; no retirement ADR exists |
| Add dependencies to r4-contracts | Zero-dep stdlib-only invariant |
| Import UMRS from r4/Gateway | Circular dependency; violates boundary |
| Change execution_authority post-creation | Data integrity invariant |
| Add network egress to inference containers | Security invariant |
| Remove `TRANSFORMERS_OFFLINE=1` | Security invariant |
| Overwrite env vars in SiteConfig.inject_env() | Config authority invariant |
| Hot-reload config at runtime | Determinism invariant |
| Run multiple schedulers per process | Singleton invariant |
| Mix cleanup with feature work in same PR | Governance rule |
| Expose presigned S3 URLs to external clients | Security invariant |
| Add durable state to Gateway | Gateway is proxy-only |
| Add retry/workflow logic to Gateway | UMRS owns orchestration |

---

## 3. Runtime Stability Expectations

### What "Stable" Means

- **APIs**: Public UMRS HTTP endpoints have backwards-compatible evolution (additive only)
- **Wire Protocol**: ZMQ message formats are versioned; old formats must be readable
- **Config**: New keys can be added with defaults; existing keys cannot change semantics
- **Database**: Schema changes require migration scripts and SCHEMA_VERSION bump
- **Boot Behavior**: Components boot in documented order; additions are allowed, reordering is not
- **Shutdown**: Graceful shutdown sequence is fixed; additions are allowed

### What "Stable" Does NOT Mean

- "Never change" — bounded improvements are welcome
- "No new features" — additive extensions are encouraged
- "Frozen code" — bug fixes and hardening always apply
- "No refactoring" — local refactoring within a package is fine

---

## 4. Validator Evolution Policy

### Adding New Validators

1. Validator must have a clear invariant it protects
2. Must not produce false positives on existing codebase
3. Must have enforcement tests
4. Must be documented in `ARCHITECTURE_ENFORCEMENT.md`
5. Requires architecture review

### Modifying Existing Validators

1. Must not cause existing passing code to fail without explicit migration
2. Threshold changes require documented rationale
3. New exemptions must be tracked in `EXEMPTION_INVENTORY.md`

### Validator Trust

- All validators run in CI as hard-fail gates
- Local pre-commit runs are encouraged but not enforced
- Validator output is authoritative for merge decisions
- False positives must be fixed in the validator, not worked around

---

## 5. Cleanup Safety Policy

### Safe Cleanup

- Remove dead code clearly unreachable (no dynamic references)
- Remove unused imports
- Remove commented-out code older than 2 release cycles
- Consolidate test fixtures within a single test module

### Unsafe Cleanup (Requires Review)

- Remove modules in `CLEANUP_EXECUTION_PLAN.md` "planned-inactive" list
- Remove anything in `ARCHITECTURE_STEADY_STATE_GUIDE.md` "intentionally duplicated" list
- Remove compatibility shims or bridges
- Remove "unused" code that may be referenced dynamically

### Forbidden Cleanup

- Never remove canonical governance documents
- Never remove ADRs (supersede them instead)
- Never remove runtime docs that describe active behavior
- Never remove test infrastructure for existing invariants

---

## 6. Rollback Expectations

### Code Changes

- Every PR must be independently revertable
- Never combine cleanup with feature work
- Feature flags preferred for risky additions
- Deployment changes must have documented rollback steps

### Schema Changes

- Forward migrations are required
- Backward migrations are strongly recommended
- Schema version must only increase
- Never modify an applied migration in-place

### Configuration Changes

- New config keys must have backward-compatible defaults
- Removed config keys must be ignored gracefully (not crash)
- Config changes in deployment must be reversible via env var

---

## 7. Architecture Review Triggers

These changes AUTOMATICALLY require architecture review before merge:

| Trigger | Reviewer |
|---------|----------|
| New `[project.dependencies]` in any pyproject.toml | Architecture owner |
| New cross-package import path | Architecture owner |
| New/modified Postgres schema | Architecture + UMRS owner |
| New container port exposure | Security + Architecture |
| Changes to enforcement tools (`tools/*.py`) | Architecture owner |
| Changes to instruction files (`.github/instructions/`) | Architecture owner |
| New ADR or ADR amendment | Architecture owner |
| Changes to `umrs/jobs/` or `umrs/server/` | Platform team |
| Changes to `MessageQueue.py` | Transport owner |
| Changes to boot/shutdown sequence | Platform team |
| New env vars in production configs | Ops team |
| New `.env` variable in deployment templates | Security owner |

---

## 8. Operational Stewardship Cadence

| Activity | Frequency | Owner |
|----------|-----------|-------|
| Validator pass (all 9) | Every PR (CI) | Automated |
| Enforcement test suite (61 tests) | Every PR (CI) | Automated |
| UMRS domain test suite (168 tests) | Every PR (CI) | Automated |
| Orphan module review | Monthly | Architecture owner |
| Bridge activity review | Monthly | Ops team |
| Compatibility lifecycle check | Quarterly | Architecture owner |
| Architecture stewardship review | Quarterly | Architecture owner |
| Security audit | Semi-annually | Security owner |
| HPC deployment validation | Per release | Ops team |

---

## 9. Relationship to Other Documents

| Document | Relationship |
|----------|-------------|
| `ARCHITECTURE_STEADY_STATE_GUIDE.md` | Defines what is stable/transitional/forbidden |
| `UMRS_CORE_RUNTIME_SPEC.md` | Formal UMRS runtime ownership spec |
| `R4_CLIENT_ROLE_DEFINITION.md` | Formal R4 client boundary spec |
| `mutation-safety.instructions.md` | AI/developer-facing forbidden patterns |
| `ARCHITECTURE_GOVERNANCE.md` | Review process and ADR lifecycle |
| `COMPATIBILITY_LIFECYCLE_POLICY.md` | Component retirement governance |
| `ARCHITECTURE_ENFORCEMENT.md` | Validator reference |
| `DOCUMENT_LIFECYCLE_INDEX.md` | Document classification index |
| `ARCHITECTURE_DOCUMENT_MAP.md` | Navigation index |
