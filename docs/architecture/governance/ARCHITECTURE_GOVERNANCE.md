# Architecture Governance

Processes for maintaining architectural integrity over time.

## Architecture Review Triggers

The following changes require architecture review before merge:

| Trigger | Reviewer | Gate |
|---------|----------|------|
| New `[project.dependencies]` in any pyproject.toml | Architecture owner | Block merge |
| New cross-package import path | Architecture owner | Block merge |
| New or modified Postgres schema | Architecture owner + UMRS owner | Block merge |
| New container port exposure | Security + Architecture owner | Block merge |
| Changes to enforcement tools (`tools/*.py`) | Architecture owner | Block merge |
| Changes to `.github/instructions/*.md` | Architecture owner | Block merge |
| New ADR or ADR amendment | Architecture owner | Block merge |
| New `.env` variable in deployment templates | Security owner | Block merge |
| New UMRS module directory | Architecture owner | Advisory |

## Invariant Review Workflow

1. **Author** runs `make ci` locally (or individual validators)
2. **CI** runs `boundary-check` job — blocks on failure
3. **Reviewer** checks that invariants listed in the PR description match changed files
4. **Merge** proceeds only when all hard-fail validators pass

## ADR Lifecycle

| Stage | Action |
|-------|--------|
| **Proposed** | Author creates ADR in `docs/adr/`, status = "Proposed" |
| **Accepted** | Architecture owner reviews and sets status = "Accepted" |
| **Superseded** | New ADR references the old one, old status = "Superseded by ADR-NNN" |
| **Deprecated** | ADR no longer applies, status = "Deprecated" with reason |

ADRs are numbered sequentially. Never reuse a number. Never delete an ADR — only
supersede or deprecate.

## Architectural Ownership

| Component | Owner | Scope |
|-----------|-------|-------|
| r4 (SDK/CLI) | Platform team | Client experience, CLI commands, companion |
| r4-contracts | Platform team | Shared DTOs, validation, zero-dep invariant |
| Gateway | Platform team | Routing, auth proxy, file fetch, audit |
| UMRS | UMRS team | Jobs, orchestrator, model registry, context, MCP, RAG |
| Enforcement tools | Architecture owner | Validators, CI gates, pre-commit hooks |
| ADRs | Architecture owner | Decision records, review |
| Deployment | Operations team | Slurm scripts, Singularity, Ansible, site.yaml |

## Production Readiness Sign-Off

Before a deployment can be declared production-ready, sign-off from the
following roles is required. See
[PRODUCTION_READINESS_GATE.md](PRODUCTION_READINESS_GATE.md) for the full
checklist.

### Sign-Off Roles and Responsibilities

| Role | Reviews | Evidence Artifacts |
|------|---------|-------------------|
| **Architecture owner** | Boundary integrity, invariant compliance, validator pass | `make arch-health` output; scorecard |
| **Security reviewer** | Auth, isolation, egress, audit durability, secret lifecycle | Container env audit; presign test; egress deny log |
| **Operator** | Infrastructure, networking, secrets, backups, monitoring | Smoke test output; backup timestamp; DR rehearsal date |
| **Data steward** | Backup, retention, recovery, PII handling | PG backup log; restore test date; retention config |
| **Release manager** | Version, migrations, compatibility, rollback | Migration check output; rollback procedure doc |

### Evidence Requirements

| Evidence | Format | Retention |
|----------|--------|-----------|
| Validator output | `make arch-health` stdout | Attached to release PR |
| Smoke test output | Terminal log or CI artifact | Attached to release PR |
| Backup restore test | Date, elapsed time, data loss (if any) | Linked from readiness gate |
| Container env audit | `env` dump from running container | Attached to security review |
| Secret rotation test | Date, affected services, downtime | Linked from readiness gate |
| DR rehearsal | Date, RTO achieved, RPO achieved | Filed in ops log |

### Sign-Off Workflow

```
1. Developer opens release PR
2. CI runs all hard-fail validators → must pass
3. Architecture owner reviews validator output and boundary checks
4. Security reviewer verifies container env, egress, audit durability
5. Operator confirms infrastructure readiness (backup, monitoring, secrets)
6. Data steward confirms backup schedule and retention policy
7. Release manager confirms migrations, compatibility, rollback
8. All roles sign the readiness template in PRODUCTION_READINESS_GATE.md
9. Merge and deploy
```

## Enforcement Escalation Policy

| Level | Condition | Action |
|-------|-----------|--------|
| **Info** | New warning from validator | Log, track in issue |
| **Warning** | Known violation in transitional enforcement | Must be fixed within 90 days |
| **Error** | Hard-fail validator blocks CI | Must be fixed before merge |
| **Critical** | Security invariant violation (INV-05, INV-06, INV-07) | Immediate fix required, escalate to security owner |

## Compatibility Retirement Policy

| Stage | Duration | Action |
|-------|----------|--------|
| **Announce** | Day 0 | Add deprecation notice to code and docs |
| **Warn** | 0–30 days | Validator emits warnings, migration guide available |
| **Soft-fail** | 30–60 days | Validator fails but CI continues |
| **Hard-fail** | 60+ days | Validator blocks CI merge |
| **Remove** | After hard-fail period | Delete deprecated code, retire validator |

## Compatibility Layers

### Permanent Infrastructure

| Component | Role | Why permanent |
|-----------|------|---------------|
| ZMQ data-plane transport | Binary file transfer, streaming | Required for HPC file operations (ADR-001) |
| HTTP control-plane | API calls, job management | Standard REST API surface |
| DurableJobRuntime bridge | Gen1↔Gen2 job coexistence | Required until Gen1 ZMQ jobs are fully retired (ADR-007) |
| SiteConfig env overlay | Deployment configuration | Universal config pattern (ADR-008) |

### Medium-Term Bridges (retire when convergence completes)

| Component | Role | Retirement prerequisite |
|-----------|------|------------------------|
| DTO equivalence checker | Validates mirrored DTOs match | UMRS imports from r4-contracts (Step 5) |
| UMRS internal boundary warnings | Foundation module import isolation | Fix known violations in core/shared/inference/jobs/admin |

### Retirement Candidates

| Component | Role | Blocker |
|-----------|------|---------|
| Mirrored DTO definitions in UMRS | Duplicate SkillManifest, RecipeDefinition, etc. | Step 5 completion |
| `mq_posix_ipc.py`, `mq_sysv_ipc.py` | Dead IPC transports | **Already removed** |
| `server/runtime.py` | Dead runtime shim | **Already removed** |

### Observability Requirements

All compatibility layers must have:
- Log messages at transition points (e.g., "falling back to Gen1 transport")
- Metrics for usage frequency (to determine when retirement is safe)
- Clear error messages when a layer fails

## AI-Assisted Development Guard Rails

### Task Templates

When using AI assistants on this repository:

**Safe tasks (proceed without review):**
- Adding tests for existing code
- Fixing lint/type errors in existing code
- Updating documentation
- Adding logging or metrics
- Fixing bugs within a single module

**Bounded tasks (run validators after):**
- Adding new CLI commands (must use r4.Client)
- Adding new UMRS modules (must not import from umrs.api/umrs.server)
- Modifying Gateway routes (must remain proxy-only)
- Updating deployment configs (must not add secrets to YAML)

**Review-required tasks (do not auto-merge):**
- Adding dependencies to any pyproject.toml
- Modifying enforcement tools
- Changing schemas (must bump version)
- Cross-package refactoring
- New ADRs or instruction files

### Architecture-Preserving Refactor Templates

**Moving code within UMRS:**
```
1. Create new location with the code
2. Add re-export from old location
3. Update internal consumers to use new location
4. In separate PR: remove old location and re-export
```

**Adding a shared type:**
```
1. Define in r4-contracts/ using @dataclass, stdlib only
2. Add to r4-contracts __init__.py exports
3. Import in consumers (r4, gateway)
4. If UMRS needs it: add to DTO_PAIRS in dto_equivalence_check.py
```

**Fixing a validator failure:**
```
1. Read the error message — it tells you which invariant is violated
2. Check docs/ARCHITECTURE_ENFORCEMENT.md for the fix pattern
3. Fix the violation (do NOT modify the validator to skip the check)
4. Run the validator again locally
5. If unsure, check the relevant ADR
```

### Invariant-Aware Review Checklist

Before approving a PR, verify:

- [ ] `boundary-check` CI job passes
- [ ] No new cross-package imports added
- [ ] No new dependencies added to r4-contracts
- [ ] Schema changes include version bump
- [ ] Gateway changes remain proxy-only (no durable state)
- [ ] UMRS foundation modules don't import from umrs.api/umrs.server
- [ ] Security invariants preserved (offline inference, auth chain)
- [ ] Deployment configs don't include secrets in YAML

### Bounded Mutation Workflows

**Rule:** Every AI-generated edit must be followed by validator execution.

```bash
# After ANY code edit:
python tools/import_boundary_validator.py
python tools/contracts_fence.py

# After schema edits:
python tools/schema_version_tracker.py

# After DTO edits:
python tools/dto_equivalence_check.py

# After build file edits:
python tools/stale_name_validator.py
```

### Repository Mutation Policies

1. **No blind mass-replace.** Every rename requires classification first.
2. **No transport consolidation.** ZMQ and HTTP coexist deliberately.
3. **No framework replacement.** Existing patterns are stable.
4. **No speculative refactoring.** Only change what's needed.
5. **No silent compatibility removal.** Follow the retirement policy.
6. **Additive before subtractive.** Add new → migrate → remove old.
7. **One concern per PR.** Don't mix cleanup with feature work.
