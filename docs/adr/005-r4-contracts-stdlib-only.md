# ADR-005: r4-contracts Zero-Dependency Rule

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** INV-10 (r4-contracts has zero dependencies)

## Context

`r4-contracts` is the shared DTO/schema/validator package consumed by three
independent packages: r4 (client SDK), Gateway (API edge), and UMRS (durable
platform). It defines shared data shapes (`WorkflowIR`, `SkillManifest`,
`RecipeDefinition`, security patterns, etc.).

A contributor might add a dependency to `r4-contracts` for convenience (e.g.,
Pydantic for validation, attrs for dataclasses).

## Decision

`r4-contracts` declares **zero** `[project.dependencies]` in its `pyproject.toml`.
It uses only Python stdlib: `dataclasses`, `typing`, `enum`, `re`, `datetime`.

### Why

Any dependency added to `r4-contracts` becomes a **transitive dependency of
every consumer**:

```
r4-contracts + pydantic
  → r4 gets pydantic (already has it, OK)
  → gateway gets pydantic (already has it, OK)
  → umrs gets pydantic (may conflict with UMRS's pydantic version)
```

On HPC environments (RCCS), dependency installation is constrained (offline
containers, Singularity). Every transitive dep increases build complexity.

## Consequences

- All data shapes in `r4-contracts` use `@dataclass`, not Pydantic `BaseModel`
- Validation uses manual `validate()` methods, not Pydantic validators
- Type checking relies on `typing` annotations, not runtime validation
- Enforced by `tools/contracts_fence.py` (validates pyproject.toml)
- Enforced by `tools/import_boundary_validator.py` rule 5 (stdlib-only imports)
- CI gate: `tests/test_contracts_fence.py`

## Alternatives Considered

- **Pydantic BaseModel**: Better validation ergonomics. But adds a ~10MB
  transitive dep tree and version-coupling risk.
- **attrs**: Lighter than Pydantic but still a non-stdlib dependency.
- **stdlib dataclasses + manual validation (chosen)**: Slightly more verbose but
  guarantees zero coupling between consumers.
