#!/usr/bin/env bash
# ADR-093 S9: confirm the final configuration against B0.
#
# ── PRE-REGISTERED GATE (ADR-093, "Slices, components and gates", row S9, verbatim) ──
#
#   S9 Confirm and document | all defaults | Final configuration vs B0 with two seeds,
#   headroom_used, retrieve p95 ≤ 1.5 s. Rollout notes in ADR-081, 083, 085 and 090.
#   Gap rows, STATE and ledger updated.
#
# and, for the agent arm, MD-2 ("S9 re-runs the agent arm at 60 claims per label; if
# the gate passes there, `auto` routes small readers") reads row S5 (verbatim):
#
#   S5 Router, citations | C12, C13 | Reader non-inferior first. Agent arm: accuracy
#   ≥ +10 pts (sign test), absent unsupported_verdict not higher, nonsense "not covered"
#   ≥ 0.9, p50 added latency ≤ 3 s. Citation planted set 100%, AUROC ≥ 0.70, 0 LLM calls.
#
# The one number the S9 row fixes is the latency: summary.latency.retrieve_seconds.p95
# of the dev retrieve:dense run (300 claims) <= 1.5 s; this script judges it. "With two
# seeds" is two runs of the same configuration: r4's Client.chat_completion takes no seed
# (model, temperature, max_tokens, tools only), so no seed reaches ollama and run.py has
# no --seed; the 3B reads at temperature 0 and `run.py repro` records whether run 2
# reproduces run 1 claim by claim. "vs B0" and headroom_used carry no threshold in the
# row: the paired compare and headroom reports are written for the maintainer to read
# (the S1 and S5 receipts are in them). The documentation half of the row (rollout
# notes, gap rows, STATE, ledger) is not a measurement.
#
# What runs (results land beside the B0 files, ADR093_RESULTS):
#   retrieve:dense, retrieve mode, dev and nonsense            (p95, sufficiency)
#   retrieve:dense + pack-soft infer on dev and tune, twice     (vs B0, repro)
#   retrieve:dense + pack-soft infer on nonsense                (vs B0)
#   run.py answer on dev and nonsense                           (vs B0, the answer path)
#   agent, R4_RAG_ROUTER=on, --limit 60 (dev) and the nonsense set (vs B0; MD-2)
#   closed and oracle under pack-soft (S9_FRESH_BRACKETS=0 skips them)
#   compare, repro, headroom, sufficiency, latency            -> results/<stamp>-s9-*.json
#
#   S9_DATA=<scifact data/> XDG_CONFIG_HOME=<eval r4 config> benchmarks/retrieval/s9_confirm.sh
#   S9_DRY_RUN=1 S9_DATA=... benchmarks/retrieval/s9_confirm.sh     # print the commands only
#
# (M) Maintainer only: the stack up with the final defaults (STANDALONE_PROFILE=dev
# ./standalone.sh up) and an r4 config logged in with `standalone.sh token`. This script
# mints nothing and reads no credential.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${ADR093_REPO_ROOT:-$(cd "${HERE}/../.." && pwd)}"
PY="${ADR093_PYTHON:-${REPO}/.venv/bin/python}"
RUN="${HERE}/run.py"
B0_DIR="${S9_B0_DIR:-${REPO}/benchmarks/retrieval/results}"
RESULTS="${ADR093_RESULTS:-${B0_DIR}}"
LABEL="${S9_LABEL:-S9}"
P95_MAX="1.5"
AGENT_PER_LABEL=60
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
export ADR093_REPO_ROOT="$REPO" ADR093_RESULTS="$RESULTS"

die()  { printf 'x %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*" >&2; }
dry()  { [[ "${S9_DRY_RUN:-0}" == "1" ]]; }

run() {
  if dry; then printf '+ %s\n' "$*"; else "$@"; fi
}

# A command's stdout kept as a report file.
report() {
  local name="$1"
  shift
  local file="${RESULTS}/${STAMP}-s9-${name}.json"
  if dry; then
    printf '+ %s > %s\n' "$*" "$file"
  else
    "$@" >"$file"
    info "  ${file}"
  fi
}

# The newest result file whose name ends with the pattern (names start with a
# UTC stamp, so the glob's last match is the newest).
newest() {
  local pattern="$1" f last=""
  if dry; then
    printf '<newest *-%s>' "$pattern"
    return
  fi
  for f in "$RESULTS"/*-"$pattern"; do
    [[ -e "$f" ]] && last="$f"
  done
  [[ -n "$last" ]] || die "no result file *-${pattern} in ${RESULTS}"
  printf '%s' "$last"
}

# A B0 file by its stem: its schema-2 rescore when one exists.
b0() {
  local stem="$1" f
  for f in "$B0_DIR"/*-"${stem}"-rescored.json "$B0_DIR"/*-"${stem}".json; do
    if [[ -e "$f" ]]; then
      printf '%s' "$f"
      return
    fi
  done
  if dry; then
    printf '<B0 *-%s.json>' "$stem"
    return
  fi
  die "no B0 file *-${stem}.json in ${B0_DIR}"
}

preflight() {
  [[ -n "${S9_DATA:-}" ]] || die "set S9_DATA to the SciFact data/ directory"
  dry || [[ -d "$S9_DATA" ]] || die "no directory ${S9_DATA}"
  dry || [[ -n "${XDG_CONFIG_HOME:-}" ]] \
    || die "(M) set XDG_CONFIG_HOME to an r4 config logged in with standalone.sh token"
  [[ -x "$PY" ]] || dry || die "no python at ${PY} (ADR093_PYTHON)"
  info "B0 files in ${B0_DIR}:"
  if [[ -d "$B0_DIR" ]]; then
    find "$B0_DIR" -maxdepth 1 -name '*-B0*.json' -exec basename {} \; | sort >&2
  fi
  dry || mkdir -p "$RESULTS"
}

# The B0 stems this script pairs with, the verdict and answer files among them
# rescored to schema 2 once (run.py rescore writes <name>-rescored.json beside it).
B0_RESCORE=(infer-dev-legacy-B0 infer-nonsense-legacy-B0 answer-dev-rag-query-B0
  answer-nonsense-rag-query-B0 infer-dev-closed-B0 infer-dev-oracle-B0
  infer-dev-agent-B0 infer-nonsense-agent-B0)

rescore_b0() {
  local stem file
  for stem in "${B0_RESCORE[@]}"; do
    file="$(b0 "$stem")"
    case "$file" in
      *-rescored.json) ;;
      *) run "$PY" "$RUN" rescore --data "$S9_DATA" "$file" ;;
    esac
  done
}

measure() {
  local d="$S9_DATA" n
  run "$PY" "$RUN" retrieve --data "$d" --arms retrieve:dense --label "$LABEL"
  run "$PY" "$RUN" retrieve --data "$d" --claims nonsense --arms retrieve:dense --label "$LABEL"
  for n in 1 2; do
    run "$PY" "$RUN" infer --data "$d" --arms retrieve:dense --prompt pack-soft --label "${LABEL}-run${n}"
    run "$PY" "$RUN" infer --data "$d" --claims tune --arms retrieve:dense --prompt pack-soft --label "${LABEL}-run${n}"
  done
  run "$PY" "$RUN" infer --data "$d" --claims nonsense --arms retrieve:dense --prompt pack-soft --label "$LABEL"
  run "$PY" "$RUN" answer --data "$d" --label "$LABEL"
  run "$PY" "$RUN" answer --data "$d" --claims nonsense --label "$LABEL"
  run env R4_RAG_ROUTER=on "$PY" "$RUN" infer --data "$d" --arms agent --limit "$AGENT_PER_LABEL" --label "${LABEL}-router"
  run env R4_RAG_ROUTER=on "$PY" "$RUN" infer --data "$d" --claims nonsense --arms agent --limit "$AGENT_PER_LABEL" --label "${LABEL}-router"
  if [[ "${S9_FRESH_BRACKETS:-1}" == "1" ]]; then
    run "$PY" "$RUN" infer --data "$d" --arms closed,oracle --prompt pack-soft --label "$LABEL"
  fi
}

latency_gate() {
  local file="$1"
  "$PY" - "$file" "$P95_MAX" <<'PYEOF'
import json, sys

path, limit = sys.argv[1], float(sys.argv[2])
latency = json.load(open(path))["summary"]["latency"]
p95 = latency["retrieve_seconds"]["p95"]
print(json.dumps({
    "file": path,
    "retrieve_seconds": latency["retrieve_seconds"],
    "server_timing_ms": {k: v for k, v in latency.items() if k.startswith("timing_ms.")},
    "max_p95_s": limit,
    "pass": p95 is not None and p95 <= limit,
}, indent=1))
PYEOF
}

compare_all() {
  local pp="p-pack-soft" n run1 run2
  run1="$(newest "infer-dev-retrieve-dense-${pp}-${LABEL}-run1.json")"
  run2="$(newest "infer-dev-retrieve-dense-${pp}-${LABEL}-run2.json")"
  for n in 1 2; do
    report "compare-dev-run${n}-vs-B0-legacy" "$PY" "$RUN" compare "$(b0 infer-dev-legacy-B0)" \
      "$(newest "infer-dev-retrieve-dense-${pp}-${LABEL}-run${n}.json")"
    report "headroom-run${n}-B0-brackets" "$PY" "$RUN" headroom \
      "$(newest "infer-dev-retrieve-dense-${pp}-${LABEL}-run${n}.json")" \
      "$(b0 infer-dev-closed-B0)" "$(b0 infer-dev-oracle-B0)"
    if [[ "${S9_FRESH_BRACKETS:-1}" == "1" ]]; then
      report "headroom-run${n}-fresh-brackets" "$PY" "$RUN" headroom \
        "$(newest "infer-dev-retrieve-dense-${pp}-${LABEL}-run${n}.json")" \
        "$(newest "infer-dev-closed-${pp}-${LABEL}.json")" "$(newest "infer-dev-oracle-${pp}-${LABEL}.json")"
    fi
  done
  report "repro-dev" "$PY" "$RUN" repro "$run1" "$run2"
  report "repro-tune" "$PY" "$RUN" repro \
    "$(newest "infer-tune-retrieve-dense-${pp}-${LABEL}-run1.json")" \
    "$(newest "infer-tune-retrieve-dense-${pp}-${LABEL}-run2.json")"
  report "compare-dev-run1-vs-run2" "$PY" "$RUN" compare "$run1" "$run2"
  report "compare-nonsense-vs-B0-legacy" "$PY" "$RUN" compare "$(b0 infer-nonsense-legacy-B0)" \
    "$(newest "infer-nonsense-retrieve-dense-${pp}-${LABEL}.json")"
  report "compare-answer-dev-vs-B0" "$PY" "$RUN" compare "$(b0 answer-dev-rag-query-B0)" \
    "$(newest "answer-dev-rag-query-${LABEL}.json")"
  report "compare-answer-nonsense-vs-B0" "$PY" "$RUN" compare "$(b0 answer-nonsense-rag-query-B0)" \
    "$(newest "answer-nonsense-rag-query-${LABEL}.json")"
  report "compare-agent-dev-vs-B0" "$PY" "$RUN" compare "$(b0 infer-dev-agent-B0)" \
    "$(newest "infer-dev-agent-${LABEL}-router.json")"
  report "compare-agent-nonsense-vs-B0" "$PY" "$RUN" compare "$(b0 infer-nonsense-agent-B0)" \
    "$(newest "infer-nonsense-agent-${LABEL}-router.json")"
  report "compare-retrieve-dev-vs-B0-legacy" "$PY" "$RUN" compare "$(b0 retrieve-dev-legacy-B0)" \
    "$(newest "retrieve-dev-retrieve-dense-${LABEL}.json")"
  report "sufficiency" "$PY" "$RUN" sufficiency "$(newest "retrieve-dev-retrieve-dense-${LABEL}.json")" \
    "$(newest "retrieve-nonsense-retrieve-dense-${LABEL}.json")"
  report "latency" latency_gate "$(newest "retrieve-dev-retrieve-dense-${LABEL}.json")"
}

main() {
  preflight
  info "S9 ${STAMP}: results in ${RESULTS}"
  rescore_b0
  if [[ "${S9_SKIP_MEASURE:-0}" != "1" ]]; then
    measure
  fi
  compare_all
  info "S9: the latency gate is in ${RESULTS}/${STAMP}-s9-latency.json; repro in *-s9-repro-*.json;"
  info "    read the compare and headroom reports against the pre-registered rows above."
}

main "$@"
