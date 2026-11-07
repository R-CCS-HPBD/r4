# Tools

Architecture enforcement and governance tools for the R4 Platform.

## Enforcement Validators

17 validators (8 hard-fail + 9 advisory), all grade A.

### Hard-Fail (CI gates)

| Tool | Purpose |
|------|---------|
| `import_boundary_validator.py` | Cross-package import boundary enforcement |
| `contracts_fence.py` | Security invariant verification |
| `stale_name_validator.py` | Naming convention drift detection |
| `schema_version_tracker.py` | Schema version consistency |
| `dto_equivalence_check.py` | DTO convergence verification |
| `config_drift_validator.py` | Configuration drift detection |
| `topology_tools.py` | Component topology validation |

### Advisory/Informational

| Tool | Purpose |
|------|---------|
| `enforcement_maturity_scorer.py` | Validator health scoring |

## Usage

```bash
# Run all validators
python tools/import_boundary_validator.py
python tools/contracts_fence.py
python tools/stale_name_validator.py

# Full enforcement regression
.venv/bin/python -m pytest tests/test_import_boundary_validator.py \
  tests/test_contracts_fence.py tests/test_dto_equivalence.py \
  tests/test_schema_version_tracker.py tests/test_stale_name_validator.py \
  tests/test_topology_tools.py -v

# Maturity scorecard
python tools/enforcement_maturity_scorer.py
```

Expected scorecard: `A:17 B:0 C:0 D:0`

## Related

- [CONSTITUTION.md](../CONSTITUTION.md) — §8 Validation After Edits
- [ARCHITECTURE_ENFORCEMENT.md](../docs/ARCHITECTURE_ENFORCEMENT.md) — Validator reference
