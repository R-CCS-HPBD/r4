"""Pins for sweep/evidence/explain.match bridge verbs (sweep batch 3, 2026-09-27)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import r4.sweep_templates
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


def _bridge(*, evidence=None) -> CompanionBridgeServer:
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
        list_evidence=MagicMock(return_value={"evidence": evidence or []}),
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


def test_sweep_templates_lists_the_pack_readonly(monkeypatch):
    rows = [
        SimpleNamespace(
            name="matgen",
            description="materials grid",
            required_params=lambda: ["lattice"],
            all_param_names=lambda: ["lattice", "steps"],
        ),
    ]
    monkeypatch.setattr(r4.sweep_templates, "list_templates", lambda: rows)
    out = _bridge().sweep_templates({})
    assert out == {
        "templates": [
            {
                "name": "matgen",
                "description": "materials grid",
                "required_params": ["lattice"],
                "params": ["lattice", "steps"],
            }
        ],
        "count": 1,
    }


def test_sweep_preview_dry_renders_without_writing(monkeypatch, tmp_path):
    template = SimpleNamespace(
        name="matgen",
        description="d",
        required_params=lambda: ["lattice"],
    )
    captured = {}

    def _fake_load(name):
        captured["loaded"] = name
        return template

    def _fake_render(tpl, combos):
        captured["combos"] = combos
        return [
            SimpleNamespace(
                index=0, combo={}, script="#!/bin/bash\necho hi", job_name="r4sweep-matgen-001"
            )
        ]

    monkeypatch.setattr(r4.sweep_templates, "load_template", _fake_load)
    monkeypatch.setattr(r4.sweep_templates, "render_plan", _fake_render)

    out = _bridge().sweep_preview({"name": "matgen"})

    assert captured["loaded"] == "matgen"
    assert captured["combos"] == [{"lattice": "<lattice>"}]
    assert out["script"].startswith("#!/bin/bash")
    assert out["job_name"] == "r4sweep-matgen-001"
    assert out["required_params"] == ["lattice"]


def test_sweep_preview_requires_name_and_surfaces_not_found(monkeypatch):
    with pytest.raises(ValueError, match="sweep.preview params.name is required"):
        _bridge().sweep_preview({})

    def _boom(name):
        raise FileNotFoundError(f"sweep template not found: {name}")

    monkeypatch.setattr(r4.sweep_templates, "load_template", _boom)
    with pytest.raises(FileNotFoundError, match="not found"):
        _bridge().sweep_preview({"name": "ghost"})


def test_explain_match_finds_a_real_id_inside_text():
    message = "Error [auth.forbidden] while calling /umrs/mcp/servers"
    out = _bridge().explain_match({"message": message})
    assert out["found"] is True
    assert out["error_id"] == "auth.forbidden"
    assert out["cause"] and out["remediation"]


def test_explain_match_ignores_unknown_tokens_and_empty_messages():
    out = _bridge().explain_match({"message": "no.such token in here"})
    assert out["found"] is False
    with pytest.raises(ValueError, match="message is required"):
        _bridge().explain_match({"message": ""})


def test_evidence_list_passes_filters_and_rows():
    rows = [{"id": "e1", "claim_id": "c1", "status": "retracted"}]
    bridge = _bridge(evidence=rows)
    out = bridge.evidence_list({"status": "retracted", "limit": 10})
    bridge.session.client.list_evidence.assert_called_once_with(status="retracted", limit=10)
    assert out == {"evidence": rows, "count": 1}


def test_traces_execution_hits_the_execution_route_and_wraps():
    bridge = _bridge()
    got = {}

    class _HTTP:
        def get(self, url, **kwargs):
            got["url"] = url
            got["kwargs"] = kwargs
            return {"traces": [{"name": "step-a", "status": "completed"}], "count": 1}

    bridge.session.client.http = _HTTP()
    bridge.session.client._config = SimpleNamespace(gateway_url="http://gateway.test")
    bridge.session.client._api_prefix = ""
    out = bridge.traces_execution({"execution_id": "exec-9"})
    assert got["url"].endswith("/traces/execution/exec-9")
    assert out["count"] == 1
    assert out["traces"][0]["name"] == "step-a"


def test_traces_execution_requires_id():
    import pytest

    with pytest.raises(ValueError, match="traces.execution requires execution_id"):
        _bridge().traces_execution({})
