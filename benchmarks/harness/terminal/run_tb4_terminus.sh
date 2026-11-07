#!/usr/bin/env bash
# Comparison lane (2026-10-06): Terminal-Bench 4.0 with harbor's reference agent
# terminus-2 on the SAME model, endpoint and proxy as run_tb4.sh — separates
# model/endpoint behaviour from the R4 harness.
#   usage: run_tb4_terminus.sh <run-name> [extra harbor args, e.g. -i terminal-bench/<task>]
# terminus-2 calls the model from the host through LiteLLM (api_base = the local
# model proxy, key = the per-run proxy token); temperature 0.4 like R4; a 2 h
# client timeout so a long reply is not cut by LiteLLM's 600 s default.
# <run-name>/run-meta.json records the commit and harbor settings (B3, 2026-10-24).
# Trials killed from the host (exit -9) are re-run once into <run-name>/terminus-rerun.
set -euo pipefail
RUN_NAME=${1:?usage: run_tb4_terminus.sh <run-name> [harbor args...]}; shift
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="$REPO/benchmarks/results/terminal-bench/$RUN_NAME"
PORT=${TB4_PROXY_PORT:-8335}
CONCURRENCY=${TB4_CONCURRENCY:-4}
TOKEN_FILE="$HOME/.cache/r4bench/$RUN_NAME.token"
cd "$REPO"
mkdir -p "$OUT" "$(dirname "$TOKEN_FILE")"
(umask 077; [[ -s $TOKEN_FILE ]] || openssl rand -hex 24 > "$TOKEN_FILE")
TOKEN="$(cat "$TOKEN_FILE")"

PYTHONPATH="$REPO" .venv/bin/python benchmarks/harness/terminal/model_proxy.py \
  --source rikyu --model glm-5.3 --port "$PORT" --token "$TOKEN" \
  --default-max-tokens 131072 --log "$OUT/model-proxy.jsonl" \
  ${TB4_PROXY_DUMP:+--dump-dir "$OUT/dumps"} > "$OUT/model-proxy.stderr" 2>&1 &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 20); do
  curl -sf -o /dev/null -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/v1/models" && break
  sleep 0.5
done

HARBOR_ARGS=(-d terminal-bench/terminal-bench@4.0.0 -e docker
  -a terminus-2 -m openai/glm-5.3
  --ak api_base="http://127.0.0.1:$PORT/v1" --ak temperature=0.4
  --ak 'llm_call_kwargs={"timeout": 7200}' -n "$CONCURRENCY")

# What this run ran with (the token travels in the environment and is never recorded).
PYTHONPATH="$REPO" .venv/bin/python -m benchmarks.harness.terminal.support run-meta \
  --out "$OUT/run-meta.json" --lane terminus-2 --set run_name="$RUN_NAME" \
  --set proxy_port="$PORT" --set default_max_tokens=131072 -- "${HARBOR_ARGS[@]}" "$@" \
  || echo "run-meta.json not written (the run goes on)" >&2

OPENAI_API_KEY="$TOKEN" harbor run "${HARBOR_ARGS[@]}" -o "$OUT/terminus" -y -q "$@"

# The same single re-run pass as run_tb4.sh for trials whose host-side exec client
# was SIGKILLed (exit -9): both lanes get it, so the paired comparison does not
# count one lane's infra kills as the other lane's wins (review 2026-10-24).
# The combined report reads <run-name>/terminus-rerun beside terminus.
# TB4_RERUN_INFRA=0 skips it.
if [[ ${TB4_RERUN_INFRA:-1} == 1 ]]; then
  mapfile -t KILLED < <(PYTHONPATH="$REPO" .venv/bin/python \
    benchmarks/harness/terminal/tb4_trial_diagnostics.py --run "$OUT/terminus" --infra-kills)
  if ((${#KILLED[@]})); then
    echo "re-running ${#KILLED[@]} host-killed trial(s): ${KILLED[*]}" >&2
    INCLUDE=()
    for t in "${KILLED[@]}"; do INCLUDE+=(-i "terminal-bench/$t"); done
    OPENAI_API_KEY="$TOKEN" harbor run "${HARBOR_ARGS[@]}" -o "$OUT/terminus-rerun" -y -q \
      "${INCLUDE[@]}" || true
  fi
fi
