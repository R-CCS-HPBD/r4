# =============================================================================
# MCP Servers Dockerfile
# =============================================================================
# Multi-purpose image for running various MCP servers.
#
# Build Args:
#   SERVER_TYPE: filesystem | database | webfetch | code | platform | rag-proxy | custom
#
# Build context: the ai4s-platform repository ROOT. `r4-contracts` is an
# unpublished sibling package (ADR-005), so the image installs it from the
# checkout instead of PyPI (ADR-045 D3: HPC venues stage this image as an
# archive built from the checkout alone — see umrs/deploy/_common/stage_images.sh).
#
# Usage (from the repository root):
#   podman build -f mcp-servers/Dockerfile -t localhost/r4-mcp:latest .
#   podman build -f mcp-servers/Dockerfile --build-arg SERVER_TYPE=filesystem -t mcp-filesystem .
#   docker run -v ./data:/data:ro -e MCP_ROOT_PATH=/data mcp-filesystem
#
# Environment Variables:
#   SERVER_TYPE       - Server type to run (filesystem, database, webfetch, code)
#   MCP_ROOT_PATH     - Filesystem server root directory (default: /data)
#   MCP_READONLY      - Enable read-only mode (default: true)
#   MCP_DSN           - Database connection string (database server)
#   MCP_ALLOWLIST     - Comma-separated allowed domains (webfetch server)
#   MCP_TIMEOUT       - Request/query timeout seconds (default: 30)
#   MCP_MAX_SIZE      - Max file/content size in bytes
#   MCP_MAX_RESULTS   - Max database result rows (default: 1000)
#   MCP_CONFIG_DIR    - Custom MCP manifest mount path (custom server)
#   MCP_TRANSPORT     - MCP transport: stdio | sse (custom server)
# =============================================================================

FROM python:3.11-alpine3.22 AS base

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
RUN apk add --no-cache curl

# r4-contracts first (stdlib-only, unpublished): satisfies the
# `r4-contracts>=0.1.0` dependency below without a registry.
COPY r4-contracts/ /tmp/r4-contracts/
RUN pip install --no-cache-dir /tmp/r4-contracts && rm -rf /tmp/r4-contracts

# Copy source and install
COPY mcp-servers/pyproject.toml mcp-servers/README.md ./
COPY mcp-servers/src/ ./src/

# Install with all extras to support all server types
RUN pip install -e ".[all]"

# Default server type (can be overridden at build time or runtime)
ARG SERVER_TYPE=filesystem
ENV SERVER_TYPE=${SERVER_TYPE}

# Default environment variables
ENV MCP_ROOT_PATH=/data \
    MCP_READONLY=true \
    MCP_DSN="" \
    MCP_ALLOWLIST="" \
    MCP_TIMEOUT=30 \
    MCP_MAX_SIZE=10485760 \
    MCP_MAX_RESULTS=1000 \
    MCP_CONFIG_DIR=/mcp-servers \
    MCP_TRANSPORT=stdio \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080

# Create data directory
RUN mkdir -p /app/data

# Copy entrypoint script
COPY mcp-servers/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && \
    adduser -D -h /home/r4mcp r4mcp && \
    chown -R r4mcp:r4mcp /app /home/r4mcp

USER r4mcp

ENTRYPOINT ["/app/entrypoint.sh"]
