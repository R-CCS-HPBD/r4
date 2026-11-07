#!/usr/bin/env bash
# Start the bench proxies and host relays. Everything listens on UNIX sockets in run/ (dir 0700,
# sockets 0600): no TCP port exists, so other local users cannot reach them and nothing on the
# host can find them by port scan. Sandboxed harnesses reach them only through the in-sandbox
# relays that sandbox.py sets up per case.
#   run/bridge.sock       anthropic_bridge.py   Claude Code  -> glm-5.3@rikyu
#   run/passthrough.sock  openai_passthrough.py OpenCode, R4 -> glm-5.3@rikyu
#   run/pg.sock           socat -> 127.0.0.1:55432  (kgq Postgres; bound into arms B/C/P only)
#   run/s3.sock           socat -> 127.0.0.1:58333  (local SeaweedFS; arm B only)
# The per-case egress guard (attempt logger) runs inside the runner, not here.
# Each process starts under `env -i` with PATH/HOME/LANG and the shim's source/model selectors
# only (no HF_TOKEN, session tokens, SSH_AUTH_SOCK). The rikyu URL + key are resolved IN-PROCESS
# by each proxy through the repo shim's resolver; nothing here reads, prints or passes a key.
# Sampling parity: both proxies apply temperature $DEFAULT_TEMPERATURE (default 0.4 = R4's own
# default) when the client sends none; set DEFAULT_TEMPERATURE=off to leave the server default.
set -euo pipefail
H="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # code (git)
REPO="$(cd "$H/../../.." && pwd)"
PY="$REPO/.venv/bin/python"
# Data (logs, run sockets, results) lives outside git, under the bench root (see paths.py).
BENCH="${R4_SAME_MODEL_BENCH:?set R4_SAME_MODEL_BENCH to the bench root outside the checkout (see paths.py)}"
LANE="$BENCH/r4glm/harness"
RUN="$LANE/run"
DEFAULT_TEMPERATURE="${DEFAULT_TEMPERATURE:-0.4}"
TEMP_ARGS=()
[[ $DEFAULT_TEMPERATURE != off ]] && TEMP_ARGS=(--default-temperature "$DEFAULT_TEMPERATURE")
umask 077
mkdir -p "$LANE/logs" "$RUN"
chmod 700 "$RUN"
CLEAN=(env -i PATH=/usr/bin:/bin HOME="$HOME" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1
       R4_BENCH_SHIM_SOURCE=rikyu R4_BENCH_SHIM_TARGET_MODEL=glm-5.3)

alive() { [[ -f $1 ]] && kill -0 "$(cat "$1")" 2>/dev/null; }
ready() {  # kind sock
  if [[ $1 == http ]]; then curl -sf -o /dev/null --max-time 2 --unix-socket "$2" http://bench/healthz
  else [[ -S $2 ]]; fi
}

start() {  # name socket http|sock cmd...
  local name=$1 sock=$2 kind=$3; shift 3
  local pidf="$RUN/$name.pid"
  if alive "$pidf" && ready "$kind" "$sock"; then echo "$name already running (pid $(cat "$pidf"))"; return 0; fi
  rm -f "$sock"
  echo "=== start $(date -Is) ===" >> "$LANE/logs/$name.log"
  (cd "$H" && exec setsid "${CLEAN[@]}" "$@" >> "$LANE/logs/$name.log" 2>&1 < /dev/null) &
  echo $! > "$pidf"
  for _ in $(seq 1 60); do
    if ready "$kind" "$sock"; then echo "$name up on unix:$sock (pid $(cat "$pidf"))"; return 0; fi
    if ! alive "$pidf"; then break; fi
    sleep 0.5
  done
  echo "ERROR: $name did not come up; last log lines:" >&2; tail -n 5 "$LANE/logs/$name.log" >&2; exit 1
}

tcp_up() { ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q .; }

start bridge "$RUN/bridge.sock" http "$PY" -B "$H/anthropic_bridge.py" --uds "$RUN/bridge.sock" \
  --target-model glm-5.3 "${TEMP_ARGS[@]}"
start passthrough "$RUN/passthrough.sock" http "$PY" -B "$H/openai_passthrough.py" --uds "$RUN/passthrough.sock" \
  --force-model glm-5.3 "${TEMP_ARGS[@]}"
for spec in pg_relay:pg:55432 s3_relay:s3:58333; do
  IFS=: read -r name short port <<< "$spec"
  tcp_up "$port" || echo "WARNING: nothing listens on 127.0.0.1:$port ($short relay will fail per connection)" >&2
  start "$name" "$RUN/$short.sock" sock /usr/bin/socat \
    "UNIX-LISTEN:$RUN/$short.sock,fork,mode=600,unlink-early" "TCP:127.0.0.1:$port"
done
