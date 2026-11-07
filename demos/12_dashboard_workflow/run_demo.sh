#!/usr/bin/env bash
# 12 — Dashboard & Workflow Visualization (2026-08 wave)
# Mirrors the README flow; requires a configured r4 profile.
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DEMO_DIR/../.." && pwd)"
LIB="$ROOT/demos/lib"

[[ -f "$LIB/r4_demo.sh" ]] && source "$LIB/r4_demo.sh" || true

step() { printf '\n=== %s\n' "$1"; }

step "1. Unified dashboard"
r4 dashboard
r4 dashboard --jobs || true

step "2. Workflow graph rendering"
WORKFLOW_NAME="${WORKFLOW_NAME:-demo-ingest}"
if r4 workflow list --json | grep -q "\"$WORKFLOW_NAME\""; then
  r4 workflow show "$WORKFLOW_NAME" --graph
  r4 workflow show "$WORKFLOW_NAME" --graph --mermaid
else
  echo "No $WORKFLOW_NAME registered — skipping graph examples"
fi

step "3. Data intake board"
r4 data intake

step "4. Persistence note"
echo "All records above are durable (Postgres): executions survive restart."
