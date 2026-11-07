#!/usr/bin/env bash
# Demo 99: Cleanup
# Inspect the final endpoint state, and optionally stop the endpoint that
# the demo suite reused or provisioned.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

STOP_ENDPOINT=0
for arg in "$@"; do
    if [[ "$arg" == "--stop-endpoint" ]]; then
        STOP_ENDPOINT=1
    fi
done

demo_header \
    "Demo 99: Cleanup" \
    "Inspect the final endpoint state, and optionally stop the endpoint that the demo suite reused or provisioned."

ensure_demo_project

demo_step "Show current endpoints"
demo_cmd r4 endpoint list

if [[ "$STOP_ENDPOINT" -eq 1 ]]; then
    demo_step "Stop the demo endpoint"
    stop_demo_endpoint
else
    demo_note "Re-run with --stop-endpoint to tear down the endpoint recorded by the demo suite."
fi
