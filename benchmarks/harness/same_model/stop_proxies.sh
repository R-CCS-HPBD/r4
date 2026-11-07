#!/usr/bin/env bash
# Stop what start_proxies.sh started (pid files in run/; the cmdline is checked before kill)
# and remove the sockets.
set -uo pipefail
# Data (logs, run sockets, results) lives outside git, under the bench root (see paths.py).
BENCH="${R4_SAME_MODEL_BENCH:?set R4_SAME_MODEL_BENCH to the bench root outside the checkout (see paths.py)}"
LANE="$BENCH/r4glm/harness"
RUN="$LANE/run"
ours() { kill -0 "$1" 2>/dev/null && tr '\0' ' ' < "/proc/$1/cmdline" \
           | grep -qE "anthropic_bridge|openai_passthrough|socat UNIX-LISTEN:$RUN/"; }
for name in bridge passthrough pg_relay s3_relay; do
  pidf="$RUN/$name.pid"
  pid=""
  [[ -f $pidf ]] && pid=$(cat "$pidf")
  if [[ -n $pid ]] && ours "$pid"; then
    kill "$pid" && echo "$name stopped (pid $pid)"
  else
    echo "$name: not running"
  fi
  rm -f "$pidf"
done
sleep 0.5
rm -f "$RUN"/bridge.sock "$RUN"/passthrough.sock "$RUN"/pg.sock "$RUN"/s3.sock
