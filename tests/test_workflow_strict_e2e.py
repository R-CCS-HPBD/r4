"""Strict-mode workflow E2E through NGINX + auth-sidecar → UMRS.

Requires the docker-compose.e2e.yaml stack with UMRS_AUTH_MODE=strict.

  E2E_TEST_MODE=true JWT_SECRET=test-secret-for-e2e \\
    pytest tests/test_workflow_strict_e2e.py -m e2e -q
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile

import pytest

httpx = pytest.importorskip("httpx")

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
JWT_SECRET = os.environ.get("JWT_SECRET", "test-secret-for-e2e")


def _e2e_enabled() -> bool:
    return os.environ.get("E2E_TEST_MODE", "").lower() == "true"


def _gateway_reachable() -> bool:
    try:
        response = httpx.get(f"{GATEWAY_URL}/health", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


def gateway_auth_headers(*, user: str, project: str) -> dict[str, str]:
    from jose import jwt as jose_jwt

    token = jose_jwt.encode(
        {
            "sub": user,
            "project_id": project,
            "exp": int(time.time()) + 3600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


pytestmark = pytest.mark.e2e


def _minimal_workflow_zip(name: str = "e2e-alpha") -> bytes:
    workflow = {
        "name": name,
        "version": "1.0",
        "description": "verify-rollout e2e fixture",
        "source": {"type": "local_path", "path": "."},
        "parameter_schema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}},
            "required": ["bucket"],
        },
        "steps": [
            {"name": "step-1", "entrypoint": "e2e_steps:StepOne", "depends_on": []},
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflow.json", json.dumps(workflow))
        zf.writestr("e2e_steps.py", "class StepOne:\n    pass\n")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def gateway_client():
    if not _e2e_enabled() and not _gateway_reachable():
        pytest.skip(
            f"Set E2E_TEST_MODE=true and start docker-compose.e2e.yaml (gateway at {GATEWAY_URL})"
        )
    if not _gateway_reachable():
        pytest.skip(f"Gateway not reachable at {GATEWAY_URL}")
    with httpx.Client(base_url=GATEWAY_URL, timeout=60.0) as client:
        yield client


def _error_blob(response) -> str:
    """Flatten gateway/UMRS error payloads for substring assertions."""
    try:
        body = response.json()
    except Exception:
        return response.text
    return json.dumps(body)


def _upload_workflow(client, headers: dict[str, str], workflow_name: str) -> None:
    upload = client.post(
        "/gw/workflows/upload",
        headers=headers,
        data={"user_id": "alice", "workflow_name": workflow_name, "scope": "user"},
        files={
            "file": (
                f"{workflow_name}.zip",
                _minimal_workflow_zip(workflow_name),
                "application/zip",
            )
        },
    )
    assert upload.status_code in (200, 202), upload.text
    upload_body = upload.json()
    assert upload_body.get("accepted") is True or upload_body.get("status") in (
        "ok",
        "accepted",
        "registered",
    )


def _assert_describe_isolation(
    client, alice: dict[str, str], bob: dict[str, str], workflow_name: str
) -> None:
    describe = client.get(f"/gw/workflows/{workflow_name}", headers=alice)
    assert describe.status_code == 200, describe.text
    schema = describe.json().get("parameter_schema") or {}
    assert "bucket" in (schema.get("required") or [])

    bob_describe = client.get(f"/gw/workflows/{workflow_name}", headers=bob)
    assert bob_describe.status_code in (403, 404), bob_describe.text


def _trigger_owned_execution(
    client, alice: dict[str, str], bob: dict[str, str], workflow_name: str
) -> str:
    trigger = client.post(
        f"/gw/workflows/{workflow_name}/trigger",
        headers=alice,
        json={
            "user_id": "alice",
            "params": {
                "bucket": "demo-bucket",
                "project_id": "evil-should-be-overridden",
            },
        },
    )
    assert trigger.status_code == 200, trigger.text
    trigger_body = trigger.json()
    execution_id = trigger_body.get("execution_id")
    assert execution_id, trigger_body
    params = trigger_body.get("params") or {}
    if "project_id" in params:
        assert params["project_id"] == "project-a"

    bob_status = client.get(
        f"/gw/workflows/executions/{execution_id}",
        headers=bob,
    )
    assert bob_status.status_code == 403, bob_status.text
    assert "workflow_access_denied" in _error_blob(bob_status)
    return execution_id


def _poll_execution_status(client, alice: dict[str, str], execution_id: str) -> None:
    terminal = {"succeeded", "failed", "cancelled", "error", "completed"}
    for _ in range(30):
        status_resp = client.get(
            f"/gw/workflows/executions/{execution_id}",
            headers=alice,
        )
        assert status_resp.status_code == 200, status_resp.text
        payload = status_resp.json()
        execution = payload.get("execution") or payload
        if execution.get("status") in terminal:
            break
        time.sleep(0.5)


def _assert_results_and_cancel(
    client, alice: dict[str, str], bob: dict[str, str], execution_id: str
) -> None:
    results = client.get(
        f"/gw/workflows/executions/{execution_id}/results",
        headers=alice,
    )
    assert results.status_code == 200, results.text
    results_body = results.json()
    assert "artifacts" in results_body or "results" in results_body

    cancel = client.post(
        f"/gw/workflows/executions/{execution_id}/cancel",
        headers=alice,
    )
    assert cancel.status_code in (200, 409), cancel.text

    bob_cancel = client.post(
        f"/gw/workflows/executions/{execution_id}/cancel",
        headers=bob,
    )
    assert bob_cancel.status_code == 403, bob_cancel.text
    assert "workflow_access_denied" in _error_blob(bob_cancel)


def test_unauthenticated_workflow_list_rejected(gateway_client):
    response = gateway_client.get("/gw/workflows")
    # Sidecar may 401/403 or nginx may redirect to web login.
    assert response.status_code in (401, 403, 302)


def test_invalid_jwt_rejected(gateway_client):
    response = gateway_client.get(
        "/gw/workflows",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert response.status_code in (401, 403)


def test_upload_trigger_wait_results_cancel_lifecycle(gateway_client):
    alice = gateway_auth_headers(user="alice", project="project-a")
    bob = gateway_auth_headers(user="bob", project="project-b")
    workflow_name = f"e2e-alpha-{int(time.time())}"

    _upload_workflow(gateway_client, alice, workflow_name)
    _assert_describe_isolation(gateway_client, alice, bob, workflow_name)
    execution_id = _trigger_owned_execution(gateway_client, alice, bob, workflow_name)
    _poll_execution_status(gateway_client, alice, execution_id)
    _assert_results_and_cancel(gateway_client, alice, bob, execution_id)


def test_trigger_missing_required_params_returns_validation_error(gateway_client):
    alice = gateway_auth_headers(user="alice", project="project-a")
    workflow_name = f"e2e-validate-{int(time.time())}"

    _upload_workflow(gateway_client, alice, workflow_name)

    response = gateway_client.post(
        f"/gw/workflows/{workflow_name}/trigger",
        headers=alice,
        json={"user_id": "alice", "params": {}},
    )
    assert response.status_code == 400, response.text
    blob = _error_blob(response)
    assert "workflow_parameters_invalid" in blob
    assert "bucket" in blob
