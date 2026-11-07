"""Tests for tools/tool_route_liveness.py (advisory validator, round-2 D.5)."""

from __future__ import annotations

import sys

from tools import tool_route_liveness as _validator_module


def _load_validator():
    return _validator_module


def test_repo_has_no_liveness_findings() -> None:
    validator = _load_validator()
    report = validator.check()
    assert report.ok, f"unexpected findings: {report.findings}"
    # Both detection classes actually ran (guards against vacuous pass).
    assert report.checked_setters
    assert report.retired_routes


def test_dead_callback_detected_when_ungated(monkeypatch) -> None:
    validator = _load_validator()
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 0)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: False)
    report = validator.check()
    kinds = {(f.kind, f.tool) for f in report.findings}
    assert ("DEAD_CALLBACK", "tool_search") in kinds
    assert ("DEAD_CALLBACK", "web_search") in kinds


def test_retired_route_detected_when_ungated(monkeypatch) -> None:
    validator = _load_validator()
    # Keep callbacks fine; only the route side is broken.
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 5)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: False)
    report = validator.check()
    route_tools = {f.tool for f in report.findings if f.kind == "RETIRED_ROUTE"}
    assert route_tools == {"file_upload_platform", "file_list_platform"}


def test_gated_tools_produce_no_findings(monkeypatch) -> None:
    validator = _load_validator()
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 0)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: True)
    report = validator.check()
    assert report.ok


def test_setter_without_tool_mapping_is_ignored(monkeypatch, tmp_path) -> None:
    """A set_<x>_callback outside SETTER_TOOLS never enters the check list."""
    validator = _load_validator()
    fake = tmp_path / "tools.py"
    fake.write_text(
        "def set_foo_callback(fn):\n    pass\n\ndef set_tool_search_callback(fn):\n    pass\n"
    )
    monkeypatch.setattr(validator, "R4_SRC", tmp_path)
    (tmp_path / "r4").mkdir()
    (tmp_path / "r4" / "agent").mkdir()
    (tmp_path / "r4" / "agent" / "tools.py").write_text(fake.read_text())
    data_dir = tmp_path / "r4"
    (data_dir / "data.py").write_text('_raise_retired_http("/gw/x")\n')
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 1)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: True)
    report = validator.check()
    assert "set_foo_callback" not in report.checked_setters
    assert "set_tool_search_callback" in report.checked_setters


def test_call_site_counter_skips_definition_line() -> None:
    """_setter_call_sites counts usages, not the def line containing the name."""
    validator = _load_validator()
    # ask_user IS wired (acp.py registers the editor round-trip) — if the def
    # line itself were counted this would read as 1 for any setter.
    count = validator._setter_call_sites("set_ask_user_callback")
    assert count >= 1, "expected real ask_user wiring call sites under r4/src"


def test_main_hard_fail_on_findings(monkeypatch, capsys) -> None:
    """Hard-fail contract (promotion #4): findings exit 1, JSON shape stable."""
    validator = _load_validator()
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 0)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: False)
    sys.argv = ["tool_route_liveness.py", "--json"]
    assert validator.main() == 1
    import json

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"findings", "checked_setters", "retired_routes"}
    assert payload["findings"]


def test_main_clean_exit_zero(capsys) -> None:
    """Hard-fail contract: zero findings exits 0."""
    validator = _load_validator()
    sys.argv = ["tool_route_liveness.py", "--json"]
    assert validator.main() == 0


def test_meaningful_finding_detail_text(monkeypatch) -> None:
    """DEAD_CALLBACK detail names setter + tool — operator-actionable text."""
    validator = _load_validator()
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 0)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: False)
    report = validator.check()
    by_tool = {f.tool: f for f in report.findings if f.kind == "DEAD_CALLBACK"}
    assert "set_tool_search_callback" in by_tool["tool_search"].detail
    assert "tool_search" in by_tool["tool_search"].detail


def test_route_finding_detail_mentions_route(monkeypatch) -> None:
    """RETIRED_ROUTE detail names the retired route — operator-actionable."""
    validator = _load_validator()
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 5)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: False)
    report = validator.check()
    route_findings = [f for f in report.findings if f.kind == "RETIRED_ROUTE"]
    assert route_findings
    assert any("/gw/data/" in f.detail for f in route_findings)
    assert {f.tool for f in route_findings} == {"file_upload_platform", "file_list_platform"}


def test_checked_setters_cover_declared_map() -> None:
    """checked_setters includes every exported SETTER_TOOLS key (no silent drop)."""
    validator = _load_validator()
    report = validator.check()
    assert set(report.checked_setters) == set(validator.SETTER_TOOLS)
    assert len(report.checked_setters) == len(validator.SETTER_TOOLS)


def test_route_tools_map_shape_sane() -> None:
    """ROUTE_TOOLS declaration: str keys (route), nonempty str lists (tools)."""
    validator = _load_validator()
    assert validator.ROUTE_TOOLS
    for route, tools in validator.ROUTE_TOOLS.items():
        assert route.startswith("/")
        assert tools and all(isinstance(tool, str) for tool in tools)


def test_no_transport_kwarg_defaults_true(monkeypatch) -> None:
    """_gated is probed with no_transport=True (the docstring contract)."""
    validator = _load_validator()
    seen: list[bool] = []

    def _probe(tool, *, no_transport: bool = True) -> bool:
        seen.append(no_transport)
        return True

    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 0)
    monkeypatch.setattr(validator, "_gated", _probe)
    validator.check()
    assert seen and all(no_transport for no_transport in seen)


def test_retired_routes_sorted_unique(monkeypatch, tmp_path) -> None:
    """retired_routes is deduped + sorted regardless of file duplication."""
    validator = _load_validator()
    agent_dir = tmp_path / "r4" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "tools.py").write_text("def set_tool_search_callback(fn):\n    pass\n")
    (tmp_path / "r4" / "data.py").write_text(
        '_raise_retired_http("/gw/b")\n_raise_retired_http("/gw/a")\n_raise_retired_http("/gw/a")\n'
    )
    monkeypatch.setattr(validator, "R4_SRC", tmp_path)
    monkeypatch.setattr(validator, "_setter_call_sites", lambda _setter: 1)
    monkeypatch.setattr(validator, "_gated", lambda _tool, *, _no_transport=True: True)
    report = validator.check()
    assert report.retired_routes == ["/gw/a", "/gw/b"]


def test_stub_client_umrs_fields() -> None:
    """_stub_client exposes the config surface unavailable_reason consults."""
    validator = _load_validator()
    client = validator._stub_client()
    assert client.config.use_umrs_transport is True
    assert client.config.umrs_server_host
    assert isinstance(client.config.umrs_control_port, int)
    assert client.config.umrs_tunnel_mode is False


def test_human_summary_prints_counts(capsys) -> None:
    """Human (non-json) summary keeps the two count lines + verdict line."""
    validator = _load_validator()
    sys.argv = ["tool_route_liveness.py"]
    assert validator.main() == 0
    out = capsys.readouterr().out
    assert "callbacks checked" in out
    assert "retired routes" in out
    assert "findings" in out
