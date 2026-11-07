#!/usr/bin/env bash
# Demo 03: Projects, Models & Knowledge Build-Up
# Show the real project and model-management commands, then build up the
# project knowledge store: import artifacts, extract knowledge, verify.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 03: Projects, Models & Knowledge Build-Up" \
    "Project/model management plus knowledge build-up: import, init, status, verify, readiness."

ensure_demo_project
resolve_demo_model

scratch_project="$(demo_scratch_project)"

demo_step "Show the current project context"
demo_cmd r4 project current

demo_step "List known projects"
demo_cmd r4 project list

demo_step "Create a disposable scratch project"
if r4 project switch "$scratch_project" >/dev/null 2>&1; then
    demo_note "Scratch project '$scratch_project' already exists."
    demo_cmd r4 project switch "$DEMO_PROJECT_ID"
else
    demo_cmd r4 project create "$scratch_project" --name "R4 Demo Scratch"
fi

demo_step "Switch into the scratch project and then return"
demo_cmd r4 project switch "$scratch_project"
demo_cmd r4 project switch "$DEMO_PROJECT_ID"

demo_step "Inspect the selected model"
demo_cmd r4 model info "$DEMO_MODEL_NAME"

demo_step "List recent jobs"
demo_try_cmd r4 job list --limit 10

# ---------------------------------------------------------------------------
# Knowledge build-up: import sample artifacts, extract knowledge, verify
# ---------------------------------------------------------------------------
demo_step "Import sample artifacts into the project knowledge store"
demo_try_cmd r4 project import "$DEMOS_DIR/sample_data"

demo_step "Extract structured knowledge and build the knowledge graph"
demo_try_cmd r4 project init

demo_step "Show knowledge status: artifacts, extraction, verifications"
demo_try_cmd r4 project status

demo_step "Verify a knowledge claim with evidence"
demo_try_cmd r4 project verify "What datasets are available in this project?"

demo_step "Generate a research readiness report"
demo_try_cmd r4 project readiness

demo_step "Delete the scratch project"
demo_try_cmd r4 project delete "$scratch_project"
demo_cmd r4 project switch "$DEMO_PROJECT_ID"

echo
echo "Next: ../04_endpoints_jobs/run_demo.sh"
