"""MCP workflow tool surface checks."""

from r4_mcp.servers.platform import PlatformServer
from r4_mcp.workflow_policy import load_policy


def test_mcp_policy_defaults_when_unconfigured():
    """Default posture (2026-08-30): allow locally; veto via policy file."""
    policy = load_policy()
    assert policy["list"] is True
    assert policy["upload"] is True


def test_mcp_policy_missing_file_keeps_reads_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(tmp_path / "missing.json"))
    policy = load_policy()
    assert policy["list"] is True
    assert policy["describe"] is True
    assert policy["run"] is False
    assert policy["upload"] is False


def test_platform_server_exposes_full_workflow_lifecycle():
    server = PlatformServer()
    tool_names = {tool.name for tool in server.tools}
    for name in (
        "workflow_list",
        "workflow_describe",
        "workflow_run",
        "workflow_trigger",
        "workflow_status",
        "workflow_wait",
        "workflow_results",
        "workflow_cancel",
    ):
        assert name in tool_names


def test_policy_guard_payload_names_surface_and_remediation(monkeypatch, tmp_path):
    """2026-08-30 misdiagnosis guard: deny must say evaluation is client-side."""
    import json

    from r4_mcp.servers.platform import PlatformServer

    restrictive = tmp_path / "policy.json"
    restrictive.write_text(
        json.dumps({"version": "1", "defaults": {"upload": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(restrictive))

    server = PlatformServer.__new__(PlatformServer)
    result = server._workflow_policy_guard("upload")
    assert result is not None
    payload = json.loads(result.content)

    assert payload["error"] == "policy_denied"
    assert payload["surface"] == "mcp_platform_server.local_policy"
    assert "no" in payload["message"] and "server-side deny" in payload["message"]
    assert "R4_WORKFLOW_POLICY_FILE" in payload["remediation"]
