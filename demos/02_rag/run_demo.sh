#!/usr/bin/env bash
# Demo 02: RAG
# Upload a document, inspect indexed documents, and exercise the RAG
# management commands through the current project context.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_FILE="$SCRIPT_DIR/attention_is_all_you_need.txt"

# shellcheck source=/dev/null  # runtime-resolved helper, same pattern as every demos/*/run_demo.sh
source "$DEMOS_DIR/lib/r4_demo.sh"

demo_init "$@"

demo_header \
    "Demo 02: RAG" \
    "Upload a document, inspect indexed documents, and exercise the RAG management commands through the current project context."

ensure_demo_project
ensure_demo_endpoint

demo_step "Review the mini-RiVault RAG mount inputs"
describe_demo_endpoint_mounts

demo_step "Upload a document into the active project"
demo_try_cmd r4 rag upload "$DATA_FILE" --project "$DEMO_PROJECT_ID"

demo_step "List indexed documents"
demo_try_cmd r4 rag docs list --project "$DEMO_PROJECT_ID"

demo_step "Run a RAG query"
demo_try_cmd r4 rag query "What does the paper say about attention?" --project "$DEMO_PROJECT_ID" --top-k 3

demo_step "Inspect available retriever components"
demo_try_cmd r4 rag retriever components embedders

echo
echo "Next: ../03_projects_models/run_demo.sh"
