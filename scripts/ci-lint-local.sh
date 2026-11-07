#!/usr/bin/env bash
# ==============================================================================
# ci-lint-local.sh — Run CI-equivalent checks on changed files locally
#
# Mirrors what .github/workflows/ci.yaml runs for:
#   1. Lint (ruff check + format --check)
#   2. Complexity gate (complexity_audit.py + ruff C901 + xenon; lizard for
#      polyglot files; shellcheck for shell files)
#   3. Boundary check (all mutation-safety validators)
#   4. Tests (r4, gateway, mcp, umrs — scoped to changed components)
#
# Usage:
#   ./scripts/ci-lint-local.sh                  # changed vs origin/main
#   ./scripts/ci-lint-local.sh --base develop   # changed vs origin/develop
#   ./scripts/ci-lint-local.sh --base HEAD~3    # changed in last 3 commits
#   ./scripts/ci-lint-local.sh --files a.py b.py # specific files
#   ./scripts/ci-lint-local.sh --full            # full-repo check
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

FAILED=0
WARNINGS=0

pass()  { echo -e "  ${GREEN}✓${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*"; FAILED=$((FAILED + 1)); }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; WARNINGS=$((WARNINGS + 1)); }

# ── resolve Python ────────────────────────────────────────────────────────────
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        PYTHON="$VIRTUAL_ENV/bin/python"
    elif [ -f ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="python3"
    fi
fi
# Absolutize: subshell legs (the umrs run below) chdir before invoking.
if [[ "$PYTHON" != /* ]]; then
    if [[ "$PYTHON" == */* ]]; then
        PYTHON="$PWD/$PYTHON"
    else
        PYTHON="$(command -v "$PYTHON")"
    fi
fi

RUFF="${RUFF:-}"
if [ -z "$RUFF" ]; then
    RUFF="$(dirname "$PYTHON")/ruff"
    [ -x "$RUFF" ] || RUFF="ruff"  # fallback to PATH
fi

# ── resolve changed files ─────────────────────────────────────────────────────
BASE="origin/main"
FULL=0
SPECIFIC_FILES=()

UMRS_CHANGED=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base) BASE="$2"; shift 2 ;;
        --files) shift; while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do SPECIFIC_FILES+=("$1"); shift; done ;;
        --full) FULL=1; shift ;;
        --umrs-changed) UMRS_CHANGED=1; shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

# ── umrs changed-files gate (ADR-052): lint + complexity-through-baseline on
# the submodule's own delta ($BASE:umrs .. HEAD:umrs), never the parent-only
# pointer. Parent diffs never reach inside the submodule; this is the only
# lane that catches an over-threshold function born inside an umrs commit.
if [ $UMRS_CHANGED -eq 1 ]; then
    OLD_UMRS=$(git rev-parse "$BASE":umrs 2>/dev/null || echo "")
    NEW_UMRS=$(git rev-parse HEAD:umrs 2>/dev/null || echo "")
    if [ -z "$OLD_UMRS" ] || [ -z "$NEW_UMRS" ]; then
        echo -e "${YELLOW}umrs pointer unreadable at $BASE/HEAD — resolved to worktree diff only${NC}"
        OLD_UMRS="HEAD"; NEW_UMRS=""
    fi
    UFILES=$(git -C umrs diff --name-only --diff-filter=ACMR "$OLD_UMRS" "${NEW_UMRS:-HEAD}" -- '*.py' 2>/dev/null || true)
    UFILES_UNSTAGED=$(git -C umrs diff --name-only --diff-filter=ACMR -- '*.py' 2>/dev/null || true)
    ALL_U=$(printf '%s\n%s\n' "$UFILES" "$UFILES_UNSTAGED" | sed '/^$/d' | sort -u)
    if [ -z "$ALL_U" ]; then
        echo -e "${GREEN}✓${NC} umrs changed-files gate: no changed .py under umrs/"
        exit 0
    fi
    echo -e "${GREEN}[umrs-changed vs $OLD_UMRS]${NC} $(echo "$ALL_U" | wc -l) file(s)"
    FAILED=0
    # sed line-prefix stays clearer than ${var//} prose here.
    # shellcheck disable=SC2001
    REL_U=$(echo "$ALL_U" | sed 's|^|umrs/|')
    # Full ruff select on changed files, but STRICTLY through the lint
    # baseline (ADR-052 F-3): pre-existing residue is a shrinking ratchet,
    # not a new-finding license. Complexity likewise goes through its own
    # baseline — C901 must not double-gate functions the baseline recorded.
    # REL_U is a multipath newline list; unquoted expansion here is the design.
    # shellcheck disable=SC2086
    if ! "$PYTHON" tools/lint_baseline.py check $REL_U; then
        FAILED=1
    fi
    # shellcheck disable=SC2086
    if ! "$PYTHON" tools/complexity_baseline.py check $REL_U; then
        FAILED=1
    fi
    [ $FAILED -eq 0 ] && echo -e "${GREEN}umrs changed-files gate PASSED${NC}" || echo -e "${RED}umrs changed-files gate FAILED${NC}"
    exit $FAILED
fi

CHANGED_PY=()
CHANGED_SH=()
CHANGED_POLY=()   # CI lizard scope: py + js/ts/rs/go/c/cpp

CHANGED_ALL=()

categorize_file() {
    local f="$1"
    [ -f "$f" ] || return 0
    CHANGED_ALL+=("$f")
    case "$f" in
        *.py) CHANGED_PY+=("$f"); CHANGED_POLY+=("$f") ;;
        *.js|*.jsx|*.ts|*.tsx|*.rs|*.go|*.c|*.cc|*.cpp|*.h|*.hpp) CHANGED_POLY+=("$f") ;;
        *.sh|*.bash|*.zsh) CHANGED_SH+=("$f") ;;
    esac
}

if [ $FULL -eq 1 ]; then
    echo -e "${YELLOW}[full-repo mode]${NC} scanning all files..."
    while IFS= read -r -d '' f; do
        categorize_file "$f"
    done < <(find . \( -name '*.py' -o -name '*.sh' \) -not -path './.venv/*' -not -path './.git/*' -not -path '*/node_modules/*' -print0)
elif [ ${#SPECIFIC_FILES[@]} -gt 0 ]; then
    for f in "${SPECIFIC_FILES[@]}"; do categorize_file "$f"; done
else
    echo -e "${GREEN}[diff vs $BASE]${NC} detecting changed files..."
    while IFS= read -r path; do
        [ -z "$path" ] && continue
        categorize_file "$path"
    done < <({
        # committed delta vs base
        git diff --name-only --diff-filter=ACMR "$BASE" HEAD 2>/dev/null
        # uncommitted tracked edits (staged + unstaged)
        git diff --name-only --diff-filter=ACMR HEAD 2>/dev/null
        # untracked files
        git ls-files --others --exclude-standard 2>/dev/null
    } | sort -u || true)
fi

TOTAL_CHANGED=$(( ${#CHANGED_PY[@]} + ${#CHANGED_SH[@]} + ${#CHANGED_POLY[@]} ))

# ═══════════════════════════════════════════════════════════════════════════════
# 0. SECRET SCANNING (Gap 6 — runs unconditionally, even for non-Python files)
# ═══════════════════════════════════════════════════════════════════════════════
echo -e "${GREEN}── 0. Secret Scanning ${NC}"

SECRET_PATTERNS="api[_-]?key\\s*[:=]\\s*[A-Za-z0-9_\\-]{20,}|eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}"
SECRETS_FOUND=0

# Check staged changes
if git diff --cached | grep -E "^\+" | grep -qE "$SECRET_PATTERNS" 2>/dev/null; then
    echo -e "  ${RED}Secret detected in staged changes:${NC}"
    git diff --cached | grep -E "^\+" | grep -E "$SECRET_PATTERNS" | head -5
    fail "secret scanning (staged)"
    SECRETS_FOUND=1
fi

# Check unstaged changes
if git diff | grep -E "^\+" | grep -qE "$SECRET_PATTERNS" 2>/dev/null; then
    echo -e "  ${RED}Secret detected in unstaged changes:${NC}"
    git diff | grep -E "^\+" | grep -E "$SECRET_PATTERNS" | head -5
    fail "secret scanning (unstaged)"
    SECRETS_FOUND=1
fi

# Check committed changes vs base (matches the range CI sees on push/PR)
if [ $FULL -eq 0 ] && git rev-parse --verify --quiet "$BASE" >/dev/null 2>&1; then
    if git diff "$BASE"...HEAD 2>/dev/null | grep -E "^\+" | grep -qE "$SECRET_PATTERNS" 2>/dev/null; then
        echo -e "  ${RED}Secret detected in commits vs $BASE:${NC}"
        git diff "$BASE"...HEAD | grep -E "^\+" | grep -E "$SECRET_PATTERNS" | head -5
        fail "secret scanning (committed vs $BASE)"
        SECRETS_FOUND=1
    fi
fi

if [ $SECRETS_FOUND -eq 0 ]; then
    pass "secret scanning (no secrets in diff)"
fi

# Early exit only if nothing changed (secret scanning already ran)
if [ "$TOTAL_CHANGED" -eq 0 ] && [ $FULL -eq 0 ] && [ ${#SPECIFIC_FILES[@]} -eq 0 ]; then
    echo -e "${GREEN}No changed Python/shell files — lint/complexity/tests skipped.${NC}"
    echo ""
    echo "============================================================"
    if [ $FAILED -eq 0 ]; then
        echo -e "  ${GREEN}ALL CHECKS PASSED${NC}  (${WARNINGS:-0} warnings)"
        echo "============================================================"
        exit 0
    else
        echo -e "  ${RED}${FAILED} CHECKS FAILED${NC}  (${WARNINGS:-0} warnings)"
        echo "============================================================"
        exit 1
    fi
fi

# ── lint-scope files: every changed .py outside vendored/archived trees ─────
# (ADR-052 step 3.2 — scope widened from gateway/sidecar|r4/src|mcp-servers/src
# to all trees; vendor dirs + umrs legacy demo dir stay excluded.)
LINT_FILES=()
if [ ${#CHANGED_PY[@]} -gt 0 ]; then
    for f in "${CHANGED_PY[@]}"; do
        case "$f" in
            .venv/*|*/node_modules/*|*/archive/*) continue ;;
        esac
        [ -f "$f" ] && LINT_FILES+=("$f")
    done
fi

echo ""
echo "============================================================"
echo "  Local CI Gate — $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo "  Changed  .py files: ${#CHANGED_PY[@]}"
echo "  Changed  .sh files: ${#CHANGED_SH[@]}"
echo "  Lint-scope files:   ${#LINT_FILES[@]}"
echo "  Base ref:           $BASE"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. LINT (ruff check + ruff format --check)
# ═══════════════════════════════════════════════════════════════════════════════
echo -e "${GREEN}── 1. Lint ${NC}"
if [ ${#LINT_FILES[@]} -gt 0 ]; then
    echo "  Running ruff check..."
    if "$RUFF" check "${LINT_FILES[@]}" --output-format=github 2>/dev/null; then
        pass "ruff check"
    else
        # Re-run visible for diagnostics
        "$RUFF" check "${LINT_FILES[@]}"
        fail "ruff check (lint errors above)"
    fi

    echo "  Running ruff format --check..."
    if "$RUFF" format --check "${LINT_FILES[@]}" 2>/dev/null; then
        pass "ruff format --check"
    else
        echo "  Files that need formatting:"
        "$RUFF" format --check --diff "${LINT_FILES[@]}" 2>/dev/null || true
        fail "ruff format --check (run 'ruff format <file>' to fix)"
    fi
else
    pass "lint (no changed files in r4/src, gateway/sidecar, or mcp-servers/src)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 2. COMPLEXITY GATE
# ═══════════════════════════════════════════════════════════════════════════════
echo -e "${GREEN}── 2. Complexity Gate ${NC}"
if [ ${#CHANGED_PY[@]} -gt 0 ]; then
    # complexity_audit.py
    echo "  Running complexity ratchet (baseline-backed; ADR-052)..."
    UMRS_FILES=$(printf '%s
' "${CHANGED_PY[@]}" | grep "^umrs/" || true)
    PARENT_FILES=$(printf '%s
' "${CHANGED_PY[@]}" | grep -v "^umrs/" || true)
    FAILED_C=0
    # Changed files are newline lists; unquoted expansion feeds the checker.
    if [ -n "$UMRS_FILES" ]; then
        # shellcheck disable=SC2086
        if ! "$PYTHON" tools/complexity_baseline.py check $UMRS_FILES; then FAILED_C=1; fi
    fi
    if [ -n "$PARENT_FILES" ]; then
        # shellcheck disable=SC2086
        if ! COMPLEXITY_BASELINE_FILE=tools/complexity_baseline_parent.json "$PYTHON" tools/complexity_baseline.py check $PARENT_FILES; then FAILED_C=1; fi
    fi
    if [ $FAILED_C -ne 0 ]; then
        fail "complexity baseline: new/grown over-threshold functions in changed files"
    else
        pass "complexity baseline (changed files all inside baselines; ratchet shrinks only)"
    fi

    # ruff C901 (McCabe) — informational since the complexity baseline
    # became the ratchet for pre-existing debt (ADR-052; do NOT fail on
    # baselined entries: a new >9 function still fails through the
    # baseline stage above).
    echo "  Running ruff McCabe check (C901, max=9, informational)..."
    if "$RUFF" check --select C901 --config 'lint.mccabe.max-complexity=9' "${CHANGED_PY[@]}" 2>/dev/null; then
        pass "ruff McCabe C901"
    else
        "$RUFF" check --select C901 --config 'lint.mccabe.max-complexity=9' "${CHANGED_PY[@]}" 2>&1 || true
        warn "ruff McCabe C901 (informational; baselined entries are the ratchet's targets)"
    fi

    # xenon absolute-B gate (CI: xenon --max-absolute B --max-modules B --max-average A)
    XENON="$(dirname "$PYTHON")/xenon"
    [ -x "$XENON" ] || XENON="xenon"
    if command -v "$XENON" >/dev/null 2>&1; then
        echo "  Running xenon (absolute B / modules B / average A)..."
        if "$XENON" --max-absolute B --max-modules B --max-average A "${CHANGED_PY[@]}" 2>&1; then
            pass "xenon"
        else
            warn "xenon absolute-B (informational after ADR-052 baseline)"
        fi
    else
        warn "xenon not installed — CI absolute-B gate not mirrored (uv pip install radon xenon)"
    fi
else
    pass "complexity (no changed .py files)"
fi

# ── polyglot complexity (CI: lizard -C 9 on py/js/ts/rs/go/c/cpp) ────────────
if [ ${#CHANGED_POLY[@]} -gt 0 ]; then
    LIZARD="$(dirname "$PYTHON")/lizard"
    [ -x "$LIZARD" ] || LIZARD="lizard"
    if command -v "$LIZARD" >/dev/null 2>&1; then
        echo "  Running lizard polyglot gate (CCN ≤ 9)..."
        if "$LIZARD" -C 9 "${CHANGED_POLY[@]}" >/dev/null 2>&1; then
            pass "lizard polyglot gate"
        else
            "$LIZARD" -C 9 "${CHANGED_POLY[@]}" 2>&1 | tail -20 || true
            # CI runs lizard informational since ADR-052 (`|| true`, ci.yaml):
            # legacy CCN>9 rows are the ratchet's baseline, which gates above.
            warn "lizard polyglot report: CCN > 9 above (informational, as in CI; the ratchet gates)"
        fi
    else
        warn "lizard not installed — CI polyglot gate not mirrored (uv pip install lizard)"
    fi
fi

# ── shellcheck (CI gates changed shell files) ─────────────────────────────
if [ ${#CHANGED_SH[@]} -gt 0 ]; then
    if command -v shellcheck >/dev/null 2>&1; then
        echo "  Running shellcheck..."
        if shellcheck "${CHANGED_SH[@]}"; then
            pass "shellcheck (${#CHANGED_SH[@]} files)"
        else
            fail "shellcheck (findings above — CI gates changed shell files)"
        fi
    else
        warn "shellcheck not installed — ${#CHANGED_SH[@]} changed shell files NOT checked locally (CI WILL gate them; brew install shellcheck)"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 3. BOUNDARY CHECK (mutation-safety validators, full-repo)
# ═══════════════════════════════════════════════════════════════════════════════
echo -e "${GREEN}── 3. Boundary Check ${NC}"

run_validator() {
    local label="$1"
    local script="$2"
    shift 2
    if "$PYTHON" "$script" "$@" 2>&1; then
        pass "$label"
    else
        fail "$label"
    fi
}

run_validator "import_boundary_validator"  tools/import_boundary_validator.py
run_validator "test_import_hygiene_validator" tools/test_import_hygiene_validator.py
# The public export leaves this validator out (it checks the sites' production
# config); private-patterns.txt marks a development checkout, where a missing
# validator must still fail.
if [ -f tools/config_drift_validator.py ] || [ -f scripts/public_export/private-patterns.txt ]; then
    run_validator "config_drift_validator"      tools/config_drift_validator.py
fi
run_validator "contracts_fence"             tools/contracts_fence.py
run_validator "stale_name_validator"        tools/stale_name_validator.py
run_validator "schema_version_tracker"      tools/schema_version_tracker.py
run_validator "dto_equivalence_check"       tools/dto_equivalence_check.py
run_validator "runtime_support_matrix"      tools/runtime_support_matrix.py
run_validator "knowledge_plane_fence"       tools/knowledge_plane_fence.py
run_validator "adr_index_uniqueness"        tools/adr_index_uniqueness_validator.py
# WP-13 round 2 promotion #1: traceability promoted advisory ⇒ hard-fail
# (ADR-035 rollout order; warnings stay warnings, violations fail).
# The public export leaves docs/verification/ out, so the registry is absent
# there; a development checkout (private-patterns.txt present) still fails
# when the registry is missing.
if [ -f docs/verification/traceability.yaml ] || [ -f scripts/public_export/private-patterns.txt ]; then
    run_validator "traceability_validator"      tools/traceability_validator.py
fi
# WP-13 round 2 promotion #2: exemption governance promoted advisory ⇒
# hard-fail (0-error baseline; warnings/info stay soft tiers).
run_validator "exemption_governance"        tools/exemption_governance_validator.py
# WP-13 round 2 promotion #3: security compatibility promoted advisory ⇒
# hard-fail (0-critical baseline; warning/info severities stay soft).
run_validator "security_compat_scanner"     tools/security_compat_scanner.py
# WP-13 round 2 promotion #4: tool-route liveness promoted advisory ⇒
# hard-fail (grade-B -> baseline sweep to grade A 16 tests, then flip;
# findings now exit 1).
run_validator "tool_route_liveness"         tools/tool_route_liveness.py
# H-10.2 promotion: client route liveness hard-fail (2026-10-20) — the
# plan's gate (cleanup C-2 done 2026-10-19) opened once r4's client paths
# sweep to zero findings; whole-tree static scan, findings now exit 1.
run_validator "client_route_liveness"       tools/client_route_liveness.py --strict
# WP-13 round 2 promotion #5 (bridge_detector + transport_boundary_audit —
# dead_compat_bridge_validator stayed advisory until its design wave,
# promotion finalized 2026-09-10 with 2 active / 0 dead baseline):
run_validator "bridge_detector"             tools/bridge_detector.py
run_validator "transport_boundary_audit"    tools/transport_boundary_audit.py
run_validator "dead_compat_bridge"          tools/dead_compat_bridge_validator.py
# WP-13 round 2 promotion #6: metrics coverage promoted advisory ⇒
# hard-fail (after detection upgrade for lazy wrappers + exporter prefixes).
run_validator "check_metrics_coverage"      tools/check_metrics_coverage.py
# WP-13 round 2 promotion #7: ADR linkage promoted advisory ⇒ hard-fail;
# orphan_module_detector stays advisory (classification wave pending).
run_validator "adr_linkage_validator"       tools/adr_linkage_validator.py
# WP-13 round 2 promotion --orphan (after the full classification wave
# landed census at zero; keeps hard-fail on regressions):
run_validator "orphan_module_detector"      tools/orphan_module_detector.py

# Doc sanity: generation-collapse detector (mixed script/noise) for ADR +
# development prose; ratchet-baselined to existing findings.
# NOTE: macOS /bin/bash is 3.2 — no mapfile; use while-read into an array.
DOC_SANITY_ARGS=()
while IFS= read -r _doc_file; do
    DOC_SANITY_ARGS+=("$_doc_file")
done < <(find docs/adr docs/development -type f -name "*.md" 2>/dev/null)
run_validator "doc_sanity_gate"             tools/doc_sanity_gate.py ${DOC_SANITY_ARGS[@]+"${DOC_SANITY_ARGS[@]}"}

# ═══════════════════════════════════════════════════════════════════════════════
# 4. TESTS (scoped to changed components)
# ═══════════════════════════════════════════════════════════════════════════════
echo -e "${GREEN}── 4. Tests ${NC}"

run_tests() {
    local label="$1"
    local test_path="$2"
    shift 2
    if [ ! -d "$(dirname "$test_path" 2>/dev/null || echo .)" ]; then
        warn "$label skipped (test dir not found)"
        return
    fi
    if "$PYTHON" -m pytest "$test_path" -q "$@" 2>&1; then
        pass "$label"
    else
        fail "$label"
    fi
}

# Detect which components changed and run their tests
HAS_R4=0; HAS_GW=0; HAS_MCP=0; HAS_UMRS=0
if [ ${#CHANGED_PY[@]} -gt 0 ]; then
    for f in "${CHANGED_PY[@]}"; do
        [[ "$f" =~ ^r4/ ]] && HAS_R4=1
        [[ "$f" =~ ^gateway/ ]] && HAS_GW=1
        [[ "$f" =~ ^mcp-servers/ ]] && HAS_MCP=1
        [[ "$f" =~ ^umrs/ ]] && HAS_UMRS=1
    done
fi
# umrs is a submodule: parent diffs show only the pointer, so also check the
# pointer range and the submodule's own dirty/untracked state (diff mode only).
if [ $HAS_UMRS -eq 0 ] && [ ${#SPECIFIC_FILES[@]} -eq 0 ] && [ -d "umrs/tests" ]; then
    if git diff --name-only --diff-filter=ACMRT "$BASE" HEAD -- umrs 2>/dev/null | grep -q . \
       || [ -n "$(git -C umrs status --porcelain 2>/dev/null | head -1)" ]; then
        HAS_UMRS=1
    fi
fi

if [ $HAS_R4 -eq 1 ] || [ $FULL -eq 1 ]; then
    # Skip IPython-dependent tests when IPython is not installed
    IPYTHON_SKIP=()
    if ! "$PYTHON" -c "import IPython" 2>/dev/null; then
        IPYTHON_SKIP=(--ignore=r4/tests/test_companion/test_jupyter_magic.py --ignore=r4/tests/test_jupyter.py --ignore=r4/tests/test_package_imports.py)
        warn "IPython not installed — skipping jupyter tests"
    fi
    run_tests "r4 SDK"            "r4/tests/"                  -q ${IPYTHON_SKIP[@]:+"${IPYTHON_SKIP[@]}"}
fi

if [ $HAS_GW -eq 1 ] || [ $FULL -eq 1 ]; then
    run_tests "Gateway"           "gateway/tests/"              -q
fi

if [ $HAS_MCP -eq 1 ] || [ $FULL -eq 1 ]; then
    run_tests "MCP Servers"       "mcp-servers/tests/"          -q
fi

if { [ $HAS_UMRS -eq 1 ] || [ $FULL -eq 1 ]; } && [ -d "umrs/tests" ]; then
    # Mirrors CI test-umrs (Python 3.11 + Postgres/MinIO services). Integration
    # tests self-skip without services. CI ignores
    # test_rag_pipeline.py; on Python < 3.11 also skip the datetime.UTC tests
    # that CI's 3.11 runs (keep this list in sync or upgrade the venv).
    # 2026-09-16: run with cwd=umrs to mirror CI test-umrs exactly. Invoking
    # `pytest umrs/tests/` from the repo root collides the `tests` namespace
    # package with the root belt's own tests/ dir, breaking the sibling
    # `from tests.test_infer_proxy import ...` style used by the R2 secrets
    # pins — a root-vs-submodule resolution trench CI never sees.
    UMRS_IGNORES=(--ignore=tests/test_rag_pipeline.py)
    if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
        warn "venv Python < 3.11 (CI uses 3.11) — skipping datetime.UTC umrs tests"
        UMRS_IGNORES+=(--ignore=tests/test_job_tracking_handler_durable.py --ignore=tests/test_rag_service_llm_resolution.py --ignore=tests/test_workflow_durable_jobs.py --ignore=tests/test_workflow_executions_api.py)
    fi
    echo "  Running UMRS tests (this mirrors CI test-umrs and can take several minutes)..."
    if (cd umrs && "$PYTHON" -m pytest tests/ -q "${UMRS_IGNORES[@]}") 2>&1; then
        pass "UMRS"
    else
        fail "UMRS"
    fi
fi

echo ""

# ── slash-registry fixture drift (H-5): the v3 reachability manifest and the
# Python slash registry must move together. Guard: only steps touching the
# registry, the generator, the manifest or the fixture (full-repo always).
SLASH_DRIFT=0
if [ $FULL -eq 1 ]; then
    SLASH_DRIFT=1
else
    for f in ${CHANGED_ALL[@]+"${CHANGED_ALL[@]}"}; do
        case "$f" in
            r4/src/r4/tui/commands.py|scripts/regen-slash-fixture.py|packages/r4-tui-v3/tests/fixtures/python-slash-registry.json|packages/r4-tui-v3/src/commands/catalog.manifest.json) SLASH_DRIFT=1; break ;;
        esac
    done
fi
if [ $SLASH_DRIFT -eq 1 ]; then
    if "$PYTHON" scripts/regen-slash-fixture.py --check 2>&1; then
        pass "slash-fixture drift"
    else
        fail "slash-fixture drift (repair: \"$PYTHON\" scripts/regen-slash-fixture.py --write)"
    fi
fi

echo "============================================================"
if [ $FAILED -eq 0 ]; then
    echo -e "  ${GREEN}ALL CHECKS PASSED${NC}  (${WARNINGS:-0} warnings)"
    echo "============================================================"
    exit 0
else
    echo -e "  ${RED}${FAILED} CHECKS FAILED${NC}  (${WARNINGS:-0} warnings)"
    echo "============================================================"
    exit 1
fi
