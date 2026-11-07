# Pre-Push Validation Guide

Run these checks locally before pushing to replicate what CI will enforce.
All commands assume you are in the repo root unless otherwise noted.

---

## 1. Format and Lint (Python)

CI blocks on both ruff lint errors and format violations.

```bash
# Auto-fix formatting and fixable lint issues
make format

# Check only (mirrors CI)
uv run ruff format --check .
uv run ruff check .
```

If you changed only a subset of files:

```bash
uv run ruff check path/to/file.py
uv run ruff format --check path/to/file.py
```

---

## 2. Complexity Gate

CI rejects functions with cyclomatic complexity > 9.
Applies to Python, TypeScript, and shell scripts.

```bash
# Python — custom audit tool
python tools/complexity_audit.py --max-complexity 9 path/to/file.py

# Python — ruff McCabe gate
uv run ruff check --select C901 --config 'lint.mccabe.max-complexity=9' path/to/file.py

# TypeScript / polyglot — requires lizard
pip install lizard
lizard -C 9 vscode-extension/src/auth.ts \
             vscode-extension/src/extension.ts \
             vscode-extension/src/gateway-client.ts
```

---

## 3. Architecture Boundary Checks

Run after editing any Python file. CI hard-fails on violations.

```bash
python tools/import_boundary_validator.py
python tools/config_drift_validator.py
python tools/contracts_fence.py
python tools/stale_name_validator.py
python tools/schema_version_tracker.py
python tools/dto_equivalence_check.py
python tools/runtime_support_matrix.py
```

Or run the full enforcement regression suite (17 validators, ~261 tests):

```bash
.venv/bin/python -m pytest tests/test_import_boundary_validator.py \
  tests/test_contracts_fence.py tests/test_dto_equivalence.py \
  tests/test_schema_version_tracker.py tests/test_stale_name_validator.py \
  tests/test_topology_tools.py -v

python tools/enforcement_maturity_scorer.py   # expected: A:17 B:0 C:0 D:0
```

The scorer also prints **exemption ages** (≥90-day-old exemptions flagged
for review); a validator graduates advisory → hard-fail only after its
flagged exemptions are re-justified or removed. See
[validator promotion process](VALIDATOR_PROMOTION.md).

Trigger-specific validators:

| Changed file(s) | Extra validator |
|---|---|
| `umrs/src/umrs/*/schema.py` | `python tools/schema_version_tracker.py` |
| `r4-contracts/` or `umrs/src/umrs/skills/` or `recipes/` | `python tools/dto_equivalence_check.py` |
| `deploy/`, `umrs/deploy/`, `site.yaml`, `.env` | `python tools/config_drift_validator.py` |
| `mcp-servers/src/r4_mcp/runners/custom_mcp.py` or `security.py` | `.venv/bin/python -m pytest mcp-servers/tests/test_extensibility_security.py mcp-servers/tests/test_custom_mcp.py -v` |

---

## 4. Unit Tests

### UMRS

```bash
# From repo root — mirrors CI exactly (excludes the rag pipeline)
PYTHONPATH=umrs/src \
  .venv/bin/python -m pytest umrs/tests/ -v \
  --ignore=umrs/tests/test_rag_pipeline.py
```

> Note: CI also starts a Postgres instance and MinIO. Most unit tests mock
> these, but a few require a live DB. If tests fail locally with connection
> errors that pass in CI, that is expected — those tests are integration-only.

### Gateway

```bash
cd gateway
GATEWAY_UMRS_URL=http://localhost:8000 \
  GATEWAY_LITELLM_SERVICE_KEY=sk-test-key \
  ../.venv/bin/python -m pytest tests/ -v
```

### r4 SDK

```bash
cd r4
../.venv/bin/python -m pytest tests/ -v
```

### MCP Servers

```bash
cd mcp-servers
../.venv/bin/python -m pytest tests/ -v
```

### All packages (unit tests only, no services required)

```bash
make test-unit
```

---

## 5. TypeScript Compilation

```bash
cd vscode-extension
npx tsc --noEmit
```

Zero errors expected.

---

## 6. Quick Pre-Push Checklist

Before `git push`:

```bash
# 1. Fix formatting
make format

# 2. Boundary validators
python tools/import_boundary_validator.py
python tools/contracts_fence.py
python tools/stale_name_validator.py

# 3. UMRS tests (most critical for umrs/ changes)
PYTHONPATH=umrs/src .venv/bin/python -m pytest umrs/tests/ \
  --ignore=umrs/tests/test_rag_pipeline.py \
  -q

# 4. Relevant component tests for what you changed
# (gateway/, r4/, mcp-servers/ — see section 4 above)
```

---

## 7. Submodule Changes

The `umrs/` directory is a submodule tracked on branch `ai4s-platform`.
When you change files under `umrs/`, you must commit and push the submodule
**before** updating the parent repo:

```bash
# 1. Commit inside submodule
cd umrs
git add -A
git commit -m "fix: ..."
git push origin ai4s-platform

# 2. Update parent repo to reference the new submodule commit
cd ..
git add umrs
git commit -m "chore: update umrs submodule"
git push origin <branch>
```

CI clones the parent repo and then runs `git submodule update --init`.
If the submodule commit is not pushed, CI will fail with a checkout error.

---

## 8. Architecture Health Report (optional)

```bash
make arch-health            # human-readable
make arch-health-json       # machine-readable JSON
make arch-health-verbose    # detailed per-validator output
```

Expected output: all 17 validators grade A.
