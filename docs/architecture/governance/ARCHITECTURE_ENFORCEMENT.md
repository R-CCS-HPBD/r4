# Architecture Enforcement

This document describes the automated architecture enforcement tools, how to run
them, and what to do when they fail.

## Quick Reference

```bash
# Run all validators (same as CI)
python tools/import_boundary_validator.py
python tools/contracts_fence.py
python tools/config_drift_validator.py
python tools/stale_name_validator.py
python tools/schema_version_tracker.py
python tools/dto_equivalence_check.py
python tools/runtime_support_matrix.py
```

## Enforcement Tools

### Hard-Fail (blocks CI merge)

| Tool | Invariant | What it checks |
|------|-----------|---------------|
| `import_boundary_validator.py` | INV-01 | No cross-package Python imports (r4↔umrs↔gateway) |
| `contracts_fence.py` | INV-10 | r4-contracts has zero `[project.dependencies]` |
| `config_drift_validator.py` | Config consistency | Gateway/UMRS config alignment |
| `stale_name_validator.py` | Naming | No stale `aisci` package references in build files |
| `schema_version_tracker.py` | SI-02 | UMRS schema versions match `tools/schema_versions.yaml` |
| `dto_equivalence_check.py` | DTO shapes | r4-contracts and UMRS DTO mirrors stay equivalent |
| `runtime_support_matrix.py` | Runtime topology | Supported runtime compositions are explicit and machine-checkable |

### Warning-Only (does not block CI)

| Tool | Invariant | What it checks |
|------|-----------|---------------|
| `import_boundary_validator.py` (UMRS internal) | SI-01 | Foundation modules don't import from `umrs.api`/`umrs.server` |

## Failure Diagnostics

### `import_boundary_validator.py` fails

**Symptom:** `FAIL: r4/src/... imports umrs.something`

**Fix:** Remove the direct import. Use HTTP or ZMQ wire protocol for cross-package
communication. If you need shared types, define them in `r4-contracts/`.

### `contracts_fence.py` fails

**Symptom:** `FAIL: r4-contracts must have zero [project.dependencies]`

**Fix:** Remove the dependency from `r4-contracts/pyproject.toml`. r4-contracts
must use only Python stdlib. If you need a third-party type, use a protocol or
abstract base class instead.

### `stale_name_validator.py` fails

**Symptom:** `FAIL: Makefile:42: stale 'aisci' package name reference`

**Fix:** Replace `aisci` → `r4`, `aisci-mcp` → `r4-mcp`, `aisci-gateway` → `r4-gateway`
in the referenced file and line.

### `schema_version_tracker.py` fails

**Symptom:** `FAIL: jobs schema version mismatch: code=3, registry=2`

**Fix:** You changed a schema file without updating the registry.
Run `python tools/schema_version_tracker.py --update` to regenerate
`tools/schema_versions.yaml`, then commit the updated registry.

### `dto_equivalence_check.py` fails

**Symptom:** `FAIL: SkillManifest field mismatch: ...`

**Fix:** You changed a DTO in r4-contracts or UMRS without keeping the other
in sync. Update the mirror to match. This check is transitional — it will be
retired when UMRS imports DTOs directly from r4-contracts (convergence Step 5).

### `runtime_support_matrix.py` fails

**Symptom:** `Runtime Support Matrix` reports findings or a composition check
returns `accepted: False`.

**Fix:** Update `tools/runtime_support_matrix.json` so the composition has a
valid support level, smoke command, rollback note, and envelope. Do not expose a
new operator-facing runtime combination without a matrix entry. Experimental and
compatibility paths should stay visible but must not be represented as supported.

## Pre-commit Hooks

Install pre-commit hooks to get local enforcement:

```bash
pip install pre-commit
pre-commit install --hook-type pre-push
```

Hooks run on `git push` (not `git commit`) to avoid slowing down local development.

## CI Integration

The `boundary-check` job in `.github/workflows/ci.yaml` runs all hard-fail
validators on every push and PR. It runs independently of the test jobs and
fails fast.

## Adding New Validators

1. Create `tools/your_validator.py` with a `main() -> int` function (0 = pass, 1 = fail)
2. Create `tests/test_your_validator.py` with a CI gate test
3. Add a step to `.github/workflows/ci.yaml` → `boundary-check` job
4. Optionally add to `.pre-commit-config.yaml`
5. Document in this file
6. Classify as hard-fail or warning-only
