"""ADR-113 WV3 (2026-11-08): companion tools and bridge verbs carry the workflow version."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import WorkflowCatalogItem, WorkflowResult, WorkflowStatus

from r4.companion.plan_commit_tools import build_plan_commit_tools
from r4.companion.ui.bridge_server import _version_param, _workflow_catalog_row
from r4.companion.workflow_tools import build_workflow_tools

DIGEST = "f" * 64


@pytest.fixture
def client():
    c = MagicMock()
    c.config = SimpleNamespace(project_id="p")
    c.workflows.list_workflows.return_value = [
        WorkflowCatalogItem(
            workflow_name="seg", name="seg", version="1.2.0", versions=["1.2.0", "1.0.0"]
        )
    ]
    c.workflows.describe.return_value = {"parameter_schema": {"type": "object"}}
    c.workflows.trigger.return_value = WorkflowResult(
        execution_id="e1",
        workflow_name="seg",
        status="submitted",
        workflow_version="1.0.0",
        source_sha256=DIGEST,
    )
    c.workflows.status.return_value = WorkflowStatus(
        execution_id="e1", workflow_name="seg", status="running", workflow_version="1.0.0"
    )
    c.workflows.get_execution_detail.return_value = {}
    c.workflows.plan.return_value = {"digest": "d", "signed": {}}
    c.workflows.save_run_definition.return_value = {"status": "ok"}
    return c


def _tools(client) -> dict:
    return {t["name"]: t for t in build_workflow_tools(client)}


@pytest.mark.parametrize("tool", ["workflow_describe", "workflow_run", "workflow_save_definition"])
def test_schemas_offer_a_version(client, tool):
    assert "version" in _tools(client)[tool]["parameters"]["properties"]


def test_plan_schema_offers_a_version(client):
    (plan,) = [t for t in build_plan_commit_tools(client) if t["name"] == "workflow_plan"]
    assert "version" in plan["parameters"]["properties"]


def test_list_rows_carry_version_and_versions(client):
    rows = json.loads(_tools(client)["workflow_list"]["execute"]())["workflows"]
    assert rows[0]["version"] == "1.2.0"
    assert rows[0]["versions"] == ["1.2.0", "1.0.0"]


def test_run_passes_the_version_and_reports_the_pin(client):
    out = json.loads(
        _tools(client)["workflow_run"]["execute"](workflow_name="seg", version="1.0.0")
    )
    assert client.workflows.trigger.call_args.kwargs["version"] == "1.0.0"
    client.workflows.describe.assert_called_with("seg", version="1.0.0")
    assert out["workflow_version"] == "1.0.0"
    assert out["source_sha256"] == DIGEST


def test_describe_passes_the_version(client):
    _tools(client)["workflow_describe"]["execute"](workflow_name="seg@1.0.0")
    client.workflows.describe.assert_called_once_with("seg@1.0.0", version=None)
    _tools(client)["workflow_describe"]["execute"](workflow_name="seg", version="1.0.0")
    assert client.workflows.describe.call_args.kwargs["version"] == "1.0.0"


def test_status_reports_the_pin(client):
    out = json.loads(_tools(client)["workflow_status"]["execute"](execution_id="e1"))
    assert out["workflow_version"] == "1.0.0"


def test_save_definition_passes_the_version(client):
    _tools(client)["workflow_save_definition"]["execute"](
        name="nightly", workflow_name="seg", parameters="{}", version="1.0.0"
    )
    assert client.workflows.save_run_definition.call_args.kwargs["version"] == "1.0.0"


def test_plan_passes_the_version(client):
    (plan,) = [t for t in build_plan_commit_tools(client) if t["name"] == "workflow_plan"]
    plan["execute"](workflow_name="seg", parameters={}, version="1.0.0")
    assert client.workflows.plan.call_args.kwargs["version"] == "1.0.0"


def test_bridge_catalog_row_and_version_param():
    row = _workflow_catalog_row(
        WorkflowCatalogItem(
            workflow_name="seg", name="seg", version="1.2.0", versions=["1.2.0"], source_sha256="x"
        )
    )
    assert (row["version"], row["versions"], row["source_sha256"]) == ("1.2.0", ["1.2.0"], "x")
    assert _version_param({"version": " 1.0.0 "}) == "1.0.0"
    assert _version_param({"version": ""}) is None
    assert _version_param({}) is None
