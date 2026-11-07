#!/usr/bin/env bash

generate_gateway_bearer_token() {
    local jwt_secret="$1"
    local project_id="${2:-demo-project}"
    local user_id="${3:-demo-user}"
    local roles="${4:-user}"

    python3 - "$jwt_secret" "$project_id" "$user_id" "$roles" <<'PY'
import base64
import hashlib
import hmac
import json
import sys
import time

secret, project_id, user_id, roles = sys.argv[1:5]
header = {"alg": "HS256", "typ": "JWT"}
payload = {
    "project_id": project_id,
    "sub": user_id,
    "roles": [role for role in roles.split(",") if role],
    "exp": int(time.time()) + 86400,
}

def encode(value):
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

message = f"{encode(header)}.{encode(payload)}"
signature = hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
token = f"{message}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"
print(token)
PY
}

resolve_gateway_bearer_token() {
    if [[ -n "${GATEWAY_BEARER_TOKEN:-}" ]]; then
        printf '%s\n' "$GATEWAY_BEARER_TOKEN"
        return 0
    fi

    local jwt_secret="${JWT_SECRET:-dev-secret-change-in-production}"
    local project_id="${GATEWAY_PROJECT_ID:-demo-project}"
    local user_id="${GATEWAY_USER_ID:-demo-user}"
    local roles="${GATEWAY_ROLES:-user}"

    generate_gateway_bearer_token "$jwt_secret" "$project_id" "$user_id" "$roles"
}