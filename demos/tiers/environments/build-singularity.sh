#!/bin/bash
# =============================================================================
# Build Singularity Image for HPC
# =============================================================================
# Creates .sif image for HPC clusters.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SIF_NAME="${SIF_NAME:-r4-client.sif}"
DEFINITION_FILE="${SCRIPT_DIR}/client/r4-client.def"

echo "=== Building Singularity Image ==="
echo "Output: ${SIF_NAME}"
echo ""

# Check for Singularity/Apptainer
if command -v apptainer &> /dev/null; then
    SINGULARITY_CMD="apptainer"
elif command -v singularity &> /dev/null; then
    SINGULARITY_CMD="singularity"
else
    echo "ERROR: Neither Singularity nor Apptainer found"
    echo ""
    echo "On RCCS/HPC, load the module:"
    echo "  module load singularity"
    echo ""
    echo "Or install Apptainer:"
    echo "  https://apptainer.org/docs/admin/main/installation.html"
    exit 1
fi

echo "Using: ${SINGULARITY_CMD}"
echo ""

cd "${REPO_ROOT}"

# Build with fakeroot (no root required)
${SINGULARITY_CMD} build --fakeroot "${SIF_NAME}" "${DEFINITION_FILE}"

echo ""
echo "=== Build Complete ==="
echo ""
echo "To use:"
echo "  ${SINGULARITY_CMD} shell ${SIF_NAME}"
echo ""
echo "With gateway access:"
echo "  SINGULARITYENV_GATEWAY_URL=http://node:8080 ${SINGULARITY_CMD} shell ${SIF_NAME}"
