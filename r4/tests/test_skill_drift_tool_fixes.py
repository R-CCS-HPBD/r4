"""Skill-drift tool fixes (ADR-105 S0, 2026-11-01).

The 2026-11-01 builtin-skill audit found tool behaviour the skills promised
but the tools did not deliver. Each test below fails on the old code:

- hpc_submit says "Track with hpc_job_status job-<hex>", but
  _hpc_durable_status only resolved all-digit ids;
- the off-cluster refusal named ``r4 job submit``, which is not an r4 verb;
- model_details left out the model's storage path;
- mcp_list_tools printed no input schema.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion import tools as tools_mod
from r4.mcp import MCPTool

DURABLE_ID = "job-0123456789ab"  # umrs generate_job_id: job-<12 hex>


def _execute(name: str, client: object):
    by_name = {t["name"]: t for t in tools_mod.get_builtin_tools(client=client)}
    return by_name[name]["execute"]


def test_durable_status_resolves_umrs_job_hex_ids(monkeypatch):
    client = SimpleNamespace(
        _api_prefix="/gw",
        get=MagicMock(return_value={"status": "running", "spec": {"script_path": "/x/run.sh"}}),
    )
    monkeypatch.setattr("r4.client.Client", lambda: client)

    out = tools_mod._hpc_durable_status(DURABLE_ID)

    assert out is not None
    assert client.get.call_args.args[0] == f"/gw/jobs/{DURABLE_ID}"
    assert f"Durable job {DURABLE_ID}: running" in out
    assert "r4 job describe" in out


def test_durable_status_keeps_scheduler_style_ids_on_legacy_lane():
    assert tools_mod._hpc_durable_status("job-XYZ") is None
    assert tools_mod._hpc_durable_status("12345_7") is None


def test_off_cluster_refusal_names_only_real_verbs():
    refusal = tools_mod._HPC_OFF_CLUSTER_REFUSAL
    assert "r4 job submit" not in refusal
    assert "r4 workflow trigger" in refusal
    assert "r4 endpoint up" in refusal


def test_model_details_returns_storage_path(monkeypatch):
    model = SimpleNamespace(
        model_id="m-1",
        name="qwen",
        scope=SimpleNamespace(value="project"),
        model_type=SimpleNamespace(value="llm"),
        status=SimpleNamespace(value="available"),
        description="",
        huggingface_id="Qwen/Qwen3-8B",
        storage_path="/models/project/qwen",
        created_at=None,
    )
    model_client = MagicMock()
    model_client.return_value.get_model.return_value = model
    monkeypatch.setattr("r4.models.ModelClient", model_client)

    payload = json.loads(_execute("model_details", MagicMock())("m-1"))

    assert payload["storage_path"] == "/models/project/qwen"


def test_mcp_list_tools_shows_compact_input_schema():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    client = MagicMock()
    client.mcp.list_tools.return_value = [
        MCPTool(name="read_file", description="Read a file", parameters=schema),
        MCPTool(name="ping", description="No args"),
    ]

    out = _execute("mcp_list_tools", client)()

    assert "- read_file: Read a file" in out
    assert 'input schema: {"properties":{"path":{"type":"string"}}' in out
    assert '"required":["path"]' in out
    assert "- ping: No args" in out and out.count("input schema:") == 1


def test_mcp_list_tools_bounds_schema_length():
    big = {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(200)}}
    line = tools_mod._mcp_tool_line(MCPTool(name="big", description="d", parameters=big))
    schema_text = line.split("input schema: ", 1)[1]
    assert schema_text.endswith("...(truncated)")
    assert len(schema_text) <= tools_mod._MCP_SCHEMA_MAX_CHARS + len("...(truncated)")
