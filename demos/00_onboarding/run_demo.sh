#!/usr/bin/env bash
# Demo 00: Deploy & Onboarding
# Check saved profile, pick a project, verify health, and provision or reuse
# the endpoint that later demos depend on.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 00: Deploy & Onboarding" \
    "Check the saved profile, pick a project, verify health, and provision or reuse the endpoint that later demos depend on."

demo_step "Show the current r4 profile"
demo_cmd r4 config show

demo_step "Verify that credentials are configured"
demo_cmd r4 auth status

demo_step "Pin the demo project in the local r4 context"
ensure_demo_project
demo_ok "Using project: $DEMO_PROJECT_ID"

demo_step "List available models"
demo_cmd r4 model list --limit 10
resolve_demo_model
demo_ok "Selected model: $DEMO_MODEL_NAME"

demo_step "Provision or reuse the endpoint required by the rest of the suite"
describe_demo_endpoint_mounts
ensure_demo_endpoint

demo_step "Inspect the active endpoint"
demo_cmd r4 endpoint status "$DEMO_ENDPOINT_ID"

demo_step "Check gateway readiness after endpoint provisioning"
demo_try_cmd r4 health

echo
echo "Next: ../01_chat/run_demo.sh"
