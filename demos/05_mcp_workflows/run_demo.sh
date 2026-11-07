#!/usr/bin/env bash
# Demo 05: MCP & Workflows
# Inspect MCP servers, tools, hatch packages, and workflow inventory
# through the real r4 command tree.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HATCH_OUTPUT_DIR="$DEMOS_DIR/.state/hatch"
HATCH_PACKAGE_NAME="r4-demo-hatch"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 05: MCP & Workflows" \
    "Inspect MCP servers, tools, hatch packages, and workflow inventory through the real r4 command tree."

ensure_demo_project

demo_step "List registered MCP servers"
demo_try_cmd r4 mcp list --servers

demo_step "List visible MCP tools"
demo_try_cmd r4 mcp list --tools

demo_step "Create a local Hatch package under demos/.state"
rm -rf "$HATCH_OUTPUT_DIR/$HATCH_PACKAGE_NAME"
mkdir -p "$HATCH_OUTPUT_DIR"
demo_try_cmd r4 mcp hatch create "$HATCH_PACKAGE_NAME" --output "$HATCH_OUTPUT_DIR"

demo_step "Validate the custom MCP manifest mounted into mini-RiVault"
describe_demo_endpoint_mounts
if python3 -m r4_mcp.runners.custom_mcp --config-dir "$DEMO_MCP_SRC" --validate-only >/dev/null 2>&1; then
    demo_ok "Mounted MCP manifest is valid."
else
    demo_note "r4_mcp is not installed in this client environment, or the manifest validator is unavailable."
fi

demo_step "List installed Hatch packages"
demo_try_cmd r4 mcp hatch list

# ---------------------------------------------------------------------------
# Default Hatch registry: install + connect a platform tool package
# ---------------------------------------------------------------------------
DEFAULT_HATCH_REGISTRY="$(cd "$DEMOS_DIR/.." && pwd)/mcp-servers/hatch-registry"
if [[ -f "$DEFAULT_HATCH_REGISTRY/registry.json" ]]; then
    export R4_HATCH_REGISTRIES="$DEFAULT_HATCH_REGISTRY"

    demo_step "Search the default platform Hatch registry"
    demo_try_cmd r4 mcp hatch search

    demo_step "Install the knowledge-tools package from the default registry"
    demo_try_cmd r4 mcp hatch install knowledge-tools

    demo_step "Connect knowledge-tools (registers its tools with UMRS)"
    demo_note "Requires MCP_URL_ALLOWLIST=127.0.0.1/32 on the UMRS side (set by the umrs-front Ansible profile)."
    demo_try_cmd r4 mcp hatch connect knowledge-tools

    demo_step "Confirm the connected server appears in the MCP registry"
    demo_try_cmd r4 mcp list --servers
else
    demo_note "Default Hatch registry not found at $DEFAULT_HATCH_REGISTRY (running outside the platform checkout)."
fi

demo_step "List registered workflows"
demo_try_cmd r4 workflow list

echo
echo "Next: ../06_complex_workflow/run_demo.sh"
