"""ADR-105 S1 / D-3: typed ``workflow_run``, the schema pre-check, workflow
hits in ``tool_search``, and the CLI describe parameter listing."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import WorkflowCatalogItem, WorkflowResult

from r4._cli.workflow import _print_description, _schema_parameter_rows
from r4.companion import tool_search as tool_search_mod
from r4.companion.tool_search import register_session_tool_search, search_workflows
from r4.companion.workflow_tools import build_workflow_tools

SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "integer", "default": 10}, "mode": {"type": "string"}},
    "required": ["steps"],
    "additionalProperties": False,
}


@pytest.fixture
def client():
    c = MagicMock()
    c.config = SimpleNamespace(project_id="project-alpha")
    c.workflows.describe.return_value = {"workflow_name": "relax", "parameter_schema": SCHEMA}
    c.workflows.trigger.return_value = WorkflowResult(
        execution_id="exec-1",
        workflow_name="relax",
        status="submitted",
        artifacts=[],
        outputs={},
        error=None,
    )
    return c


def _tools(client) -> dict[str, dict]:
    return {t["name"]: t for t in build_workflow_tools(client)}


def test_run_schema_is_typed(client):
    props = _tools(client)["workflow_run"]["parameters"]["properties"]
    assert props["parameters"]["type"] == "object"
    assert props["input_files"] == {
        **props["input_files"],
        "type": "array",
        "items": {"type": "string"},
    }


def test_run_accepts_an_object(client):
    """Old code json.loads()'d the dict and raised TypeError."""
    out = _tools(client)["workflow_run"]["execute"](workflow_name="relax", parameters={"steps": 3})
    assert json.loads(out)["execution_id"] == "exec-1"
    assert client.workflows.trigger.call_args.kwargs["parameters"] == {"steps": 3}


def test_run_still_accepts_a_json_string(client):
    _tools(client)["workflow_run"]["execute"](workflow_name="relax", parameters='{"steps": 3}')
    assert client.workflows.trigger.call_args.kwargs["parameters"] == {"steps": 3}


def test_precheck_refuses_with_every_error_and_the_schema(client):
    out = json.loads(
        _tools(client)["workflow_run"]["execute"](
            workflow_name="relax", parameters={"steps": "three", "colour": "red"}
        )
    )
    assert out["code"] == "workflow_parameters_invalid"
    assert [e["path"] for e in out["details"]["errors"]] == ["/steps", "/colour"]
    assert out["details"]["parameter_schema"] == SCHEMA
    client.workflows.trigger.assert_not_called()


def test_precheck_lets_input_files_through(client):
    _tools(client)["workflow_run"]["execute"](
        workflow_name="relax", parameters={"steps": 1}, input_files=["data/a.cif"]
    )
    assert client.workflows.trigger.call_args.kwargs["input_files"] == ["data/a.cif"]


def test_describe_failure_defers_to_the_server(client):
    client.workflows.describe.side_effect = RuntimeError("down")
    _tools(client)["workflow_run"]["execute"](workflow_name="relax", parameters={"x": 1})
    client.workflows.trigger.assert_called_once()


def test_precheck_skips_when_policy_denies_describe(client, monkeypatch):
    """The schema is never read (nor echoed) when describe is not allowed."""
    from r4.companion import workflow_tools

    def guard(_client, operation):
        return '{"code": "denied"}' if operation == "describe" else None

    monkeypatch.setattr(workflow_tools, "_policy_guard", guard)
    _tools(client)["workflow_run"]["execute"](workflow_name="relax", parameters={"steps": "x"})
    client.workflows.describe.assert_not_called()
    client.workflows.trigger.assert_called_once()


def _session(client, tools):
    return SimpleNamespace(client=client, tools=tools, tool_search_opt_ins=set())


@pytest.fixture
def live(monkeypatch):
    """The platform answers (the real probe is a TCP connect)."""
    monkeypatch.setattr(tool_search_mod, "platform_live", lambda client: True)


@pytest.fixture
def catalog_session(client, live):
    client.workflows.list_workflows.return_value = [
        WorkflowCatalogItem(
            workflow_name="relax",
            name="relax",
            description="Structure relaxation with MACE",
            tags=["user"],
        ),
        WorkflowCatalogItem(
            workflow_name="ocr", name="ocr", description="Scan documents", tags=["user"]
        ),
    ]
    return _session(client, list(_tools(client).values()))


def _workflow_hits(session, query: str) -> list[dict]:
    return [h for h in register_session_tool_search(session)(query) if h.get("workflow_name")]


def test_tool_search_finds_registered_workflows(catalog_session):
    hits = _workflow_hits(catalog_session, "run my relaxation")
    assert [h["workflow_name"] for h in hits] == ["relax"]
    assert hits[0]["name"] == "workflow_run"
    assert {"workflow_run", "workflow_describe"} <= catalog_session.tool_search_opt_ins


def test_tool_search_ignores_scope_tags(catalog_session):
    """Scope tags ("user") are not indexed: they would match every workflow."""
    assert _workflow_hits(catalog_session, "user") == []


def test_tool_search_survives_an_unreachable_catalog(client, live):
    client.workflows.list_workflows.side_effect = RuntimeError("no platform")
    session = _session(client, list(_tools(client).values()))
    search = register_session_tool_search(session)
    assert all(not h.get("workflow_name") for h in search("relaxation"))
    search("relaxation")
    assert client.workflows.list_workflows.call_count == 1  # failure is cached too


def test_tool_search_never_calls_a_dead_platform(client):
    """No live gateway (the TCP probe fails): no catalog call can hang a search."""
    session = _session(client, list(_tools(client).values()))
    register_session_tool_search(session)("relaxation")
    client.workflows.list_workflows.assert_not_called()


def test_tool_search_without_terms_reads_no_catalog(client, live):
    session = _session(client, list(_tools(client).values()))
    register_session_tool_search(session)("the of")
    client.workflows.list_workflows.assert_not_called()


def test_search_workflows_needs_a_term_hit():
    rows = [{"name": "relax", "description": "d", "text": "relax d"}]
    assert search_workflows(rows, "the of", {}) == []


def test_cli_describe_lists_property_names(capsys):
    """Old code printed the schema's keys: 'type', 'properties', ..."""
    _print_description({"workflow_name": "relax", "parameter_schema": SCHEMA}, "x", 1)
    out = capsys.readouterr().out
    assert "name        : relax" in out
    assert "- steps" in out and "(required)" in out and "- mode" in out
    assert "- type" not in out and "- properties" not in out


def test_cli_schema_rows_keep_legacy_flat_maps():
    assert _schema_parameter_rows({"bucket": {"default": "b"}}) == [("bucket", "b", False)]
