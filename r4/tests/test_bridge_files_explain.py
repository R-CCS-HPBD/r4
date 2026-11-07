"""Pins for the files + explain bridge verbs (TUI/rccs function sweep, 2026-09-27).

Named refusals for missing params; the upload/download payloads pass the
FilesClient result through; explain answers come from the registry, never
invented.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import r4.files
from r4.companion.permissions import PermissionManager
from r4.companion.session_models import UsageSummary
from r4.companion.skills import SkillRegistry
from r4.companion.ui.bridge_server import CompanionBridgeServer


class _FakeStore:
    def __init__(self, session):
        self.session = session

    def create_session(self, client, name=None, *, model=None):
        self.session.client = client
        return self.session

    def load(self, session_id, client):
        self.session.client = client
        return self.session

    def save(self, session):
        return None

    def list_sessions(self, limit=50):
        return []


def _bridge() -> CompanionBridgeServer:
    client = SimpleNamespace(
        config=SimpleNamespace(
            profile="research",
            gateway_url="http://gateway.test",
            project_id="demo",
            model="config-model",
            endpoint_url=None,
            endpoint_provider=None,
        ),
        readiness=MagicMock(return_value={"ready": True}),
        list_endpoints=MagicMock(return_value={"endpoints": []}),
    )
    session = SimpleNamespace(
        session_id="comp-1",
        name="Demo",
        client=client,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        agent=SimpleNamespace(model="test-model"),
        messages=[],
        skills=SkillRegistry(),
        permissions=PermissionManager(),
        context=SimpleNamespace(_memory={}),
        usage_summary=UsageSummary(),
        active_endpoints=[],
        metadata={},
        _harness=None,
        send=lambda text, **kwargs: "answer",
        request_turn_cancel=MagicMock(),
        usage_report=MagicMock(return_value={"total_tokens": 0}),
        export_markdown=MagicMock(return_value="# Demo"),
    )
    return CompanionBridgeServer(client=client, store=_FakeStore(session))


def _patch_files_client(monkeypatch) -> SimpleNamespace:
    fake = SimpleNamespace(
        upload_file=MagicMock(
            return_value={
                "key": "demo/clips/sample.mp4",
                "bucket": "r4-data",
                "uri": "s3://r4-data/demo/clips/sample.mp4",
                "size": 12,
            }
        ),
        download_file=MagicMock(return_value=None),
    )
    monkeypatch.setattr(r4.files, "FilesClient", lambda *a, **k: fake)
    return fake


def test_files_upload_passes_params_and_payload(monkeypatch):
    fake = _patch_files_client(monkeypatch)
    bridge = _bridge()

    out = bridge.files_upload({"path": "/tmp/sample.mp4", "key": "clips/sample.mp4"})

    fake.upload_file.assert_called_once_with(
        "/tmp/sample.mp4", key="clips/sample.mp4", purpose="general"
    )
    assert out == {
        "ok": True,
        "key": "demo/clips/sample.mp4",
        "bucket": "r4-data",
        "uri": "s3://r4-data/demo/clips/sample.mp4",
        "size": 12,
    }


def test_files_upload_refuses_missing_path_by_name():
    with pytest.raises(ValueError, match="files.upload params.path is required"):
        _bridge().files_upload({})


def test_files_download_requires_dest_and_rides_the_client(monkeypatch):
    fake = _patch_files_client(monkeypatch)
    bridge = _bridge()

    with pytest.raises(ValueError, match="dest is required"):
        bridge.files_download({"key": "demo/x.bin"})

    out = bridge.files_download({"key": "demo/x.bin", "dest": "/tmp/x.bin"})
    fake.download_file.assert_called_once_with("demo/x.bin", dest="/tmp/x.bin")
    assert out["ok"] is True
    assert out["key"] == "demo/x.bin"
    assert out["dest"] == "/tmp/x.bin"


def test_explain_known_id_returns_cause_remediation_and_meta():
    out = _bridge().explain({"error_id": "auth.failed"})
    assert out["found"] is True
    assert out["error_id"] == "auth.failed"
    assert out["cause"]
    assert out["remediation"]
    assert out["exit_code"] == 10
    assert out["retryable"] is False


def test_explain_unknown_id_is_an_honest_miss_with_hint():
    out = _bridge().explain({"error_id": "no.such"})
    assert out["found"] is False
    assert "hint" in out
    assert "cause" not in out


def test_explain_requires_error_id():
    with pytest.raises(ValueError, match="explain params.error_id is required"):
        _bridge().explain({})
