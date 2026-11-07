#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

FORWARDED_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --bench)
            echo "Benchmark mode now uses the real r4 chat demo."
            FORWARDED_ARGS+=("--auto")
            ;;
        *)
            FORWARDED_ARGS+=("$arg")
            ;;
    esac
done

echo
echo "DGX Spark demo entrypoint: forwarding to the r4 demo suite."
echo

exec bash "$DEMOS_DIR/run_all.sh" "${FORWARDED_ARGS[@]}"
