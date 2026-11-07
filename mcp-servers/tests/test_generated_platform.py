"""ADR-105 D-2 (slice S5): MCP platform tools generated from the UMRS manifest.

Pins that the generated family is exactly the manifest's ``mcp-eligible``
rows (companion-only rows never appear), that no identity input reaches an
``inputSchema``, that calls are built right (method, gateway URL, query,
JSON body, Bearer token; seen through ``httpx.MockTransport``), that the
local workflow policy guard still applies (and fails closed for an unmapped
``workflow_*`` tool), and that the hand-written residual is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from r4_mcp.base import MCPServer, ToolDefinition, ToolResult
from r4_mcp.servers import generated_platform as gen
from r4_mcp.servers.platform import PlatformServer

GATEWAY = "http://gateway-test:8000"
OLD_TOOL_NAMES = {
    "endpoint_list",
    "endpoint_status",
    "endpoint_provision",
    "endpoint_stop",
    "job_list",
    "job_status",
    "job_cancel",
    "workflow_list",
    "workflow_describe",
    "workflow_run",
    "workflow_trigger",
    "workflow_status",
    "workflow_wait",
    "workflow_results",
    "workflow_cancel",
    "workflow_upload",
    "model_list",
    "model_get",
    "platform_health",
    "platform_guide",
}
# Hand-written residual (no manifest row yet): parameter names and required set.
RESIDUAL = {
    "endpoint_list": ({"model", "include_stopped"}, []),
    "endpoint_status": ({"job_id"}, ["job_id"]),
    "endpoint_provision": ({"model", "gpu_count", "ttl_sec"}, ["model"]),
    "endpoint_stop": ({"job_id", "force"}, ["job_id"]),
    "workflow_trigger": ({"workflow_name", "parameters", "input_files"}, ["workflow_name"]),
    "workflow_wait": ({"execution_id", "timeout_sec"}, ["execution_id"]),
    "workflow_upload": ({"workflow_name", "archive_path"}, ["workflow_name", "archive_path"]),
    "model_list": ({"scope"}, []),
    "model_get": ({"model_name"}, ["model_name"]),
    "platform_health": (set(), []),
    "platform_guide": ({"topic"}, []),
}
IDENTITY_NAMES = {"caller_user_id", "caller_project_id", "is_admin", "user_id"}


class _Recorder:
    """MockTransport handler recording every request."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = {"ok": True} if body is None else body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def only(self) -> httpx.Request:
        assert len(self.requests) == 1
        return self.requests[0]


def _server(recorder: _Recorder) -> PlatformServer:
    return PlatformServer(
        gateway_url=GATEWAY,
        api_key="test-key",
        http_transport=httpx.MockTransport(recorder),
    )


async def _call(server: PlatformServer, name: str, arguments: dict) -> dict:
    return await server.handle_call_tool({"name": name, "arguments": arguments})


def _contract() -> tuple[dict, dict]:
    return gen.load_contract()


def _rows(scope: str | None = None) -> list[dict]:
    rows = _contract()[0]["operations"]
    return [r for r in rows if scope is None or r["scope"] == scope]


# ── the generated set ────────────────────────────────────────────────────


def test_generated_tools_are_the_mcp_eligible_rows():
    ops, skipped = gen.build_operations(*_contract())
    eligible = {r["operation_id"] for r in _rows("mcp-eligible")}
    assert {op.name for op in ops} == eligible - set(skipped)
    assert set(skipped) == {"rag_upload"}
    assert "multipart/form-data" in skipped["rag_upload"]


def test_companion_only_rows_never_become_tools():
    companion = {r["operation_id"] for r in _rows("companion-only")}
    companion |= {r["tool"] for r in _rows("companion-only")}
    assert "workflow_commit" in companion  # ADR-105 D-4: MCP has no approval ladder
    names = {t.name for t in PlatformServer().tools}
    assert not names & companion


def test_a_companion_only_row_is_skipped_even_when_supported():
    manifest, openapi = _contract()
    rows = [{**r, "scope": "companion-only"} for r in manifest["operations"]]
    ops, skipped = gen.build_operations({"operations": rows}, openapi)
    assert ops == [] and skipped == {}


def test_tool_set_keeps_every_old_name():
    names = {t.name for t in PlatformServer().tools}
    assert OLD_TOOL_NAMES <= names


def test_input_schemas_carry_no_identity_input():
    for op in gen.build_operations(*_contract())[0]:
        props = set(op.input_schema["properties"])
        assert not props & IDENTITY_NAMES, op.name
        assert not [p for p in props if p.startswith(("x-", "x_", "verified_"))], op.name
        assert op.input_schema["additionalProperties"] is False


def test_mvp_only_body_identity_and_legacy_alias_are_hidden():
    by_name = {op.name: op for op in gen.build_operations(*_contract())[0]}
    run = by_name["workflow_run"].input_schema
    assert set(run["properties"]) == {"workflow_name", "parameters", "input_files"}
    assert run["required"] == ["workflow_name"]
    assert run["properties"]["parameters"]["type"] == "object"
    assert run["properties"]["input_files"] == {
        "items": {"type": "string"},
        "type": "array",
        "description": "Optional input file keys",
    }
    # A real corpus selector stays (checked against the ADR-080 access list).
    assert "project_id" in by_name["rag_query"].input_schema["required"]


def test_refs_are_inlined_and_cycles_stay_valid():
    schemas = {
        "Body": {"type": "object", "properties": {"inner": {"$ref": "#/components/schemas/In"}}},
        "In": {"type": "object", "properties": {"self": {"$ref": "#/components/schemas/In"}}},
    }
    resolved = gen._inline({"$ref": "#/components/schemas/Body"}, schemas)
    inner = resolved["properties"]["inner"]
    assert inner["properties"]["self"] == {"type": "object"}
    assert "$ref" not in json.dumps(resolved)


def test_descriptions_carry_risk_and_taint():
    by_name = {op.name: op for op in gen.build_operations(*_contract())[0]}
    assert "risk: elevated; taint: none" in by_name["workflow_run"].description
    assert "taint: rag_chunk" in by_name["rag_query"].description
    assert "# auth" not in by_name["workflow_run"].description


def test_package_contract_matches_the_repo_contract():
    repo = Path(__file__).resolve().parents[2] / "contracts" / "umrs-http" / "v1"
    if not repo.is_dir():
        pytest.skip("not a repository checkout")
    for name in (gen.MANIFEST_NAME, gen.OPENAPI_NAME):
        packaged = Path(gen.__file__).resolve().parents[1] / "contracts" / name
        assert packaged.read_bytes() == (repo / name).read_bytes(), name


# ── calls ────────────────────────────────────────────────────────────────


async def test_workflow_run_posts_the_json_body_to_the_gateway():
    rec = _Recorder(body={"execution_id": "e1"})
    result = await _call(
        _server(rec),
        "workflow_run",
        {"workflow_name": "alpha", "parameters": {"k": 1}, "input_files": ["a.txt"]},
    )
    assert not result["isError"]
    req = rec.only
    assert req.method == "POST"
    assert str(req.url) == f"{GATEWAY}/gw/workflows/alpha/trigger"
    assert req.headers["Authorization"] == "Bearer test-key"
    assert json.loads(req.content) == {"parameters": {"k": 1}, "input_files": ["a.txt"]}
    assert json.loads(result["content"][0]["text"]) == {"execution_id": "e1"}


async def test_query_parameters_and_path_quoting():
    rec = _Recorder()
    server = _server(rec)
    await _call(server, "job_list", {"status": "RUNNING", "limit": 5})
    await _call(server, "job_status", {"job_id": "a/b", "include_attempts": False})
    await _call(server, "job_cancel", {"job_id": "j1", "reason": "stop"})
    first, second, third = rec.requests
    assert (first.method, first.url.path) == ("GET", "/gw/jobs")
    assert dict(first.url.params) == {"status": "RUNNING", "limit": "5"}
    assert second.url.raw_path.decode() == "/gw/jobs/a%2Fb?include_attempts=false"
    assert (third.method, third.url.path) == ("POST", "/gw/jobs/j1/cancel")
    assert dict(third.url.params) == {"reason": "stop"}
    assert first.content == b"" and third.content == b""


async def test_rag_query_sends_its_body():
    rec = _Recorder()
    await _call(_server(rec), "rag_query", {"query": "q", "project_id": "p1", "top_k": 3})
    assert rec.only.url.path == "/gw/rag/query"
    assert json.loads(rec.only.content) == {"query": "q", "project_id": "p1", "top_k": 3}


async def test_http_error_is_a_tool_error_with_status_and_body():
    rec = _Recorder(status=404, body={"detail": "Job not found"})
    result = await _call(_server(rec), "job_status", {"job_id": "nope"})
    assert result["isError"]
    text = result["content"][0]["text"]
    assert text.startswith("HTTP 404") and "Job not found" in text


async def test_bad_arguments_never_reach_the_gateway():
    rec = _Recorder()
    server = _server(rec)
    missing = await _call(server, "workflow_run", {"parameters": {}})
    extra = await _call(server, "job_list", {"caller_user_id": "mallory"})
    assert missing["isError"] and "missing required" in missing["content"][0]["text"]
    assert extra["isError"] and "caller_user_id" in extra["content"][0]["text"]
    assert rec.requests == []


async def test_dot_segments_never_reach_the_gateway():
    """`..` would turn /gw/workflows/{name} into another route."""
    rec = _Recorder()
    server = _server(rec)
    for bad in ("..", "."):
        out = await _call(server, "workflow_describe", {"workflow_name": bad})
        assert out["isError"] and "not a valid path value" in out["content"][0]["text"]
    assert rec.requests == []


async def test_workflow_trigger_alias_delegates_to_generated_run():
    rec = _Recorder()
    await _call(_server(rec), "workflow_trigger", {"workflow_name": "alpha"})
    assert rec.only.url.path == "/gw/workflows/alpha/trigger"
    assert json.loads(rec.only.content) == {"parameters": {}}


# ── workflow policy guard ────────────────────────────────────────────────


def _restrict(monkeypatch, tmp_path, defaults: dict) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": "1", "defaults": defaults}), encoding="utf-8")
    monkeypatch.setenv("R4_WORKFLOW_POLICY_FILE", str(policy))


async def test_policy_guard_applies_to_generated_workflow_tools(monkeypatch, tmp_path):
    # A policy file's defaults replace the built-in ones wholesale.
    _restrict(monkeypatch, tmp_path, {"list": True, "run": False, "describe": False})
    rec = _Recorder()
    server = _server(rec)
    run = await _call(server, "workflow_run", {"workflow_name": "alpha"})
    plan = await _call(server, "workflow_plan", {"workflow_name": "alpha"})
    listed = await _call(server, "workflow_list", {})
    assert json.loads(run["content"][0]["text"])["operation"] == "run"
    assert json.loads(plan["content"][0]["text"])["operation"] == "describe"
    assert not listed["isError"]
    assert [r.url.path for r in rec.requests] == ["/gw/workflows"]


async def test_non_workflow_tools_are_not_policy_guarded(monkeypatch, tmp_path):
    _restrict(monkeypatch, tmp_path, {op: False for op in gen.WORKFLOW_POLICY_OPERATIONS.values()})
    rec = _Recorder()
    result = await _call(_server(rec), "job_list", {})
    assert not result["isError"]


async def test_unmapped_workflow_tool_fails_closed():
    row = {
        "operation_id": "workflow_mystery",
        "tool": "workflow_mystery",
        "method": "GET",
        "path": "/umrs/workflows/mystery",
        "risk": "safe",
        "taint": "none",
        "scope": "mcp-eligible",
    }
    op = gen.build_operation(row, {"responses": {}}, {})
    assert op.policy_operation == "workflow_mystery"
    rec = _Recorder()
    result = await gen.make_handler(op, _server(rec))()
    assert result.is_error
    assert json.loads(result.content)["error"] == "policy_denied"
    assert rec.requests == []


# ── base + residual ─────────────────────────────────────────────────────


def test_raw_input_schema_is_returned_verbatim():
    schema = {"type": "object", "properties": {"a": {"type": "array", "items": {}}}}
    tool = ToolDefinition(name="t", description="d", input_schema=schema)
    assert tool.to_mcp_format()["inputSchema"] is schema


def test_register_tool_refuses_a_second_definition():
    class _S(MCPServer):
        name = "s"

    server = _S()

    async def handler(**_: object) -> ToolResult:
        return ToolResult.success("x")

    server.register_tool(ToolDefinition(name="t", description="d", handler=handler))
    with pytest.raises(ValueError):
        server.register_tool(ToolDefinition(name="t", description="d", handler=handler))
    with pytest.raises(ValueError):
        server.register_tool(ToolDefinition(name="u", description="d"))


def test_residual_tools_are_unchanged():
    by_name = {t.name: t.to_mcp_format()["inputSchema"] for t in PlatformServer().tools}
    for name, (props, required) in RESIDUAL.items():
        assert set(by_name[name]["properties"]) == props, name
        assert by_name[name]["required"] == required, name
