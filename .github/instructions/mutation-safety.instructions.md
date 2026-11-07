---
description: Repository mutation safety rules — architectural invariants and forbidden patterns
applyTo: '**'
---

# Mutation Safety Rules

Updated 2026-09-22 (design reset): rules re-derived from
[CONSTITUTION.md](../../CONSTITUTION.md) §1, §2, §6 and §7; scorecard and
validator count re-run on that date. Where the tree still differs from
[docs/R4-UMRS-DESIGN.md](../../docs/R4-UMRS-DESIGN.md) the gap row (`D-nn`
in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md))
is named; an agent never closes a structural gap without its ADR.

These rules protect architectural invariants under steady-state
governance: conservative mutation policies with explicit ownership
boundaries (UMRS owns durable state, scheduling, storage, transport and
identity; R4 owns the client surfaces; the edge is a stateless proxy).
Every AI-assisted edit MUST comply.

## Forbidden Patterns

### Import Direction Violations
- NEVER add `import umrs` in `r4/` or `gateway/`
- NEVER add `import r4` in `umrs/` or `gateway/`
- NEVER add `import gateway` in `r4/` or `umrs/`
- NEVER add `[project.dependencies]` to `r4-contracts/pyproject.toml`

### UMRS Internal Layer Violations
- NEVER import from `umrs.api` or `umrs.server` in foundation modules:
  `core/`, `jobs/`, `model_registry/`, `mcp/`, `rag/`, `pipeline_sdk/`,
  `orchestrator/`, `security/`, `shared/`
- NEVER import router modules from `umrs/api/__init__.py`; routers are
  mounted only by the registry in `umrs/src/umrs/server/http_sidecar.py`
- NEVER ship an `umrs/api` router module that the sidecar does not mount
  (delete it or mount it behind a lane, ADR-053)

### Security Invariant Violations
- NEVER add network/egress access to inference containers
- NEVER remove `TRANSFORMERS_OFFLINE=1` / `HF_HUB_OFFLINE=1` from inference environments
- NEVER remove NGINX `auth_request` from edge routes; the edge validates the JWT on every request (2026-10-18: the qualifier "until D-02 moves authentication into UMRS" was stale — ADR-059 made the edge UMRS's edge process, D-02 Implemented by design amendment)
- NEVER overwrite existing env vars in `SiteConfig.inject_env()` (ADR-008)
- NEVER expose presigned S3 URLs to external clients
- NEVER add a new path that hands storage credentials to a client (the
  per-user `s3cfg` delivery is gap D-16 — being removed, not extended)
- NEVER change `execution_authority` after job creation (ADR-004)
- NEVER remove the `r4-kb` bundle export capability from knowledge services (INV-13; the Scientific Catalog bundle when D-39/D-40 close)
- NEVER let agent-originated knowledge mutation bypass a recorded user decision;
  `knowledge_graph_decide` never auto-approves (`umrs/src/umrs/api/_knowledge_mutation_guard.py`, migration 0069, ADR-050)
- NEVER give a remote-session worker a user JWT or a provider key (ADR-046)
- NEVER let a ZeroMQ server socket in `session` or `curve` mode bind without key material

### Edge (`gateway/`) Boundary Violations
- NEVER add durable state (database, job stores) to the edge
- NEVER add retry/workflow/orchestration logic to the edge — that belongs in UMRS
- The NGINX + auth-sidecar edge is a proxy/policy layer only; it is UMRS's
  edge process by ADR-059 — an agent does not remove or bypass it on its
  own; changing that is a new ADR (2026-10-18: said "its future is decision
  D-01/D-02", decided 2026-09-22)

### Transport Violations
- NEVER remove the ZeroMQ data-plane transport (required for file/binary operations)
- NEVER remove the `DurableJobRuntime` bridge (`umrs/src/umrs/server/durable_job_runtime.py`) without an ADR (CONSTITUTION §5.3)

### Client-Side Duplication
- NEVER add a client-side job store, workflow registry, retry state machine
  or storage credential in `r4/`, `gateway/` or platform tests (CONSTITUTION §1.3)
- NEVER add a new research-loop or team engine beside the server-hosted loop
  (`umrs/src/umrs/knowledge/loop.py`, ADR-036) and the Team Engine SPI
  (ADR-020) — gap rows D-29, D-32

### Test Hygiene Violations
- NEVER use `sys.path.insert` or `importlib.util.spec_from_file_location`
  in a test — fix the import graph instead (ADR-053)
- NEVER pin a module in `orphan_module_detector` to hide that nothing imports it

### Deployment Violations
- NEVER use ad-hoc SSH commands to deploy code or restart services on managed hosts (umrs-front, rccs-cloud, etc.)
- NEVER use `scp` to copy individual source files to remote deployments
- NEVER edit a deployed host's checkout by hand — it is a read-only deploy
  input; a hot-patch is a repo commit + Ansible converge (ADR-019 amendment)
- ALWAYS use Ansible playbooks via `deploy/ansible/bootstrap.sh --profile <profile>` for all deployment operations
  (2026-11-07: `deploy/ansible/` exists only in the development repository;
  in the public tree, deploy from `deploy/standalone/` or the UMRS templates in
  `umrs/deploy/templates/` with your own site tooling, never by hand on a host)
- See deployment-operations.instructions.md for correct commands

### Documentation Violations
- NEVER cite `docs/**/archive/` or `docs/history/` as current state
- NEVER edit `docs/R4-UMRS-DESIGN.md` without an ADR

## Required Validation After Edits

Canonical gate before any commit touching Python (stages 0–4; see
[ci-harness.instructions.md](ci-harness.instructions.md)):
```bash
bash scripts/ci-lint-local.sh                 # changed files vs base — must print ALL CHECKS PASSED
bash scripts/ci-lint-local.sh --umrs-changed  # additionally, whenever the umrs pointer moved
```

After editing ANY Python file, run:
```bash
python tools/import_boundary_validator.py
python tools/contracts_fence.py
python tools/stale_name_validator.py
```

After editing schema files (`umrs/src/umrs/*/schema.py`):
```bash
python tools/schema_version_tracker.py
```

After editing DTOs in `r4-contracts/` or `umrs/src/umrs/skills/` or `umrs/src/umrs/recipes/`:
```bash
python tools/dto_equivalence_check.py
```

After editing deployment configs (`deploy/`, `umrs/deploy/`, `site.yaml`, `.env`):
```bash
python tools/config_drift_validator.py
```

After editing `mcp-servers/src/r4_mcp/runners/custom_mcp.py` or `mcp-servers/src/r4_mcp/security.py`:
```bash
.venv/bin/python -m pytest mcp-servers/tests/test_extensibility_security.py mcp-servers/tests/test_custom_mcp.py -v
```

To run the **full enforcement regression suite** (24 registered validators, 390 enforcement tests on 2026-09-22):
```bash
.venv/bin/python -m pytest tests/test_import_boundary_validator.py \
  tests/test_contracts_fence.py tests/test_dto_equivalence.py \
  tests/test_schema_version_tracker.py tests/test_stale_name_validator.py \
  tests/test_topology_tools.py -v
python tools/enforcement_maturity_scorer.py
```

Expected scorecard: `A:24 B:0 C:0 D:0` (read N from the scorer output; the
registry count changes when validators are added, and this line is updated
in the same commit). Exemptions are governed by
`python tools/exemption_governance_validator.py`.

## Safe Change Patterns

1. **Additive first**: Add new code alongside old, migrate consumers, then remove old
2. **Never move + remove in same PR**: Split into separate PRs
3. **Cross-package communication**: Use the HTTP or ZeroMQ wire protocol, not Python imports
4. **New UMRS modules**: Place in `umrs/src/umrs/<module>/`, no imports from `umrs.api`/`umrs.server`
5. **New CLI commands**: Use `r4.Client` for UMRS operations, never direct UMRS imports
6. **New shared DTOs**: Define in `r4-contracts/`, stdlib-only, use `@dataclass`
7. **Name the gap rows** (`D-nn`) a change advances and update their status in the same commit

## Review-Required Changes

These changes require architecture review before merge:
- New `[project.dependencies]` in any pyproject.toml
- New cross-package import paths
- New or modified Postgres schemas (must bump SCHEMA_VERSION)
- New container port exposure
- Changes to enforcement tools (`tools/*.py`)
- Changing the lint/complexity gate SCOPE or adding a baseline file (ADR-052)
- Changes to instruction files (`.github/instructions/*.md`)
- New ADR or ADR amendment

These require an ADR (CONSTITUTION §5.3):
- Any change that moves the tree away from `docs/R4-UMRS-DESIGN.md`, or any edit to the design itself
- Reversing or reopening a structural decision recorded in an ADR: transport and edge (ADR-059), topology (ADR-060, D-47 converging), sandbox profiles (ADR-061, D-12 Partial), the catalog object (D-39, object landed, Partial) (2026-10-18: listed these as decisions still to take; CONSTITUTION §5.3 still does, see its dated erratum)
- Removing permanent infrastructure (ZeroMQ, `DurableJobRuntime` bridge)
- Changing the `execution_authority` model
- Adding dependencies to `r4-contracts`
- Adding network egress to inference containers
