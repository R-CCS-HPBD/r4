#!/bin/sh
# =============================================================================
# MCP Server Entrypoint
# =============================================================================
# Selects and runs the appropriate MCP server based on SERVER_TYPE env var.
# =============================================================================

set -e

# Command passthrough (ADR-045, 2026-09-07): when the container is given an
# explicit program — compose `command: python -m r4_mcp.egress_guard ...`, the
# custom-MCP runner command, or `podman run IMAGE python -c ...` — run it as is.
# Only a bare start (or one beginning with an option) goes through the
# SERVER_TYPE dispatch below. Without this every such command was appended to
# `r4-mcp <SERVER_TYPE> ...` and rejected as "unrecognized arguments".
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ] && command -v "$1" >/dev/null 2>&1; then
  exec "$@"
fi

case "${SERVER_TYPE}" in
  filesystem)
    ARGS="filesystem --root ${MCP_ROOT_PATH:-/data}"
    [ "${MCP_READONLY}" = "true" ] && ARGS="${ARGS} --readonly"
    [ -n "${MCP_MAX_SIZE}" ] && ARGS="${ARGS} --max-size ${MCP_MAX_SIZE}"
    ;;
  database)
    if [ -z "${MCP_DSN}" ]; then
      echo "ERROR: MCP_DSN is required for database server"
      exit 1
    fi
    ARGS="database --dsn ${MCP_DSN}"
    [ "${MCP_READONLY}" = "true" ] && ARGS="${ARGS} --readonly"
    [ -n "${MCP_TIMEOUT}" ] && ARGS="${ARGS} --timeout ${MCP_TIMEOUT}"
    [ -n "${MCP_MAX_RESULTS}" ] && ARGS="${ARGS} --max-results ${MCP_MAX_RESULTS}"
    ;;
  webfetch)
    ARGS="webfetch"
    [ -n "${MCP_ALLOWLIST}" ] && ARGS="${ARGS} --allowlist ${MCP_ALLOWLIST}"
    [ -n "${MCP_MAX_SIZE}" ] && ARGS="${ARGS} --max-size ${MCP_MAX_SIZE}"
    [ -n "${MCP_TIMEOUT}" ] && ARGS="${ARGS} --timeout ${MCP_TIMEOUT}"
    ;;
  code)
    ARGS="code"
    [ -n "${MCP_TIMEOUT}" ] && ARGS="${ARGS} --timeout ${MCP_TIMEOUT}"
    ;;
  platform)
    ARGS="platform"
    [ -n "${GATEWAY_URL}" ] && ARGS="${ARGS} --gateway-url ${GATEWAY_URL}"
    [ -n "${GATEWAY_API_KEY}" ] && ARGS="${ARGS} --api-key ${GATEWAY_API_KEY}"
    [ -n "${MCP_TIMEOUT}" ] && ARGS="${ARGS} --timeout ${MCP_TIMEOUT}"
    ;;
  rag-proxy)
    ARGS="rag-proxy"
    [ -n "${GATEWAY_URL}" ] && ARGS="${ARGS} --gateway-url ${GATEWAY_URL}"
    [ -n "${GATEWAY_API_KEY}" ] && ARGS="${ARGS} --api-key ${GATEWAY_API_KEY}"
    [ -n "${R4_PROJECT_ID}" ] && ARGS="${ARGS} --project-id ${R4_PROJECT_ID}"
    [ -n "${MCP_TIMEOUT}" ] && ARGS="${ARGS} --timeout ${MCP_TIMEOUT}"
    ;;
  vision)
    ARGS="vision"
    [ -n "${VISION_MODEL_PATH}" ] && ARGS="${ARGS} --model-path ${VISION_MODEL_PATH}"
    [ -n "${VISION_DEVICE}" ] && ARGS="${ARGS} --device ${VISION_DEVICE}"
    [ -n "${VISION_OUTPUT_DIR}" ] && ARGS="${ARGS} --output-dir ${VISION_OUTPUT_DIR}"
    [ -n "${VISION_MAX_IMAGE_SIZE}" ] && ARGS="${ARGS} --max-image-size ${VISION_MAX_IMAGE_SIZE}"
    ;;
  custom)
    ARGS="custom --config-dir ${MCP_CONFIG_DIR:-/mcp-servers}"
    [ -n "${MCP_TRANSPORT}" ] && ARGS="${ARGS} --transport ${MCP_TRANSPORT}"
    [ -n "${MCP_HOST}" ] && ARGS="${ARGS} --host ${MCP_HOST}"
    [ -n "${MCP_PORT}" ] && ARGS="${ARGS} --port ${MCP_PORT}"
    [ "${MCP_VALIDATE_ONLY}" = "true" ] && ARGS="${ARGS} --validate-only"
    ;;
  *)
    echo "ERROR: Unknown SERVER_TYPE: ${SERVER_TYPE}"
    echo "Valid types: filesystem, database, webfetch, code, platform, rag-proxy, vision, custom"
    exit 1
    ;;
esac

# shellcheck disable=SC2086  # ARGS is an intentionally word-split argument string
exec r4-mcp ${ARGS} "$@"
