#!/usr/bin/env bash
# Demo 07: Knowledge-Enhanced Inference & Research Iteration
# Ask questions grounded in the project knowledge store, generate a
# graph-grounded research plan, and start the autonomous research loop.
# Also covers r4 agent, r4 search, and r4 team surfaces.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 07: Knowledge-Enhanced Inference & Research Iteration" \
    "Knowledge-grounded ask/plan, autonomous research loop, plus r4 agent/search/team surfaces."

ensure_demo_project

# ---------------------------------------------------------------------------
# Custom-knowledge enhanced inference (requires Demo 03 knowledge build-up
# and a live inference endpoint from Demo 00/04)
# ---------------------------------------------------------------------------
demo_step "Ask a question grounded in the project knowledge store (with citations)"
demo_note "Uses the knowledge built in Demo 03; answers cite ingested artifacts. Needs a live endpoint."
demo_try_cmd r4 project ask "Summarize what this project knows about its datasets"

demo_step "Generate a graph-grounded research plan"
demo_try_cmd r4 project plan "What analysis should we perform next on the sample data?"

# ---------------------------------------------------------------------------
# Research iteration: approve readiness and start the autonomous loop
# ---------------------------------------------------------------------------
demo_step "Review verification findings before approving"
demo_try_cmd r4 project findings

demo_step "Start the autonomous research loop on UMRS (approve + bounded iterations)"
demo_note "--approve accepts the readiness report; --max-iterations bounds the loop. The loop runs each experiment as a registered UMRS workflow; --workflow names the one Demo 06 registers (with several registered and none named, the server refuses and asks you to choose; with none registered the start reports the blocker and names the next step)."
# 2026-10-23: without --workflow every nightly start was refused (400 "several
# workflows are registered … pass workflow_name") once Demo 06 had registered
# its DAG next to the user's own workflows (Ops Watch #532 investigation).
DEMO_LOOP_WORKFLOW="${R4_DEMO_COMPLEX_WORKFLOW_NAME:-r4-complex-dag-demo}"
if demo_has_ready_llm_endpoint; then
    demo_try_cmd r4 project loop start --approve --max-iterations 2 --workflow "$DEMO_LOOP_WORKFLOW"
else
    demo_note "Skipped: the loop's experiments run $DEMO_LOOP_WORKFLOW, whose steps all need an LLM endpoint, and this venue has no ready one (start one in Demo 04, or: r4 endpoint up)."
fi

demo_step "Export the project knowledge bundle (.kb) for reuse elsewhere"
demo_try_cmd r4 project export

demo_step "List saved agent sessions"
demo_try_cmd r4 agent sessions

demo_step "List hardware profiles for team"
demo_try_cmd r4 team hardware

demo_step "List saved agent sessions (JSON)"
demo_try_cmd r4 agent sessions --json

demo_step "Search the codebase"
demo_try_cmd r4 search "find endpoint provisioning code" --scope code_only --depth shallow --max-steps 2

demo_step "Inspect the agent tool catalog"
demo_try_cmd r4 agent tools --search rag

echo
echo "Optional cleanup: ../99_cleanup/run_demo.sh --stop-endpoint"
