#!/usr/bin/env bash
# Demo 01: Chat & Sessions
# Use the real r4 chat and session commands against the project and endpoint
# prepared by the onboarding step.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 01: Chat & Sessions" \
    "Use the real r4 chat and session commands against the project and endpoint prepared by the onboarding step."

ensure_demo_project
ensure_demo_endpoint
resolve_demo_model

demo_step "List the current endpoint inventory"
demo_cmd r4 endpoint list

demo_step "Run a one-shot chat request"
demo_try_cmd r4 chat "What is 2 + 2? Answer with one token." --model "$DEMO_MODEL_NAME"

demo_step "Run a streaming chat request"
demo_try_cmd r4 chat "List three short benefits of using the r4 CLI." --model "$DEMO_MODEL_NAME" --stream

demo_step "Inspect saved local session registries"
demo_cmd r4 session --json

demo_step "Inspect companion sessions"
demo_try_cmd r4 companion sessions

echo
echo "Next: ../02_rag/run_demo.sh"
