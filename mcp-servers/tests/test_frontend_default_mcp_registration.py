"""frontend_default_mcp.py registers through the gateways (2026-09-25).

UMRS accepts registry writes only with a gateway-verified caller identity;
the launcher POSTed straight to the UMRS ports (401) and exited 0, so the
default set had not re-registered since July. It now registers via each
loopback gateway with the owner's token and exits 3 when registration is
incomplete (the systemd unit keeps healthy runtimes on 3).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "frontend_default_mcp.py"


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("frontend_default_mcp", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_ensure_runtime", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_fetch_tools", lambda port: [{"name": "t1"}])
    monkeypatch.setattr(mod, "_load_capabilities", lambda *a: {})
    monkeypatch.setattr(mod, "_load_contract", lambda *a: {})
    return mod


def test_registers_through_every_gateway_with_the_token(launcher, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        launcher, "_http_json", lambda url, **kw: calls.append((url, kw.get("token"))) or {}
    )
    healthy, registered = launcher._process_package(
        tmp_path, "platform-tools", [50080, 50082], {}, "tok"
    )
    assert (healthy, registered) == (True, True)
    assert calls == [
        ("http://127.0.0.1:50080/gw/mcp/servers", "tok"),
        ("http://127.0.0.1:50082/gw/mcp/servers", "tok"),
    ]


def test_no_token_means_not_registered_and_no_direct_umrs_post(launcher, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(launcher, "_http_json", lambda url, **kw: calls.append(url) or {})
    assert launcher._process_package(tmp_path, "rag-search", [50080], {}, "") == (True, False)
    assert calls == []


def test_a_refusing_gateway_leaves_registration_incomplete(launcher, monkeypatch, tmp_path):
    import urllib.error

    def refuse(url, **kw):
        if ":50084/" in url:
            raise urllib.error.URLError("401 Unauthorized")
        return {}

    monkeypatch.setattr(launcher, "_http_json", refuse)
    result = launcher._process_package(tmp_path, "knowledge-tools", [50080, 50084], {}, "tok")
    assert result == (True, False)


def test_exit_codes(launcher):
    assert launcher._exit_code([(True, True), (True, True)]) == 0
    assert launcher._exit_code([(True, True), (True, False)]) == 3
    assert launcher._exit_code([(False, False), (True, True)]) == 1


def test_register_token_is_preferred_and_never_reaches_runtimes(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("UMRS_MCP_REGISTER_TOKEN", "admin-tok")
    monkeypatch.setenv("R4_GATEWAY_TOKEN", "tool-tok")
    captured = {}

    class _Proc:
        pid = 4242

    def fake_popen(argv, **kw):
        captured["env"] = kw["env"]
        return _Proc()

    repo = tmp_path
    (repo / "r4/src/r4/integrations").mkdir(parents=True)
    (repo / "r4/src/r4/integrations/hatch_runtime.py").write_text("")
    (repo / "mcp-servers/hatch-registry/packages/rag-search").mkdir(parents=True)
    (repo / "mcp-servers/hatch-registry/packages/rag-search/server.py").write_text("")
    monkeypatch.setattr(launcher, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    launcher._launch_runtime(repo, "rag-search", 8172)
    assert "UMRS_MCP_REGISTER_TOKEN" not in captured["env"]
    assert captured["env"]["R4_GATEWAY_TOKEN"] == "tool-tok"
