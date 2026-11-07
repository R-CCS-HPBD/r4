#!/usr/bin/env bash
# Demo 04: Endpoints & Jobs
# Inspect the real endpoint lifecycle and durable job surfaces after the
# onboarding step has prepared a live endpoint.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 04: Endpoints & Jobs" \
    "Inspect the real endpoint lifecycle and durable job surfaces after the onboarding step has prepared a live endpoint."

ensure_demo_project
ensure_demo_endpoint

demo_step "List active endpoints"
demo_cmd r4 endpoint list

demo_step "Inspect the active endpoint"
demo_cmd r4 endpoint status "$DEMO_ENDPOINT_ID"

demo_step "Show the mounted mini-RiVault endpoint contract"
describe_demo_endpoint_mounts
demo_note "The endpoint was requested with project tier isolation, custom MCP, RAG, and EgressGuard profile intent."

demo_step "Read recent endpoint logs"
demo_try_cmd r4 endpoint logs "$DEMO_ENDPOINT_ID" --tail 20

demo_step "List recent jobs"
demo_try_cmd r4 job list --limit 10

demo_step "Describe the most recent job when one exists"
recent_job_id="$(r4 job list --limit 1 2>/dev/null | awk 'NR==3 {print $1}')"
if [[ -n "$recent_job_id" ]]; then
    demo_try_cmd r4 job describe "$recent_job_id"
else
    demo_note "No recent jobs were available to describe."
fi

echo
echo "Next: ../05_mcp_workflows/run_demo.sh"
