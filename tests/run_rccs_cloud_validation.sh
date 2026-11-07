#!/usr/bin/env bash
# RCCS-cloud deployment validation (Phase 2l, 2026-04-27).
#
# Validates the two-tier client → UMRS data transfer model on a real
# RCCS-cloud deployment:
#
#   1. Tier A (HTTP via Gateway) — accessible to external users
#      without SSH access to the cluster.
#   2. Tier B (ZMQ via SSH tunnel) — for users with cluster SSH;
#      verifies UMRS binds ZMQ to 127.0.0.1 only.
#
# Usage:
#   ./tests/run_rccs_cloud_validation.sh \
#       --gateway https://gateway.r-ccs.riken.jp \
#       --ssh-host login.r-ccs.riken.jp \
#       --umrs-host umrs.internal
#
# Exit codes:
#   0  All checks passed
#   1  A check failed
#   2  Bad arguments / missing prereqs

set -euo pipefail

GATEWAY=""
SSH_HOST=""
UMRS_HOST=""
ZMQ_PORT="${ZMQ_PORT:-5555}"
TOKEN="${R4_API_TOKEN:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gateway)   GATEWAY="$2";  shift 2 ;;
        --ssh-host)  SSH_HOST="$2"; shift 2 ;;
        --umrs-host) UMRS_HOST="$2"; shift 2 ;;
        --zmq-port)  ZMQ_PORT="$2"; shift 2 ;;
        --token)     TOKEN="$2";    shift 2 ;;
        -h|--help)
            sed -n '1,30p' "$0"; exit 0 ;;
        *)
            echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

[[ -z "$GATEWAY"  ]] && { echo "--gateway required" >&2; exit 2; }
[[ -z "$SSH_HOST" ]] && { echo "--ssh-host required" >&2; exit 2; }

echo "=== RCCS-cloud validation ==="
echo "  Gateway: $GATEWAY"
echo "  SSH host: $SSH_HOST"
echo "  UMRS host: ${UMRS_HOST:-<localhost on cluster>}"
echo "  ZMQ port: $ZMQ_PORT"
echo

fail=0
check() {
    local name="$1"; shift
    if "$@"; then
        echo "  ✓ $name"
    else
        echo "  ✗ $name"
        fail=1
    fi
}

# -----------------------------------------------------------------------
# Tier A: external HTTP path via Gateway
# -----------------------------------------------------------------------
echo "[Tier A] HTTP via Gateway"

check "gateway /health reachable" \
    bash -c "curl -fsS --max-time 10 '$GATEWAY/health' >/dev/null"

if [[ -n "$TOKEN" ]]; then
    tmpfile="$(mktemp)"
    head -c 1048576 /dev/urandom > "$tmpfile"  # 1 MiB
    check "gateway accepts streaming PUT (1 MiB)" \
        bash -c "curl -fsS --max-time 60 -X PUT \
            -H 'Authorization: Bearer $TOKEN' \
            -H 'Content-Type: application/octet-stream' \
            --data-binary @'$tmpfile' \
            '$GATEWAY/gw/upload?path=validation/test.bin' >/dev/null"
    rm -f "$tmpfile"
else
    echo "  - skipping authenticated upload (no R4_API_TOKEN)"
fi

# -----------------------------------------------------------------------
# Tier B: SSH-tunnel ZMQ path
# -----------------------------------------------------------------------
echo
echo "[Tier B] ZMQ via SSH tunnel (binding invariant)"

# CRITICAL invariant: UMRS must NOT expose ZMQ on 0.0.0.0.
# We log into the cluster and inspect the listening sockets.
remote_check() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_HOST" "$@"
}

check "ssh to $SSH_HOST works" remote_check "true"

# `ss` (or `netstat` fallback) on the UMRS host must show port bound to 127.0.0.1
listen_cmd="(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | grep -E ':${ZMQ_PORT}\\b' || true"
listen_target="$SSH_HOST"
if [[ -n "$UMRS_HOST" ]]; then
    listen_target="$UMRS_HOST"
fi

echo "  Inspecting $ZMQ_PORT bind on $listen_target..."
if [[ "$listen_target" == "$SSH_HOST" ]]; then
    listening=$(remote_check "$listen_cmd" || true)
else
    listening=$(remote_check "ssh -o BatchMode=yes -o ConnectTimeout=10 '$UMRS_HOST' '$listen_cmd'" || true)
fi
echo "$listening"

if echo "$listening" | grep -qE "127\\.0\\.0\\.1:${ZMQ_PORT}\\b"; then
    echo "  ✓ ZMQ port $ZMQ_PORT bound to 127.0.0.1 (Phase 2l invariant)"
elif echo "$listening" | grep -qE "0\\.0\\.0\\.0:${ZMQ_PORT}\\b|\\*:${ZMQ_PORT}\\b"; then
    echo "  ✗ ZMQ port $ZMQ_PORT bound to 0.0.0.0 — VIOLATES Phase 2l invariant" >&2
    echo "    Set UMRS_TUNNEL_MODE=true in the UMRS server environment." >&2
    fail=1
else
    echo "  ! ZMQ port $ZMQ_PORT not currently listening (UMRS may be down)"
    fail=1
fi

echo
if [[ $fail -eq 0 ]]; then
    echo "✓ All RCCS-cloud validation checks passed"
    exit 0
else
    echo "✗ One or more validation checks failed"
    exit 1
fi
