# ADR-052: One gate for every tree — changed-files lint/complexity + fingerprint ratchet baselines

**Status:** Accepted
**Date:** 2026-09-14
**Relates to:** structure-hardening-plan-2026-09-14 §D-1 · `scripts/ci-lint-local.sh:162` scope regex · `tools/complexity_audit.py` · `docs/verification/structure-baseline-2026-09-14.md`

## Context

Lint scope today is a regex `^(gateway/sidecar|r4/src|mcp-servers/src)/.*\.py$`
in `scripts/ci-lint-local.sh:162` (cloned into `.github/workflows/ci.yaml:102`),
so every other Python tree is linted by nobody. Measured 2026-09-14 on the
baseline snapshot referenced above: 1284 autofixable findings in `umrs/src`,
636 in `umrs/tests`, 421 in `r4-contracts/src`, 271 in `tests/`, 49 in `tools/`.
Complexity is worse: the CI complexity gate runs on parent changed files and
the parent diff never descends into the umrs submodule, so `complexity_audit
--max-complexity 9` over 650 umrs files reports **270 functions over target,
72 hard warnings, 16 critical** and no gate has ever seen them.

The changed-files gate for umrs is derivable from the two submodule pointers
(base vs head) and would have flagged exactly one true-positive function in
the ADR-051 wave.

## Decision

(i) Lint scope = all `**/*.py` outside `.venv`, `node_modules`, `**/archive/**`.
(ii) The umrs complexity gate runs on umrs files changed BETWEEN the two
submodule pointers (not the parent diff); a new `ci-lint-local.sh
--umrs-changed` mode and an equivalent `test-umrs` step in CI wire it.
(iii) Pre-existing violations land in fingerprint baselines
(`tools/complexity_baseline.py`, JSON entries of `{file, function, complexity,
fingerprint}` where fingerprint = `sha256 of rule|file|function-or-line-text`,
reusing `doc_sanity_gate._fingerprint` semantics).
(iv) **Ratchet**: a gate fails on any violation not in the baseline, fails if
the baseline count increases, and `--rebaseline` only ever shrinks the count.
The baseline is a migration record, never an exemption.

### Why

270 functions is a rewrite-scale change with no behavioural payoff; a ratchet
makes every future touch of a hot function pay down debt while blocking new
debt immediately. Mechanical autofix commits precede scope extension, so the
gate switches on green. Permanent exemptions are forbidden by CONSTITUTION §8.

## Consequences

- Every Python tree of the monorepo (including umrs) is held to the same
  standard on changed files; legacy debt is a tracked, shrinking surface.
- A function over the default needs a ≤-15-minute extraction; never a
  ethical-flag-up.
- NOT in scope: rewriting the 270 functions inside this wave (the ratchet
  pays them down). A `--rebaseline` upward is never legitimate.

## Alternatives Considered

- **Big-bang fix of 270 functions** — rejected: no behavioural value, collides
  with concurrent umrs waves.
- **Permanent exemptions** — rejected: CONSTITUTION §8 forbids them.

## Rollout-status (2026-09-14)

- Proposed; execution per structure-hardening-plan-2026-09-14.md §6 (Phases
  0 and 3).
- Same-day landing, step 3.3 (branch `structure/hardening-2026-09`):
  `tools/complexity_baseline.py` with AST-fingerprinted entries and strict
  ratchet semantics (new pair / grew / count-increase all fail;
  `--rebaseline` refuses when the count grew) + recorded baseline of
  **424 over-threshold pairs** for `umrs/src umrs/tests` + 11 pins +
  scorer row (`A:23 B:0`, 375 validator tests) + the `--umrs-changed`
  changed-files gate in `scripts/ci-lint-local.sh` (ruff `F` through
  per-file loop + complexity THROUGH the baseline checker — C901 is not
  double-gated) + `test-umrs` CI step on
  `${{ pull_request.base.sha || event.before }}` + CONSTITUTION §8
  umrs-gate sentence (N-from-scorer form). The wave-changed ruff `--fix`
  pass inside umrs (33 files) repaired one real latent F821 the gate
  caught. Sequence diverged from plan text on purpose: 3.3 landed BEFORE
  3.1 because the changed-file gate would otherwise have been red from
  birth (270+ pre-existing hotspots); 3.1's full-tree autofix and 3.4's
  lint baseline for the 112 non-mechanical findings in the changed set
  (BLE001×30, B008×27, …) remain open, and the changed-files gate widens
  to full-select when 3.4's lint baseline exists.
- Step 3.1 + 3.2 the same day: the mechanical autofix waves swept umrs
  (four grouped commits: `97547556`/`5f5cf5be`/`71bdfff8`/`d382c247`,
  ~1,950 fixes) and the parent trees (`5569f5e2`, ~700 fixes + manual
  residue); every suite re-verified per group with **zero** net
  regressions past four caught autofix-breaks, each restored with
  load-bearing reason comments (`api/llm.py` set-mutated-in-loop,
  `server/otel.py` byte-parity vs the sidecar copy — twice,
  `model_registry_handler._catalog_path` re-export,
  `job_tracking_handler` psycopg2 patch targets; plus one
  adjacent-literal-concat misfire in `r4/errors.py` caught by 19
  auth-hint pins and repaired inline). Whole-umrs lint residue after the
  waves: **3,782 → 1,927 findings** (−49%, now all non-mechanical
  classes: BLE001×607, B008×264, ARG002×209, ARG005×142, S110×110, …);
  the complexity baseline stayed flat-by-design (424 → 424, autofix
  does not change CCN). Lint scope widened to every changed `.py`
  outside vendor dirs in `ci-lint-local.sh` + CI Lint job (`7e5413e9`).
- Step 3.4 landed the same day: sibling `tools/lint_baseline.py` —
  ruff-JSON collector, multiset ratchet keyed `(file, code,
  line-content fingerprint)` with whitespace-normalized hashing so line
  shifts above a flagged line stay silent while content edits on the
  flagged line itself become fresh findings (both behaviors pinned).
  Whole-tree baseline for `umrs/src umrs/tests`: **3,782 findings** —
  top classes BLE001×607, UP045×431, I001×372, F401×323, UP006×291,
  B008×264, ARG002×209 (autofixable classes I001/F401/UP* are the 3.1
  full-tree wave's target; non-mechanical classes stay until their
  dedicated refactors). 15 pins; scorer now **A:24 B:0 / 24 hard /
  390 tests**. The `--umrs-changed` gate now runs the FULL ruff select
  through this baseline (plus the complexity baseline) — a fresh
  BLE001/S110 planted in a baselined file fails exit 1 (mutation probe
  captured and reverted); the gate's 14-changed-file window shows
  79 baselined findings, all quiet.

## Related

- [ADR-053](053-umrs-import-discipline.md)
- structure-hardening-plan-2026-09-14.md
