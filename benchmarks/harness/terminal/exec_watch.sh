#!/usr/bin/env bash
# Log a host process snapshot whenever a trial's driver `docker compose exec`
# client goes away (2026-10-05). Two TB 4.0 trials lost that client to a SIGKILL
# from outside (exit -9; an in-container kill exits 137) with no kernel, OOM or
# timer trace; the snapshot shows what else was running at that moment.
#   usage: exec_watch.sh <log file> <pid to outlive>   (run_tb4.sh starts it)
set -uo pipefail
LOG=${1:?log file}; OWNER=${2:?pid}
declare -A age
while kill -0 "$OWNER" 2>/dev/null; do
  declare -A live=()
  for p in $(pgrep -f "docker compose --project-name .* exec .*driver[.]py"); do
    live[$p]=1; age[$p]=$(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ')
  done
  for p in "${!age[@]}"; do
    [[ -n ${live[$p]:-} ]] && continue
    { echo "=== $(date '+%F %T') driver exec pid $p gone after ~${age[$p]} s"
      ps -eo pid,ppid,etimes,stat,cmd --sort=-etimes | awk '$5 !~ /^\[/' | cut -c1-200 | head -60
    } >> "$LOG"
    unset "age[$p]"
  done
  unset live
  sleep 5
done
