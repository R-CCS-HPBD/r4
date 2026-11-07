"""ADR-105 D-1 (slice S1): the UMRS agent capability manifest.

Offline mirror of ``tools/umrs_agent_manifest.py --check``: the committed
``contracts/umrs-http/v1/agent-tools.json`` and ``agent-openapi.json`` are
what the pinned UMRS app and the allowlist produce. The builder itself is
pinned on synthetic specifications (unknown or untyped operations are
refused; identity headers never reach the agent surface). The r4 side of the
allowlist is ``r4/tests/test_umrs_agent_manifest_parity.py``.
"""

from __future__ import annotations

import json

from tools import umrs_agent_manifest as manifest

_REF = "#/components/schemas/"


def _op(op_id: str, *, body: dict | None = None, response: dict | None = None, headers=()) -> dict:
    op = {
        "operationId": op_id,
        "parameters": [{"in": "header", "name": h, "schema": {"type": "string"}} for h in headers],
        "responses": {"200": {"content": {"application/json": {"schema": response or {}}}}},
    }
    if body is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": body}}}
    return op


def _spec(paths: dict, schemas: dict | None = None) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "t"},
        "paths": paths,
        "components": {"schemas": schemas or {}},
    }


def _allow(*ids: str) -> dict:
    return {
        "version": 1,
        "operations": [
            {
                "operation_id": i,
                "tool": i,
                "risk": "safe",
                "taint": "none",
                "scope": "mcp-eligible",
                "site": False,
            }
            for i in ids
        ],
    }


def test_committed_artifacts_match_the_pinned_umrs():
    assert manifest.drifted() == [], "run: python tools/umrs_agent_manifest.py --write"


def test_every_allowlisted_operation_is_in_the_manifest():
    allow = json.loads(manifest.ALLOWLIST.read_text())
    built = json.loads((manifest.OUT_DIR / manifest.MANIFEST_NAME).read_text())
    assert [r["operation_id"] for r in built["operations"]] == sorted(
        r["operation_id"] for r in allow["operations"]
    )


def test_unknown_operation_is_a_problem():
    _, _, problems = manifest.build(_spec({}), _allow("ghost"))
    assert problems == ["ghost: no UMRS route declares this explicit operation_id"]


def test_untyped_request_and_response_are_refused():
    spec = _spec(
        {"/x": {"post": _op("x_run", body={"type": "object"}, response={"type": "object"})}}
    )
    _, _, problems = manifest.build(spec, _allow("x_run"))
    assert problems == [
        "x_run: untyped request body (type before expose)",
        "x_run: untyped success response (type before expose)",
    ]


def test_identity_headers_are_stripped_and_schemas_closed_over():
    schemas = {
        "Out": {"type": "object", "properties": {"inner": {"$ref": _REF + "Inner"}}},
        "Inner": {"type": "object", "properties": {"v": {"type": "string"}}},
        "Unused": {"type": "object", "properties": {}},
    }
    headers = ("x-user-id", "x-verified-project-id", "x-project-memberships-hmac", "x-request-id")
    spec = _spec(
        {"/x": {"get": _op("x_get", response={"$ref": _REF + "Out"}, headers=headers)}}, schemas
    )
    built, subset, problems = manifest.build(spec, _allow("x_get"))
    assert problems == []
    params = subset["paths"]["/x"]["get"]["parameters"]
    assert [p["name"] for p in params] == ["x-request-id"]
    assert sorted(subset["components"]["schemas"]) == ["Inner", "Out"]
    assert built["operations"][0]["response_schema"] == "Out"


def test_other_library_versions_compare_the_shape_not_the_text(monkeypatch):
    """A fastapi/pydantic upgrade elsewhere must not turn this lane red."""
    name = manifest.OPENAPI_NAME
    committed = json.dumps(
        {"info": {"x-generated-with": {"fastapi": "0"}}, "paths": {"/x": {"get": {}}}}
    )
    reworded = json.dumps({"info": {}, "paths": {"/x": {"get": {"summary": "new text"}}}})
    reshaped = json.dumps({"info": {}, "paths": {"/y": {"get": {}}}})
    monkeypatch.setattr(manifest, "generator_versions", lambda: {"fastapi": "1"})
    assert manifest._same(name, committed, reworded)
    assert not manifest._same(name, committed, reshaped)
    monkeypatch.setattr(manifest, "generator_versions", lambda: {"fastapi": "0"})
    assert not manifest._same(name, committed, reworded)


_IDENTITY_QUERY = ("caller_user_id", "caller_project_id", "is_admin", "verified_project_id")
_IDENTITY_HEADER_PREFIXES = ("x-user-", "x-verified-")


def _committed_params() -> list[tuple[str, str]]:
    doc = json.loads((manifest.OUT_DIR / manifest.OPENAPI_NAME).read_text())
    return [
        (op["operationId"], p["name"].lower())
        for item in doc["paths"].values()
        for op in item.values()
        for p in op.get("parameters", [])
    ]


def _is_identity(op_id: str, name: str) -> bool:
    if name in _IDENTITY_QUERY or name == "x-r4-session-id":
        return True
    if name.startswith(_IDENTITY_HEADER_PREFIXES) or name.endswith("-hmac"):
        return True
    return (op_id, name) == ("workflow_list", "user_id")


def test_committed_surface_has_no_identity_input():
    """S5 (2026-11-05): S1 stripped the identity headers only; the MVP query
    identity (``caller_*``, ``is_admin``), the MVP-only ``workflow_list``
    owner and the client-set session header reached the agent surface."""
    params = _committed_params()
    assert [row for row in params if _is_identity(*row)] == []
    # Real filters stay: an admin's project filter, the corpus, the session filter.
    assert {
        ("job_list", "project_id"),
        ("rag_list_documents", "project_id"),
        ("job_events", "session_id"),
    } <= set(params)


def test_identity_query_and_session_header_are_stripped():
    op = _op("workflow_list", headers=("x-r4-session-id", "x-request-id"))
    op["parameters"] += [
        {"in": "query", "name": n, "schema": {"type": "string"}}
        for n in (*_IDENTITY_QUERY, "user_id", "scope")
    ]
    _, subset, _ = manifest.build(_spec({"/w": {"get": op}}), _allow("workflow_list"))
    names = [p["name"] for p in subset["paths"]["/w"]["get"]["parameters"]]
    assert names == ["x-request-id", "scope"]


def test_user_id_is_kept_where_the_route_honours_it():
    op = _op("other_list")
    op["parameters"] = [{"in": "query", "name": "user_id", "schema": {"type": "string"}}]
    _, subset, _ = manifest.build(_spec({"/o": {"get": op}}), _allow("other_list"))
    assert [p["name"] for p in subset["paths"]["/o"]["get"]["parameters"]] == ["user_id"]


def test_the_mcp_package_mirror_is_byte_exact(tmp_path):
    """ADR-105 D-2 (S5): the MCP server reads its package copy at runtime."""
    assert manifest.mirror_drift() == []
    for name in (manifest.MANIFEST_NAME, manifest.OPENAPI_NAME):
        (tmp_path / name).write_text("{}\n")
    assert len(manifest.mirror_drift(manifest.OUT_DIR, tmp_path)) == 2
