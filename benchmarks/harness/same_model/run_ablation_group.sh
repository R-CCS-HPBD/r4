#!/usr/bin/env bash
# usage: run_ablation_group.sh <group-file> <variant:srcname> [<variant:srcname> ...]
# Runs each variant (arm A, par 2) concurrently on the questions in <group-file>; waits for all.
set -u
# <group-file> is a qid list under the lane data dir; variant sources live in $LANE/r4_variants/<src>/src.
H=$(cd "$(dirname "$0")" && pwd); PY="$(cd "$H/../../.." && pwd)/.venv/bin/python"
# Data (logs, run sockets, results) lives outside git, under the bench root (see paths.py).
BENCH="${R4_SAME_MODEL_BENCH:?set R4_SAME_MODEL_BENCH to the bench root outside the checkout (see paths.py)}"
LANE="$BENCH/r4glm/harness"
G=$1; shift
for spec in "$@"; do
  v=${spec%%:*}; src=${spec##*:}
  ( cd "$H" && "$PY" run_r4.py --variant "$v" --r4-src "$LANE/r4_variants/$src/src" --qids "$(cat "$LANE/$G")" --arms A \
      --par 2 --timeout 1200 --run-note "ablation $v ($src) $G arm A par2" > "$LANE/logs/run_r4${v}_$(basename "$G" .txt).out" 2>&1 ) &
done
wait
echo "group $G done: $*"
