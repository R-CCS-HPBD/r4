---
description: Local CI harness — MUST run scripts/ci-lint-local.sh before every commit
applyTo: '**'
---

# Local CI Harness Rule

> *(2026-11-07)* This file describes the development repository's process. Public contributors follow [CONTRIBUTING.md](../../CONTRIBUTING.md).

Updated 2026-09-22 (design reset): scope, stages and modes re-verified
against `scripts/ci-lint-local.sh` and `.github/workflows/ci.yaml`
(ADR-052 "one gate for every tree").

## CRITICAL: Pre-Commit CI Gate

Before EVERY commit that modifies Python files — in **any** tree, including
the `umrs` submodule — you MUST run:

```bash
bash scripts/ci-lint-local.sh
```

It must print `ALL CHECKS PASSED`. Never relax a gate, validator, threshold
or baseline to make it pass (CONSTITUTION §7).

## Scope (ADR-052)

- **Lint scope = every changed `.py` in every tree** (`r4/`, `gateway/`,
  `mcp-servers/`, `r4-contracts/`, `tools/`, `tests/`, `umrs/`, …), excluding
  only `.venv/`, `node_modules/`, `**/archive/**` and
  Test files are in scope. The pre-ADR-052 regex
  `^(gateway/sidecar|r4/src|mcp-servers/src)/.*\.py$` no longer exists
  (one stale pass-message in the script still names those three dirs).
- **Pre-existing debt lives in fingerprint baselines** that only shrink:
  `tools/lint_baseline_umrs.json` (ruff findings, checked by
  `tools/lint_baseline.py`), `tools/complexity_baseline_umrs.json` and
  `tools/complexity_baseline_parent.json` (over-threshold functions, checked
  by `tools/complexity_baseline.py`). A check fails on any new finding, any
  grown function, or any count increase; `--rebaseline` refuses to grow a
  baseline. A baseline is a migration record, not an exemption.
- Changing the gate scope or adding a baseline file is an
  architecture-review change (CONSTITUTION §5.2).

## Stages (what the script runs)

0. **Secret scanning** — regexes for API keys, JWTs, `sk-…`, AWS and GitHub
   tokens over staged, unstaged and committed-vs-base diffs. Runs even when
   no Python file changed.
1. **Lint** — `ruff check` + `ruff format --check` on the lint-scope files
   (ruff pinned to 0.16.6 in CI; local and CI must agree).
2. **Complexity gate** — `tools/complexity_baseline.py check` (umrs files
   against the umrs baseline, everything else against the parent baseline;
   hard-fail); `ruff --select C901` (max 9) and
   `xenon --max-absolute B --max-modules B --max-average A` are informational
   after ADR-052; `lizard -C 9` on py/js/ts/rs/go/c/cpp files (hard-fail
   locally, informational in CI); `shellcheck` on changed `.sh/.bash/.zsh`
   files (hard-fail locally and in CI, which installs `shellcheck-py`).
3. **Boundary check** — full-repo validators, hard-fail:
   `import_boundary_validator`, `test_import_hygiene_validator` (ADR-053),
   `config_drift_validator` (development repository only), `contracts_fence`,
   `stale_name_validator`,
   `schema_version_tracker`, `dto_equivalence_check`,
   `runtime_support_matrix`, `knowledge_plane_fence`,
   `adr_index_uniqueness_validator`, `traceability_validator`,
   `exemption_governance_validator`, `security_compat_scanner`,
   `tool_route_liveness`, `bridge_detector`, `transport_boundary_audit`,
   `dead_compat_bridge_validator`, `check_metrics_coverage`,
   `adr_linkage_validator`, `orphan_module_detector`, and
   `doc_sanity_gate.py` over `docs/adr/**` (strict) and
   `docs/development/**` (baselined).
4. **Tests** — component-scoped pytest: `r4/tests/` when `r4/` changed,
   `gateway/tests/`, `mcp-servers/tests/`, and `umrs/tests/` (run with
   `cwd=umrs` to mirror CI `test-umrs`; ignores
   `tests/test_rag_pipeline.py`, plus the `datetime.UTC` tests on
   Python < 3.11) when `umrs/` changed — including when only the submodule
   pointer moved or the submodule worktree is dirty. `--full` runs all four.

E2E lanes are not part of this script; they opt in with `R4_RUN_E2E=1`
(ADR-054).

### When to Run

- **Before every `git commit`** that touches `*.py` (or `*.sh`) files
- **After any edit** to Python source, test, or tooling files
- **Before pushing** to any branch
- **After bumping the `umrs` submodule pointer** — also run `--umrs-changed`

### If the Script Fails

1. Read the output carefully — it shows exactly which check failed
2. Fix the issue (auto-fix available: `ruff check --fix`, `ruff format`)
3. Re-run `bash scripts/ci-lint-local.sh` until it passes
4. Only then proceed with the commit

### Script Modes

```bash
# Default: changed files vs origin/main (committed + uncommitted + untracked)
bash scripts/ci-lint-local.sh

# Check vs a different base
bash scripts/ci-lint-local.sh --base origin/develop

# Check specific files
bash scripts/ci-lint-local.sh --files r4/src/r4/foo.py umrs/src/umrs/bar.py deploy/x.sh

# Full-repo scan (all Python and shell files)
bash scripts/ci-lint-local.sh --full

# umrs submodule delta gate (ADR-052)
bash scripts/ci-lint-local.sh --umrs-changed [--base <ref>]
```

`--umrs-changed`: the parent diff never descends into the submodule, so this
mode diffs `<base>:umrs..HEAD:umrs` (plus the submodule's unstaged edits) and
runs `tools/lint_baseline.py check` and `tools/complexity_baseline.py check`
on exactly those files, then exits. CI runs the same command in `test-umrs`
before the umrs suite, so a function born over threshold inside an umrs
commit fails fast there too.

### Known Exclusions

- IPython-dependent tests are auto-ignored when IPython is not installed:
  `r4/tests/test_companion/test_jupyter_magic.py`, `r4/tests/test_jupyter.py`,
  `r4/tests/test_package_imports.py`.
- `xenon`, `lizard`, `shellcheck` missing locally produce a warning, not a
  pass — install them (`uv pip install radon xenon lizard`, `shellcheck`) so
  the local run mirrors CI.

## DO NOT

- Skip running the script because "it's a small change"
- Commit when the script exits non-zero
- Suppress failures with `|| true` or `2>/dev/null`
- Rebaseline upward, widen an exclusion, or move a file into an excluded
  path to dodge a finding
