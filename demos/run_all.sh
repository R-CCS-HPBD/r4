#!/usr/bin/env bash
# Run all r4 demos in order (00 through 08).
# Use --auto to skip interactive pauses.
# Use --destroy to tear down the endpoint after the suite.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AUTO_MODE=""
DESTROY_ENDPOINT=0

for arg in "$@"; do
    case "$arg" in
        --auto)
            AUTO_MODE="--auto"
            ;;
        --destroy)
            DESTROY_ENDPOINT=1
            ;;
    esac
done

echo
echo "======================================================================"
echo "Running The r4 Demo Suite"
echo "======================================================================"
echo

DEMOS=(
    "00_onboarding"
    "01_chat"
    "02_rag"
    "03_projects_models"
    "04_endpoints_jobs"
    "05_mcp_workflows"
    "06_complex_workflow"
    "07_ask"
    "08_scientific_data_management"
)

passed=0
failed=0

for demo in "${DEMOS[@]}"; do
    echo "Running: $demo/run_demo.sh"
    echo

    if bash "$SCRIPT_DIR/$demo/run_demo.sh" $AUTO_MODE; then
        passed=$((passed + 1))
        echo "[OK] Completed: $demo"
    else
        failed=$((failed + 1))
        echo "[NOTE] Failed: $demo"
        if [[ "$demo" == "00_onboarding" && "${R4_DEMO_CONTINUE_AFTER_BOOTSTRAP_FAILURE:-0}" != "1" ]]; then
            echo "[NOTE] Onboarding prepares the project, model, and endpoint used by later demos; stopping early."
            echo "[NOTE] Set R4_DEMO_CONTINUE_AFTER_BOOTSTRAP_FAILURE=1 to continue anyway."
            break
        fi
    fi

    if [[ -z "$AUTO_MODE" ]]; then
        echo
        read -r -p "Press Enter to continue to the next demo..." _unused
    fi

    echo
done

if [[ "$DESTROY_ENDPOINT" -eq 1 ]]; then
    echo "Running: 99_cleanup/run_demo.sh --stop-endpoint"
    echo
    if bash "$SCRIPT_DIR/99_cleanup/run_demo.sh" --stop-endpoint $AUTO_MODE; then
        passed=$((passed + 1))
        echo "[OK] Completed: 99_cleanup"
    else
        failed=$((failed + 1))
        echo "[NOTE] Failed: 99_cleanup"
    fi
    echo
fi

echo "======================================================================"
echo "Summary: $passed completed, $failed failed"
echo "======================================================================"
echo
