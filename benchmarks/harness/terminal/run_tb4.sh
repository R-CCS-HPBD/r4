#!/usr/bin/env bash
# Terminal-Bench 4.0 with the R4 companion, set against the public board (2026-10-05).
#   usage: run_tb4.sh <run-name> [extra harbor args, e.g. -i terminal-bench/<task> or -x ...]
# - model calls go through model_proxy.py on port 8333 (the agent's default route and port;
#   key stays on the host; containers get a per-run token kept in ~/.cache/r4bench, never in
#   git), streamed upstream, max_tokens 131072 when R4 sends none (rikyu otherwise caps a
#   reply at 32,768);
# - board settings: every task's own 8 h agent timeout (exec cap 28800), 1 attempt, decision
#   model off (one model, like the public rows); TB4_DECISION=nimble runs the decision-model
#   lane instead: nimble_proxy.py on port TB4_NIMBLE_PORT (8331) forwards the in-container
#   scoring calls through the host's platform profile TB4_NIMBLE_PROFILE (rccs-cloud), with
#   its own per-run token; its log is <run-name>/nimble-proxy.log;
# - the three H100-only tasks are excluded unless TB4_INCLUDE_GPU=1 (local Docker cannot
#   allocate GPUs in harbor 0.23, and the tasks' verifiers are calibrated on H100);
# - results: benchmarks/results/terminal-bench/<run-name>/r4, proxy usage log beside it;
#   trials killed from the host (exit -9) are re-run once into <run-name>/r4-rerun;
# - <run-name>/run-meta.json records the commit, harbor settings, the R4 knobs the agent
#   env carries (--ak profile/temperature/top_p, --ae R4_*), the install footprint (B3);
# - R4 knobs for an ablation arm: --ak profile=terminal, --ak temperature=1.0 --ak top_p=0.95,
#   or any R4_* switch as --ae NAME=VALUE (harbor passes it to the agent's commands).
# Compare afterwards with tb4_public_compare.py (see README). Never add --upload/--launch.
set -euo pipefail
RUN_NAME=${1:?usage: run_tb4.sh <run-name> [harbor args...]}; shift
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="$REPO/benchmarks/results/terminal-bench/$RUN_NAME"
PORT=${TB4_PROXY_PORT:-8333}
CONCURRENCY=${TB4_CONCURRENCY:-4}
DECISION=${TB4_DECISION:-off}
NIMBLE_PORT=${TB4_NIMBLE_PORT:-8331}
TOKEN_FILE="$HOME/.cache/r4bench/$RUN_NAME.token"
cd "$REPO"
mkdir -p "$OUT" "$(dirname "$TOKEN_FILE")"
(umask 077; [[ -s $TOKEN_FILE ]] || openssl rand -hex 24 > "$TOKEN_FILE")
TOKEN="$(cat "$TOKEN_FILE")"

GPU_EXCLUDES=()
if [[ ${TB4_INCLUDE_GPU:-0} != 1 ]]; then
  for t in fp8-rmsnorm-gemm jax-speedrun-gpu math-eval-grader; do GPU_EXCLUDES+=(-x "terminal-bench/$t"); done
fi

# Build the r4 wheelhouse once up front: concurrent trials would race on a first build
# (the cache is keyed by a source signature, so an edited tree always gets fresh wheels).
WHEELHOUSE=$(PYTHONPATH="$REPO" .venv/bin/python -c \
  'from benchmarks.harness.terminal.support import ensure_wheelhouse; print(ensure_wheelhouse())')
echo "$WHEELHOUSE"

PYTHONPATH="$REPO" .venv/bin/python benchmarks/harness/terminal/model_proxy.py \
  --source rikyu --model glm-5.3 --port "$PORT" --token "$TOKEN" \
  --default-max-tokens 131072 --log "$OUT/model-proxy.jsonl" \
  ${TB4_PROXY_DUMP:+--dump-dir "$OUT/dumps"} > "$OUT/model-proxy.stderr" 2>&1 &
PROXY_PID=$!
NIMBLE_PID=""
NIMBLE_TOKEN=""
if [[ $DECISION == nimble ]]; then  # decision-model lane (ADR-076), scoring calls only
  NIMBLE_TOKEN_FILE="$HOME/.cache/r4bench/$RUN_NAME.nimble.token"
  (umask 077; [[ -s $NIMBLE_TOKEN_FILE ]] || openssl rand -hex 24 > "$NIMBLE_TOKEN_FILE")
  NIMBLE_TOKEN="$(cat "$NIMBLE_TOKEN_FILE")"
  PYTHONPATH="$REPO" .venv/bin/python benchmarks/harness/terminal/nimble_proxy.py \
    --profile "${TB4_NIMBLE_PROFILE:-rccs-cloud}" --port "$NIMBLE_PORT" --token "$NIMBLE_TOKEN" \
    > "$OUT/nimble-proxy.log" 2>&1 &
  NIMBLE_PID=$!
elif [[ $DECISION != off ]]; then
  echo "TB4_DECISION must be off or nimble, not $DECISION" >&2; exit 2
fi
WATCH_PID=""
if [[ ${TB4_EXEC_WATCH:-1} == 1 ]]; then  # process snapshots when a driver exec client dies
  bash benchmarks/harness/terminal/exec_watch.sh "$OUT/exec-watch.log" $$ &
  WATCH_PID=$!
fi
trap 'kill $PROXY_PID $NIMBLE_PID $WATCH_PID 2>/dev/null || true' EXIT

# Both proxies must answer with THIS run's token: a port held by another run's proxy
# answers 403, and the trials would then run against it (2026-10-06: a stale decision
# proxy from an earlier run kept port 8331, so every gate call failed).
wait_proxy() {  # wait_proxy <name> <port> <token> <log>
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null -H "Authorization: Bearer $3" "http://127.0.0.1:$2/v1/models" && return 0
    sleep 0.5
  done
  echo "$1 proxy on port $2 did not answer with this run's token (port taken? see $4)" >&2
  exit 3
}
wait_proxy model "$PORT" "$TOKEN" "$OUT/model-proxy.stderr"
if [[ $DECISION == nimble ]]; then
  wait_proxy decision "$NIMBLE_PORT" "$NIMBLE_TOKEN" "$OUT/nimble-proxy.log"
fi

HARBOR_ARGS=(-d terminal-bench/terminal-bench@4.0.0 -e docker
  -a benchmarks.harness.terminal.r4_agent:R4CompanionAgent -m glm-5.3@rikyu
  --ak model_proxy_port="$PORT" --ak decision="$DECISION" --ak nimble_port="$NIMBLE_PORT"
  --ak exec_timeout_sec=28800 --ak r4_timeout_sec=7200 -n "$CONCURRENCY")

run_harbor() {  # run_harbor <job dir> [harbor args...]
  local dest=$1; shift
  PYTHONPATH="$REPO" R4BENCH_MODEL_TOKEN="$TOKEN" R4BENCH_NIMBLE_TOKEN="$NIMBLE_TOKEN" harbor run \
    "${HARBOR_ARGS[@]}" -o "$dest" -y -q "$@"
}

# What this run ran with (tokens travel in the environment and are never recorded).
PYTHONPATH="$REPO" .venv/bin/python -m benchmarks.harness.terminal.support run-meta \
  --out "$OUT/run-meta.json" --lane r4 --set run_name="$RUN_NAME" --set proxy_port="$PORT" \
  --set default_max_tokens=131072 --set decision="$DECISION" --set wheelhouse="$WHEELHOUSE" \
  -- "${HARBOR_ARGS[@]}" "${GPU_EXCLUDES[@]}" "$@" \
  || echo "run-meta.json not written (the run goes on)" >&2

run_harbor "$OUT/r4" "${GPU_EXCLUDES[@]}" "$@"

# One re-run pass for trials whose host-side `docker compose exec` client was
# SIGKILLed (exit -9, cause unidentified; not an agent outcome — an in-container
# kill exits 137). Merge with: tb4_trial_diagnostics.py / tb4_public_compare.py
# --run "$OUT/r4" --run "$OUT/r4-rerun". TB4_RERUN_INFRA=0 skips it.
if [[ ${TB4_RERUN_INFRA:-1} == 1 ]]; then
  mapfile -t KILLED < <(PYTHONPATH="$REPO" .venv/bin/python \
    benchmarks/harness/terminal/tb4_trial_diagnostics.py --run "$OUT/r4" --infra-kills)
  if ((${#KILLED[@]})); then
    echo "re-running ${#KILLED[@]} host-killed trial(s): ${KILLED[*]}" >&2
    INCLUDE=()
    for t in "${KILLED[@]}"; do INCLUDE+=(-i "terminal-bench/$t"); done
    run_harbor "$OUT/r4-rerun" "${INCLUDE[@]}" || true
  fi
fi
