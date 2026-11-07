#!/usr/bin/env bash
# Demo 08: Scientific Data Management
# Upload Blank Project artifact mix (or legacy CSV+PNG), wait for DATA_INGESTION,
# grep the catalog, raw-read, then run agentic search in lab_only scope.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE_DIR="$SCRIPT_DIR/fixture"
CSV_FILE="$SCRIPT_DIR/batch-3-yields.csv"
PNG_FILE="$SCRIPT_DIR/sample-chart.png"
CATALOG_HELPER="$SCRIPT_DIR/catalog_demo.py"
STRICT="${R4_DEMO_STRICT:-0}"
CATALOG_MODE="${R4_DEMO_CATALOG_MODE:-blank}"

source "$DEMOS_DIR/lib/r4_demo.sh"

if [[ "${R4_DEMO_CATALOG_DRY_RUN:-0}" == "1" ]]; then
    command -v python3 >/dev/null 2>&1 || demo_die "python3 is required for demo helpers."
    demo_header \
        "Demo 08: Scientific Data Management" \
        "Dry-run: validate Blank Project fixture (no gateway or r4 CLI)."
    demo_step "Dry-run: validate fixture and expected modalities"
    demo_cmd python3 "$CATALOG_HELPER" --mode "$CATALOG_MODE" --dry-run
    demo_ok "Dry-run passed (no gateway required)."
    exit 0
fi

demo_init "$@"

demo_header \
    "Demo 08: Scientific Data Management" \
    "Upload Blank Project artifacts, wait for catalog ingestion, search metadata, raw-read bytes, and run r4 search --scope lab_only."

ensure_demo_project
ensure_demo_endpoint

CATALOG_PREFIX="${R4_DEMO_CATALOG_PREFIX:-}"
if [[ -z "$CATALOG_PREFIX" ]]; then
    if [[ "$CATALOG_MODE" == "blank" ]]; then
        CATALOG_PREFIX="demo-08-blank-${R4_DEMO_RUN_ID:-$(date +%s)}"
    else
        CATALOG_PREFIX="demo-08-${R4_DEMO_RUN_ID:-$(date +%s)}"
    fi
fi

if [[ "$CATALOG_MODE" == "blank" ]]; then
    demo_step "Upload Blank Project mix (papers, notebooks, code, CSV, README)"
    HELPER_ARGS=(--mode blank --fixture-dir "$FIXTURE_DIR" --prefix "$CATALOG_PREFIX")
else
    demo_step "Upload CSV + PNG (legacy mode)"
    HELPER_ARGS=(--mode legacy --csv "$CSV_FILE" --png "$PNG_FILE" --query "batch-3" --prefix "$CATALOG_PREFIX")
fi

if [[ "$STRICT" == "1" ]]; then
    demo_cmd python3 "$CATALOG_HELPER" \
        "${HELPER_ARGS[@]}" \
        --timeout "${R4_DEMO_CATALOG_TIMEOUT_SEC:-300}"
else
    demo_try_cmd python3 "$CATALOG_HELPER" \
        "${HELPER_ARGS[@]}" \
        --timeout "${R4_DEMO_CATALOG_TIMEOUT_SEC:-300}"
fi

demo_step "List recent jobs (expect DATA_INGESTION when job API exposes it)"
demo_try_cmd r4 job list --limit 5

demo_step "Agentic search over lab data (catalog + raw read; no web)"
demo_try_cmd r4 search \
    "Which batch had the highest yield in the batch-3 CSV uploads?" \
    --scope lab_only \
    --project "$DEMO_PROJECT_ID" \
    --depth shallow \
    --max-steps 4 \
    --no-web

echo
echo "Expected:"
if [[ "$CATALOG_MODE" == "blank" ]]; then
    echo "  - 15 papers + 2 notebooks + code + CSV + README ingested (pending → ready)"
    echo "  - catalog search returns document, notebook, code, structured, text modalities"
else
    echo "  - batch-3-yields.csv appears in catalog with yield column signals"
fi
echo "  - raw read returns CSV header row"
echo "  - r4 search --scope lab_only cites catalog s3_key (no web sources)"
echo
echo "Next: ../99_cleanup/run_demo.sh --stop-endpoint (optional)"
