# Cleanup Execution Plan

> Historical record (dated); not maintained (banner added 2026-11-07). Current status: [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../../R4-UMRS-GAP-ANALYSIS-2026-09-22.md).

*Generated: 2026-05-13 — from validated cleanup readiness report + manual verification*

## Executive Summary

37 cleanup candidates identified by automated tooling. After deep verification
(dynamic import analysis, config references, test dependencies, entrypoint checks):

| Classification | Count | Action |
|---------------|-------|--------|
| SAFE_REMOVE | 3 | Execute in ordered sequence |
| HISTORICAL_ONLY | 2 | Archive/remove (debug scripts) |
| REVIEW_REQUIRED | 14 | Manual review before action |
| LOAD_BEARING | 12 | Do not touch — dynamically loaded or config-referenced |
| DO_NOT_TOUCH | 2 | Core infrastructure despite static orphan appearance |
| STALE_REFERENCE | 4 | Fix reference, not delete module |

---

## Classification Detail

### SAFE_REMOVE — Provably Dead

| # | Target | Evidence | Lines | Rollback |
|---|--------|----------|-------|----------|
| 1 | `umrs/src/umrs/core/mq_zeroMQ.py` | Zero imports, listed as dead in `tasks/todo.md`, superceded by `MessageQueue.py` | ~50 | `git revert` single commit |
| 2 | `umrs/src/umrs/mini-rivault-llm/debug_502.py` | Debug artifact, zero imports, zero config refs | ~30 | `git revert` |
| 3 | `umrs/src/umrs/mini-rivault-llm/debug_bentoml_direct.py` | Debug artifact, zero imports, zero config refs | ~40 | `git revert` |

**Verification checklist for each:**
- [x] Zero `import` statements referencing it
- [x] Zero `importlib` / `__import__` dynamic loading
- [x] Zero test file dependencies
- [x] Zero config/compose/yaml references
- [x] Zero CLI entrypoint registration
- [x] Zero `__init__.py` re-exports

### HISTORICAL_ONLY — Archived Classification

Same as SAFE_REMOVE above (items 2-3). These are debug scripts with no runtime role.

### LOAD_BEARING — Dynamically Loaded or Config-Referenced

These modules appear orphaned to static analysis but are actually in use:

| Module | Loading Mechanism |
|--------|-------------------|
| `umrs/src/umrs/api/rag_service.py` | CLI entrypoint in `serve-model.yaml`; tested |
| `umrs/src/umrs/orchestrator/endpoint_mounts.py` | Imported in `orchestrator/service.py`, `durable.py`, `jobs/adapters.py` |
| `umrs/src/umrs/inference/masked_autoencoder.py` | Dynamic import via `inference/loading.py` |
| `umrs/src/umrs/inference/openai_api.py` | Dynamic import via `inference/loading.py` |
| `umrs/src/umrs/inference/transformer_encoder.py` | Dynamic import via `inference/loading.py`; tested |
| `umrs/src/umrs/vision_service/service.py` | Mounted in `serve-model.yaml`; documented |
| `umrs/src/umrs/vision_service/default_adapters/hmae_adapter.py` | Adapter registry auto-discovery |
| `umrs/src/umrs/vision_service/default_adapters/huggingface_adapter.py` | Adapter registry auto-discovery |
| `umrs/src/umrs/vision_service/default_adapters/sam3_adapter.py` | Adapter registry auto-discovery |
| `umrs/src/umrs/security/scanner.py` | Re-exports DTOs; referenced in convergence docs |
| `r4/src/r4/transport.py` | Core I/O abstraction for CLI transport selection |
| `gateway/gateway/metrics.py` | Prometheus integration point for observability |

### DO_NOT_TOUCH — Infrastructure

| Module | Reason |
|--------|--------|
| `umrs/src/umrs/vision_service/protocol.py` | ABC definitions for adapter interface contract |
| `umrs/src/umrs/inference/hmae/engine.py` | Core inference engine called via reflection |

### REVIEW_REQUIRED — Reassessed Classification (2026-05-13)

Each module is now classified as either **PLANNED_INACTIVE** (intentional future
work, retain) or **CANDIDATE_ORPHAN** (no clear future, investigate further).

#### PLANNED_INACTIVE — Retain with documented rationale

| Module | Rationale | Retention Evidence |
|--------|-----------|-------------------|
| `umrs/src/umrs/model_registry/conversion.py` | Model format conversion is Phase 2 planned work | Referenced in DEVELOPMENT_OBJECTIVES.md |
| `umrs/src/umrs/model_registry/versions.py` | Model versioning is Phase 2 planned work | Referenced in DEVELOPMENT_OBJECTIVES.md |
| `umrs/src/umrs/slurm/k8s_bootstrap.py` | K8s worker airgap setup; active in `deploy/k8s-worker-airgap-setup.sh` | Deployment script reference |
| `r4/src/r4/acp.py` | Agent Communication Protocol — Phase 2 multi-agent work | Referenced in README.md Phase 2 |
| `r4/src/r4/briefing.py` | Research briefing feature — planned companion integration | Referenced in companion instructions |
| `r4/src/r4/cost.py` | Token cost tracking — planned observability feature | Referenced in DEVELOPMENT_OBJECTIVES.md |

**Retention policy**: These modules remain in-tree with no cleanup action.
Reassess in 6 months (2026-11) if still unused.

#### CANDIDATE_ORPHAN — Requires owner confirmation before action

| Module | Uncertainty | Evidence Required for Deletion |
|--------|-------------|-------------------------------|
| `umrs/src/umrs/admin/create_users.py` | Re-exported in `__init__.py`; unclear if provisioning uses it | Confirm no CLI/script invocation |
| `umrs/src/umrs/orchestrator/slurm_vision_script.py` | May be superseded by mini-RiVault vision endpoint | Confirm no Slurm script references |
| `umrs/src/umrs/scheduler/triggers.py` | Unfinished event trigger system; no consumers found | Confirm not in any branch |
| `umrs/src/umrs/voice/whisper_adapter.py` | No adapter registry discovery for voice | Confirm no planned voice feature |
| `umrs/src/umrs/pipeline_sdk/connectors/bentoml.py` | Unfinished connector stub | Owner confirmation: dead or planned? |
| `umrs/src/umrs/pipeline_sdk/connectors/pgvector.py` | Unfinished connector stub | Owner confirmation: dead or planned? |
| `umrs/src/umrs/pipeline_sdk/connectors/s3.py` | Docs-only reference, no imports | Owner confirmation: dead or planned? |
| `r4/src/r4/_dto.py` | Internal utility, not exported | Confirm no lazy/dynamic usage |

**Deletion evidence requirements** (ALL must be met):
1. Owner confirms no planned use
2. Zero imports (static + dynamic + config)
3. Zero test dependencies
4. No branch references the module
5. `git log --since="6 months ago" -- <file>` shows no recent activity

### STALE_REFERENCE — Fix Docs, Not Delete Modules

| Location | Issue | Fix |
|----------|-------|-----|
| `docs/TECHNICAL_REFERENCE.md:1080-1081` | References nonexistent ADR (number 026) | Remove stale reference |
| `docs/CLEANUP_READINESS.md:60-63` | Self-referential (stale refs from own report) | Regenerate report |

---

## Ordered Cleanup Sequence

Execute in this order to minimize risk and maximize reversibility:

### Phase 1 — Zero-Risk Removals ✅ COMPLETE

Executed 2026-05-13 on branch `arch/archaeology-audit-2026-05-13`:

1. ~~Delete `umrs/src/umrs/mini-rivault-llm/debug_502.py`~~ — done
2. ~~Delete `umrs/src/umrs/mini-rivault-llm/debug_bentoml_direct.py`~~ — done
3. ~~Delete `umrs/src/umrs/core/mq_zeroMQ.py`~~ — done

**Result:** Orphan candidates dropped 33→30. All 9 validators pass. 61 enforcement tests + 100 UMRS domain tests green.

### Phase 2 — Stale Reference Fix ✅ COMPLETE

Executed 2026-05-13 on branch `arch/archaeology-audit-2026-05-13`:

1. ~~Remove stale ADR references from `docs/TECHNICAL_REFERENCE.md`~~ — done (false-positive from ADR regex matching dates)
2. ~~Regenerate `docs/CLEANUP_READINESS.md`~~ — done (stale references section now shows 0)
3. Fixed `tools/adr_linkage_validator.py` regex to prevent date-string false positives

**Result:** ADR linkage warnings dropped 10→0. Validator regex hardened.

### Phase 3 — REVIEW_REQUIRED Assessment (No PR — Investigation Only)

For each REVIEW_REQUIRED module:
1. Run `git log --all --source --full-history -- <file>` to check last-modified date
2. Check if any branch references it
3. Confirm with module owner

Only promote to SAFE_REMOVE after:
- Owner confirms no planned use
- No test regressions when removed
- No dynamic loading pathway exists

---

## Risk Table

| Action | Risk | Blast Radius | Recovery Time |
|--------|------|-------------|---------------|
| Delete debug scripts | None | 0 files affected | Instant revert |
| Delete mq_zeroMQ.py | Low | 0 files (confirmed dead) | Instant revert |
| Fix stale ADR refs | None | Documentation only | Instant revert |
| Delete CANDIDATE_ORPHAN | Medium | Unknown until verified | Needs investigation |
| Delete PLANNED_INACTIVE | High | Feature work blocked | Needs reimplementation |

---

## Cleanup Anti-Patterns

The following patterns are FORBIDDEN in cleanup work:

### Anti-Pattern 1: "Looks Dead, Must Be Dead"

**Wrong**: Module has no static imports → delete it.
**Right**: Check dynamic loading, config references, CLI entrypoints, test
fixtures, and adapter registries before concluding a module is dead.

### Anti-Pattern 2: "Simplification by Deletion"

**Wrong**: This module seems redundant with another → delete it.
**Right**: Confirm the modules have identical runtime behavior AND all consumers
have been migrated. Redundancy may be intentional (isolation, compatibility).

### Anti-Pattern 3: "Cleanup + Feature in One PR"

**Wrong**: While cleaning up X, also add feature Y that touches the same area.
**Right**: Cleanup PRs are single-purpose. Feature work is separate. This ensures
clean revert paths and clear attribution.

### Anti-Pattern 4: "Bridge Retirement Without Metrics"

**Wrong**: This bridge has been around for months and nobody uses it → remove it.
**Right**: Bridges require 30 days of zero-activity metrics AND explicit owner
confirmation before retirement. "Nobody uses it" is not evidence without metrics.

### Anti-Pattern 5: "Bulk Cleanup Sprint"

**Wrong**: Delete 10 modules in one PR to reduce orphan count.
**Right**: Each deletion is a separate, individually-revertable change with its
own evidence trail. Batch cleanup creates debugging nightmares.

### Anti-Pattern 6: "Validator Count as Quality Metric"

**Wrong**: Reduce orphan_module_detector count by deleting modules.
**Right**: Reduce count by correctly classifying modules (LOAD_BEARING,
DYNAMIC_IMPORT, PLANNED_INACTIVE). The metric measures understanding, not waste.

### Anti-Pattern 7: "Runtime Logic Changes Disguised as Cleanup"

**Wrong**: While removing a dead module, refactor adjacent code for clarity.
**Right**: Cleanup PRs remove dead code ONLY. Refactoring is a separate activity
with its own review and test requirements.

---

## Deferred Module Retention Rationale

Modules classified as PLANNED_INACTIVE are retained because:

1. **Implementation cost**: Re-creating them later is more expensive than maintaining
   their presence (they impose zero runtime cost while dormant).

2. **Design signal**: Their existence documents architectural intent. Removing
   them erases institutional knowledge about planned capabilities.

3. **Test coverage preparation**: Some have partial test infrastructure that would
   need rebuilding.

4. **Branch hygiene**: They exist on the main branch and removing them creates
   merge conflicts with feature branches that depend on them.

**Reassessment cadence**: Every 6 months, check:
- Has any development activity occurred?
- Is the planned feature still in the roadmap?
- Has the design changed such that the module is obsolete?

If all answers are "no" for 12 consecutive months, reclassify as CANDIDATE_ORPHAN.

---

## Forbidden Actions

- ❌ Bulk delete REVIEW_REQUIRED modules without verification
- ❌ Delete any LOAD_BEARING or DO_NOT_TOUCH module
- ❌ Mix cleanup with feature work in same PR
- ❌ Delete ZMQ infrastructure (`MessageQueue.py`, control/data queues)
- ❌ Remove compatibility bridges without traffic analysis
- ❌ Delete modules from `umrs/src/umrs/jobs/` or `umrs/src/umrs/server/`
- ❌ Delete PLANNED_INACTIVE modules without roadmap confirmation
- ❌ Retire validators without replacement coverage analysis

---

## Rollback Strategy

All cleanup PRs must:
1. Be single-purpose (cleanup only, no features)
2. Be individually revertable via `git revert`
3. Pass all 9 validators before merge
4. Pass all enforcement tests
5. Pass all UMRS domain tests

If ANY test regression occurs post-merge:
1. Immediately `git revert` the cleanup PR
2. Reclassify the module as LOAD_BEARING
3. Update orphan_module_detector exclusions
