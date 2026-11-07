# ADR-054: umrs as a uv workspace member + test tiers by marker

**Status:** Accepted
**Date:** 2026-09-14
**Relates to:** structure-hardening-plan-2026-09-14 §D-4/D-5 · `pyproject.toml` ([tool.uv.workspace], [tool.pytest.ini_options]) · `docs/verification/structure-baseline-2026-09-14.md`

## Context

`.venv` has an umrs 0.1.0 install record, yet `python -c "import umrs"` from
the repo root resolves to `_NamespacePath(['…/umrs'])` — the submodule
DIRECTORY, not a package, because `a2l`/`umrs` stay under
`[tool.uv.workspace].exclude`. Every suite and every CI job therefore carries
`PYTHONPATH=umrs/src`, and `pyproject.toml` still names `a2l/src` (deleted
2026-09-11). `umrs/src/pyproject.toml` already has its setuptools build +
extras (`client`, `client-full`, `server`, `otel`, `all`, `api-test`) already
heavy-gated — the packaging exists; only the workspace wiring is missing.

The test-tier picture next door: `integration`, `e2e`, `hpc`, `k8s` markers
are declared in `pyproject.toml [tool.pytest.ini_options].markers` and never
act. Skipping E2E used to happen by **filename lists** in `tests/conftest.py`;
three heavyweight files (`test_k8s_e2e.py`, `test_custom_mcp_e2e.py`,
`test_hpc_gateway_e2e.py`) had no guard at all and hung the belt indefinitely
until the 2026-09-14 fixes.

## Decision

1. `umrs/src` joins `[tool.uv.workspace].members` (light defaults; the ML
   stack stays behind the opt-in `server` extra). `uv.lock` regenerated. The
   default `uv sync` succeeds without torch.
2. `PYTHONPATH` is removed from `[tool.pytest.ini_options].pythonpath`,
   `.github/workflows/ci.yaml` every job, `scripts/ci-lint-local.sh`, and this
   plan's §0.4/§0.6 text. Dead `a2l/src` path is dropped with it. CI jobs
   install umrs editable through the regular dependency path instead.
3. **Test tiers by marker**: one `pytest_collection_modifyitems` rule —
   items marked `e2e`, `k8s`, `hpc`, or `integration` are skipped unless
   `R4_RUN_E2E=1` (or the item's module-specific `R4_E2E_*` flag allows).
   The filename-list skips disappear once every named file carries a marker;
   new heavyweight tests opt in the SAME way.
4. **Repo-root belt charter**: `tests/` stays the enforcement belt
   (validators, drift detectors, topology, contract rails). Product-specific
   test files (`import umrs.*` / `import r4.*`) move to their package's
   `tests/` directories.

### Why

An `import umrs` that resolves to a bare submodule directory is broken by
definition: it shadows the package, masks import defects, and forces
PYTHONPATH everywhere. Test tiers become a single mechanism a new heavyweight
test needs: a marker, not boilerplate.

## Consequences

- `import umrs` works from anywhere with the package installed; every CI
  harness row simplifies.
- New heavyweight tests are opt-in via markers; none die by filename again.
- NOT in scope: renaming the submodule path (shadowing gone without it);
  changing any test's semantics.

## Alternatives Considered

- **Rename umrs → vendor dir shape** — rejected: member wiring already
  fixes the shadow without renaming anything.
- **Keep filename lists** — rejected: files rot between the harness renames
  and the new test naming conventions; markers scale.

## Rollout-status (2026-09-14)

- Proposed; execution per structure-hardening-plan-2026-09-14.md §6 Phase 2.
- Packaging half landed (same day, branch `structure/hardening-2026-09`):
  `umrs/src` is a workspace member (`uv sync` default = light: 0 torch,
  verified in scratch venvs; version bumped to `0.2.0rc1` umrs `33bdcc90`);
  root `client` extra forwards to `umrs[client]` (uv 0.9.5 reads only the
  root table for `--extra`); root `all` extra dropped the retired
  `r4-gateway` (pre-existing unresolvable discovered by `uv lock
  --dry-run`); `Makefile` uv lanes call `uv sync`; **every** `PYTHONPATH`
  site removed from `ci.yaml` (5 env sites; four jobs install
  `-e ./umrs/src` instead), `pyproject.toml` pytest pythonpath (dropped
  `a2l/src`, `umrs/src`) and coverage source (`gateway` →
  `gateway/sidecar`), `scripts/ci-lint-local.sh` umrs leg. Suites verified
  with `env -u PYTHONPATH`: root belt 1308 / r4 3161 / umrs 4936, all 0
  failed.
- Test-tier half landed (same day): single opt-in rule in
  `tests/conftest.py` (markers e2e/k8s/hpc/integration skip unless
  `R4_RUN_E2E=1` or the file's own `R4_E2E_<TIER>` variable; capability
  detection orthogonal), filename-list skips replaced by module
  `pytestmark`, belt charter at `tests/README.md`, four product-specific
  files moved to `r4/tests/` (`test_team_bench`, `test_team_resolver`,
  `test_hpc_gateway_e2e`, `test_kb_roundtrip_e2e`) — commits `d7a25071`
  (rule + markers + moves) and `2b2837f2` (reference repoints:
  traceability.yaml + deployment doc). Belt collected 1498→1474 = exactly
  the moved files; belt 1275/0-failed; r4 3185/0-failed. Charter refines
  the §6.2 literal `grep=0` acceptance: contract rails (batch3–8,
  control_schema, provenance_diff, validator pins) stay in the belt by
  design. **Phase 2 COMPLETE** (2.1–2.5 all landed).

## Related

- [ADR-052](052-one-gate-every-tree.md)
- [ADR-053](053-umrs-import-discipline.md)
- structure-hardening-plan-2026-09-14.md
