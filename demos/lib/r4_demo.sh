#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "source this file from a demo script" >&2
    exit 1
fi

R4_DEMO_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R4_DEMOS_DIR="$(cd "$R4_DEMO_LIB_DIR/.." && pwd)"
R4_DEMO_STATE_DIR="${R4_DEMO_STATE_DIR:-${R4_DEMOS_DIR}/.state}"
R4_DEMO_ENDPOINT_FILE="${R4_DEMO_STATE_DIR}/endpoint_id"

DEMO_STEP=0
DEMO_AUTO=0
DEMO_PROJECT_ID=""
DEMO_MODEL_NAME=""
DEMO_ENDPOINT_ID=""
DEMO_MCP_SRC=""
DEMO_PROJECT_DATA_SRC=""
DEMO_RAG_INDEX_SRC=""
DEMO_ENDPOINT_ARGS=()

mkdir -p "$R4_DEMO_STATE_DIR"

demo_die() {
    echo "ERROR: $*" >&2
    exit 1
}

demo_info() {
    echo "[INFO] $*"
}

demo_ok() {
    echo "[OK] $*"
}

demo_note() {
    echo "[NOTE] $*"
}

demo_header() {
    local title="$1"
    local subtitle="$2"
    echo
    echo "======================================================================"
    echo "$title"
    echo "======================================================================"
    echo "$subtitle"
    echo
}

demo_step() {
    DEMO_STEP=$((DEMO_STEP + 1))
    echo
    echo "[$DEMO_STEP] $1"
    echo
}

demo_cmd() {
    printf '+ '
    printf '%q ' "$@"
    printf '\n'
    "$@"
}

demo_try_cmd() {
    printf '+ '
    printf '%q ' "$@"
    printf '\n'

    if "$@"; then
        return 0
    else
        local exit_code=$?
        demo_note "Command failed with exit code $exit_code. Continuing so the rest of the demo can still show the real r4 flow."
        return 0
    fi
}

demo_pause() {
    if [[ "$DEMO_AUTO" -eq 1 ]]; then
        return 0
    fi
    echo
    read -r -p "Press Enter to continue..." _unused
}

demo_scratch_project() {
    printf '%s-scratch\n' "$DEMO_PROJECT_ID"
}

demo_init() {
    DEMO_STEP=0
    DEMO_AUTO=0
    for arg in "$@"; do
        if [[ "$arg" == "--auto" ]]; then
            DEMO_AUTO=1
        fi
    done

    command -v r4 >/dev/null 2>&1 || demo_die "r4 is not installed or not on PATH."
    command -v python3 >/dev/null 2>&1 || demo_die "python3 is required for demo helpers."

    # Auto-detect the active profile so demos use the JWT-configured profile
    # instead of defaulting to the (possibly unauthenticated) "default" profile.
    if [[ -z "${R4_PROFILE:-}" ]]; then
        local detected
        detected="$(demo_config_value 'Profile')"
        if [[ -n "$detected" && "$detected" != "(not set)" ]]; then
            export R4_PROFILE="$detected"
            demo_note "Auto-detected profile: $detected"
        fi
    fi
}

demo_config_value() {
    local key="$1"
    r4 config show 2>/dev/null | awk -F': ' -v key="$key" '$1 ~ key {print $2; exit}'
}

available_demo_models_json() {
    local output
    if ! output="$(r4 model list --json --limit 100 2>&1)"; then
        return 1
    fi
    printf '%s\n' "$output"
}

model_is_available() {
    local model="$1"
    [[ -n "$model" ]] || return 1

    local models_json
    if ! models_json="$(available_demo_models_json)"; then
        return 1
    fi

    python3 -c '
import json
import sys

target = sys.argv[1]
try:
    models = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
for item in models:
    if item.get("type") != "llm":
        continue
    if item.get("status") not in (None, "", "available"):
        continue
    if item.get("name") == target:
        sys.exit(0)
sys.exit(1)
' "$model" <<<"$models_json" 2>/dev/null
}

pick_first_available_model() {
    local models_json
    if ! models_json="$(available_demo_models_json)"; then
        return 1
    fi

    python3 -c '
import json
import sys

try:
    models = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
for item in models:
    if item.get("type") != "llm":
        continue
    if item.get("status") not in (None, "", "available"):
        continue
    name = item.get("name")
    if name:
        print(name)
        break
' <<<"$models_json" 2>/dev/null
}

resolve_demo_project() {
    local project="${R4_DEMO_PROJECT:-}"
    if [[ -z "$project" ]]; then
        project="$(demo_config_value 'Project')"
    fi
    if [[ -z "$project" || "$project" == "(not set)" ]]; then
        project="demo-project"
    fi
    DEMO_PROJECT_ID="$project"
}

resolve_demo_model() {
    local model="${R4_DEMO_MODEL:-}"
    if [[ -z "$model" ]]; then
        model="$(demo_config_value 'Model')"
    fi
    if [[ "$model" == "(not set)" ]]; then
        model=""
    fi
    if [[ -n "$model" ]] && ! model_is_available "$model"; then
        demo_note "Configured model '$model' is not currently available; selecting a registered model instead."
        model=""
    fi
    if [[ -z "$model" ]]; then
        if ! model="$(pick_first_available_model)"; then
            model=""
        fi
    fi
    # Model registry is projected-scoped: a freshly-created demo project has a
    # narrow view, which previously killed every demo at this point. Fallback:
    # consult already-READY inference endpoints from `r4 endpoint list` and use
    # one of their models before giving up.
    if [[ -z "$model" ]]; then
        model="$(pick_first_ready_endpoint_model || true)"
    fi
    [[ -n "$model" ]] || demo_die "Unable to resolve a demo model. Set R4_DEMO_MODEL or start the Gateway/tunnel so 'r4 model list --json' succeeds."
    DEMO_MODEL_NAME="$model"
}

pick_first_ready_endpoint_model() {
    local output
    if ! output="$(r4 endpoint list --json 2>/dev/null)"; then
        return 1
    fi
    python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
rows = data if isinstance(data, list) else data.get("endpoints") or []
for item in rows:
    if not isinstance(item, dict):
        continue
    if str(item.get("status") or "").lower() != "ready":
        continue
    name = item.get("model") or item.get("model_id")
    if name:
        print(name)
        break
' <<<"$output" 2>/dev/null
}

# True when the venue has a ready LLM endpoint the step router can use (a row
# with status ready/active/running whose model_type is llm or unset). Demos 06
# and 07 run an all-LLM workflow; without one, nightly demo runs failed with
# "routing failed … no matching active endpoint" (2026-10-30).
demo_has_ready_llm_endpoint() {
    local output
    if ! output="$(r4 endpoint list --json 2>/dev/null)"; then
        return 1
    fi
    python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
rows = data if isinstance(data, list) else data.get("endpoints") or []
usable = {"ready", "active", "running"}
for item in rows:
    if not isinstance(item, dict):
        continue
    status = str(item.get("status") or "").lower()
    kind = str(item.get("model_type") or "llm").lower()
    if status in usable and kind == "llm":
        sys.exit(0)
sys.exit(1)
' <<<"$output" 2>/dev/null
}

ensure_demo_project() {
    resolve_demo_project
    if ! r4 project switch "$DEMO_PROJECT_ID" >/dev/null 2>&1; then
        demo_cmd r4 project create "$DEMO_PROJECT_ID" --name "R4 Demo Project"
    fi
    demo_cmd r4 project switch "$DEMO_PROJECT_ID"
    demo_cmd r4 config context --project "$DEMO_PROJECT_ID"
}

load_demo_endpoint_id() {
    if [[ -f "$R4_DEMO_ENDPOINT_FILE" ]]; then
        tr -d '\n' < "$R4_DEMO_ENDPOINT_FILE"
    fi
}

save_demo_endpoint_id() {
    printf '%s\n' "$1" > "$R4_DEMO_ENDPOINT_FILE"
}

clear_demo_endpoint_id() {
    rm -f "$R4_DEMO_ENDPOINT_FILE"
}

prepare_demo_endpoint_mounts() {
        local mount_root="${R4_DEMO_MOUNT_ROOT:-${R4_DEMO_STATE_DIR}/mounts}"
        DEMO_MCP_SRC="${R4_DEMO_MCP_SRC:-${mount_root}/mcp/demo-tools}"
        DEMO_PROJECT_DATA_SRC="${R4_DEMO_PROJECT_DATA_SRC:-${mount_root}/project-data}"
        DEMO_RAG_INDEX_SRC="${R4_DEMO_RAG_INDEX_SRC:-${mount_root}/rag-index}"

        mkdir -p "$DEMO_MCP_SRC" "$DEMO_PROJECT_DATA_SRC" "$DEMO_RAG_INDEX_SRC"

        cat > "${DEMO_MCP_SRC}/config.json" <<'JSON'
{
    "name": "r4-demo-mounted-tools",
    "version": "0.1.0",
    "capabilities": {
        "tools": ["summarize_mount"],
        "tool_capabilities": {
            "summarize_mount": ["READ_LOCAL_FILE"]
        },
        "egress": {
            "domains": ["api.example.com"],
            "protocols": ["https"]
        }
    },
    "tool_descriptions": {
        "summarize_mount": "Summarize the demo project data mounted into mini-RiVault."
    },
    "tool_schemas": {
        "summarize_mount": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Optional topic to emphasize in the summary."
                }
            }
        }
    },
    "entry_point": "python3 run_tool.py",
    "sandbox": {
        "network": "egress_guard",
        "filesystem": "read_only",
        "max_memory_mb": 256,
        "max_runtime_sec": 10
    }
}
JSON

        cat > "${DEMO_MCP_SRC}/run_tool.py" <<'PY'
from __future__ import annotations

import json
import sys

payload = json.load(sys.stdin)
arguments = payload.get("arguments", {}) if isinstance(payload, dict) else {}
topic = arguments.get("topic") or "mini-RiVault mounts"
print(json.dumps({
        "topic": topic,
        "summary": "The demo endpoint mounted MCP tools, project data, and a RAG index into mini-RiVault.",
}))
PY

        cat > "${DEMO_PROJECT_DATA_SRC}/demo-corpus.txt" <<'TEXT'
R4 demo project corpus.

This data directory is mounted read-only at /project inside mini-RiVault so
RAG services and custom MCP tools can inspect project-scoped data without
giving the Gateway or r4 client ownership of retrieval execution.
TEXT

        cat > "${DEMO_RAG_INDEX_SRC}/manifest.json" <<'JSON'
{
    "name": "r4-demo-rag-index",
    "description": "Placeholder pre-built RAG index mounted at /rag-index for the endpoint mount demo.",
    "documents": ["demo-corpus.txt"]
}
JSON
}

build_demo_endpoint_args() {
        DEMO_ENDPOINT_ARGS=()
        if [[ "${R4_DEMO_ENDPOINT_MOUNTS:-1}" = "0" ]]; then
                return 0
        fi

        prepare_demo_endpoint_mounts
        DEMO_ENDPOINT_ARGS+=(
                --tier "${R4_DEMO_ENDPOINT_TIER:-project}"
                --mount-mcp "${DEMO_MCP_SRC}:/mcp-servers/demo-tools"
                --mount-data "${DEMO_PROJECT_DATA_SRC}:/project"
                --mount-rag "${DEMO_RAG_INDEX_SRC}:/rag-index"
        )
        if [[ -n "${R4_DEMO_EGRESS_DOMAINS:-api.example.com}" ]]; then
                DEMO_ENDPOINT_ARGS+=(--egress-domains "${R4_DEMO_EGRESS_DOMAINS:-api.example.com}")
        fi
}

describe_demo_endpoint_mounts() {
        if [[ "${R4_DEMO_ENDPOINT_MOUNTS:-1}" = "0" ]]; then
                demo_note "Endpoint mount demo is disabled with R4_DEMO_ENDPOINT_MOUNTS=0."
                return 0
        fi
        prepare_demo_endpoint_mounts
        demo_info "MCP mount: ${DEMO_MCP_SRC} -> /mcp-servers/demo-tools"
        demo_info "Project data mount: ${DEMO_PROJECT_DATA_SRC} -> /project"
        demo_info "RAG index mount: ${DEMO_RAG_INDEX_SRC} -> /rag-index"
        demo_info "Egress domains: ${R4_DEMO_EGRESS_DOMAINS:-api.example.com}"
}

endpoint_status_is_active() {
    local instance_id="$1"
    [[ -n "$instance_id" ]] || return 1

    local status
    status="$(r4 endpoint status "$instance_id" --json 2>/dev/null | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
print((payload.get("status") or "").strip().lower())
')" || return 1

    case "$status" in
        failed|stopped|terminated|cancelled|"")
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

discover_demo_endpoint() {
    local discovered
    discovered="$(r4 endpoint list --json 2>/dev/null | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)

for endpoint in payload.get("endpoints", []):
    status = (endpoint.get("status") or "").strip().lower()
    if status in {"failed", "stopped", "terminated", "cancelled"}:
        continue
    instance_id = endpoint.get("instance_id")
    if instance_id:
        print(instance_id)
        break
')" || return 1

    [[ -n "$discovered" ]] || return 1
    if endpoint_status_is_active "$discovered"; then
        DEMO_ENDPOINT_ID="$discovered"
        save_demo_endpoint_id "$DEMO_ENDPOINT_ID"
        return 0
    fi
    return 1
}

load_active_demo_endpoint() {
    local recorded
    recorded="$(load_demo_endpoint_id || true)"
    if endpoint_status_is_active "$recorded"; then
        DEMO_ENDPOINT_ID="$recorded"
        return 0
    fi
    discover_demo_endpoint
}

ensure_demo_endpoint() {
    if load_active_demo_endpoint; then
        demo_ok "Reusing endpoint $DEMO_ENDPOINT_ID"
        return 0
    fi

    resolve_demo_model
    demo_note "No active endpoint found. Provisioning model '$DEMO_MODEL_NAME'."
    build_demo_endpoint_args
    printf '+ '
    printf '%q ' r4 endpoint up --model "$DEMO_MODEL_NAME" "${DEMO_ENDPOINT_ARGS[@]}" --wait --json
    printf '\n'

    local response
    response="$(r4 endpoint up --model "$DEMO_MODEL_NAME" "${DEMO_ENDPOINT_ARGS[@]}" --wait --json)"
    printf '%s\n' "$response"

    DEMO_ENDPOINT_ID="$(printf '%s' "$response" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
print(payload.get("instance_id", ""))
')"
    [[ -n "$DEMO_ENDPOINT_ID" ]] || demo_die "Endpoint provisioning returned no instance_id."

    save_demo_endpoint_id "$DEMO_ENDPOINT_ID"
    demo_ok "Provisioned endpoint $DEMO_ENDPOINT_ID"
}

stop_demo_endpoint() {
    if ! load_active_demo_endpoint; then
        demo_note "No active endpoint recorded for the demo suite."
        return 0
    fi

    demo_cmd r4 endpoint down "$DEMO_ENDPOINT_ID"
    clear_demo_endpoint_id
    demo_ok "Stopped endpoint $DEMO_ENDPOINT_ID"
}