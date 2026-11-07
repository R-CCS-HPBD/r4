#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo
echo "Workstation demo entrypoint: forwarding to the r4 demo suite."
echo

exec bash "$DEMOS_DIR/run_all.sh" "$@"
