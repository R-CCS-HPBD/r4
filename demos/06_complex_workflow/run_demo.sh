#!/usr/bin/env bash
# Demo 06: Complex Workflow
# Upload, trigger, and inspect a five-step UMRS DAG through the real r4
# workflow commands.  The workflow.json source lives in this directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$DEMOS_DIR/lib/r4_demo.sh"

# Source workflow bundle is co-located; a working copy is prepared under
# the state dir so the model placeholder can be substituted without
# modifying the committed file.
WORKFLOW_SRC_DIR="$SCRIPT_DIR"
WORKFLOW_DIR="${R4_DEMO_COMPLEX_WORKFLOW_DIR:-${R4_DEMO_STATE_DIR}/workflows/complex-dag}"
WORKFLOW_NAME="${R4_DEMO_COMPLEX_WORKFLOW_NAME:-r4-complex-dag-demo}"

demo_init "$@"

demo_header \
    "Demo 06: Complex Workflow" \
    "Upload, trigger, and inspect a five-step UMRS DAG through the real r4 workflow commands."

ensure_demo_project

workflow_model="${R4_DEMO_MODEL:-$(demo_config_value 'Model')}"
if [[ -z "$workflow_model" || "$workflow_model" == "(not set)" ]]; then
    workflow_model="rivault-gpt-oss-static"
fi

prepare_complex_workflow_bundle() {
    # Copy the source bundle and substitute the model placeholder.
    rm -rf "$WORKFLOW_DIR"
    mkdir -p "$WORKFLOW_DIR"
    sed "s/{{model}}/$workflow_model/g" \
        "$WORKFLOW_SRC_DIR/workflow.json" > "$WORKFLOW_DIR/workflow.json"
}

preview_complex_workflow_dag() {
    python3 - "$WORKFLOW_DIR/workflow.json" <<'PY'
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

workflow = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
steps = workflow["steps"]
name_to_index = {step["name"]: index for index, step in enumerate(steps)}
children = defaultdict(list)
in_degree = [0] * len(steps)

for index, step in enumerate(steps):
    for dependency in step.get("depends_on") or []:
        dep_index = name_to_index[dependency]
        children[dep_index].append(index)
        in_degree[index] += 1

queue = deque(index for index, degree in enumerate(in_degree) if degree == 0)
levels = []
while queue:
    level = list(queue)
    levels.append(level)
    queue.clear()
    for node in level:
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

print(f"Workflow: {workflow['name']} v{workflow.get('version', '1.0')}")
for level_number, level in enumerate(levels):
    names = [steps[index]["name"] for index in level]
    suffix = " (parallel)" if len(names) > 1 else ""
    print(f"  Level {level_number}: {', '.join(names)}{suffix}")
PY
}

trigger_complex_workflow() {
    local trigger_output
    local execution_id
    local trigger_args=(
        workflow trigger "$WORKFLOW_NAME"
        --project "$DEMO_PROJECT_ID"
        --param "project_id=$DEMO_PROJECT_ID"
        --param "sample_id=demo-complex-001"
        --param "question=prepare a reproducible RAG and model-evaluation workflow"
        --json
    )

    printf '+ r4 '
    printf '%q ' "${trigger_args[@]}"
    printf '\n'

    if trigger_output="$(r4 "${trigger_args[@]}" 2>&1)"; then
        printf '%s\n' "$trigger_output"
    else
        local exit_code=$?
        printf '%s\n' "$trigger_output"
        demo_note "Workflow trigger failed with exit code $exit_code. Continuing so the upload and registry flow remain visible."
        return 0
    fi

    execution_id="$(python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)
value = payload.get("execution_id")
if value:
    print(value)
' <<<"$trigger_output")"

    if [[ -n "$execution_id" ]]; then
        demo_step "Inspect workflow execution status"
        demo_try_cmd r4 workflow status "$execution_id"
    else
        demo_note "The trigger response did not include an execution_id to inspect."
    fi
}

demo_step "Prepare workflow bundle from source data"
prepare_complex_workflow_bundle
demo_info "Source: $WORKFLOW_SRC_DIR/workflow.json"
demo_info "Working copy: $WORKFLOW_DIR"
demo_info "Workflow name: $WORKFLOW_NAME"
demo_info "LLM model: $workflow_model"

demo_step "Preview the DAG levels"
preview_complex_workflow_dag

demo_step "Upload the workflow bundle"
demo_try_cmd r4 workflow upload "$WORKFLOW_DIR" --name "$WORKFLOW_NAME"

demo_step "List the registered complex workflow"
demo_try_cmd r4 workflow list --name "$WORKFLOW_NAME"

demo_step "Trigger the complex workflow"
if demo_has_ready_llm_endpoint; then
    trigger_complex_workflow
else
    demo_note "Skipped: every step of this workflow needs an LLM endpoint and this venue has no ready one (start one in Demo 04, or: r4 endpoint up). The upload and registry steps above still ran."
fi

echo
echo "Next: ../07_ask/run_demo.sh"
