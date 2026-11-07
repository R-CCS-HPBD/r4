"""A workflow shared with the caller is labelled shared, not by the owner's scope.

2026-10-23 (rccs): researcher2 saw mnagaso's example-project-b workflows, shared through
``project:example-project-b``, as "[user]". UMRS marks such a row ``shared: true`` and
keeps the owner's fields (``scope: "user"``, ``user_id`` = owner); r4 turned
the scope into the only tag and dropped the rest. The label is now
"shared · <owner>" everywhere, the caller's own rows keep "user", system
rows keep "system", and the raw fields travel with the row.
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SHARED = {
    "name": "example-project-b-track-v0.2.9",
    "version": "0.2.9",
    "user_id": "mnagaso",
    "scope": "user",
    "access": ["circle:example-org/example-project-b", "project:example-project-b"],
    "description": "example workflow",
    "shared": True,
}
OWN = {
    "name": "my-own-wf",
    "user_id": "researcher2",
    "scope": "user",
    "access": [],
    "description": "mine",
}
SYSTEM = {"name": "sys-wf", "user_id": "admin", "scope": "system", "description": "platform"}


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


class _Parent:
    def __init__(self, config, transport=None, http=None):
        self.config = config
        self.transport = transport
        self.http = http

    def _ensure_transport_connected(self) -> bool:
        return self.transport is not None

    def _get_username(self) -> str:
        return "researcher2"


def _zmq_listing() -> list:
    from r4.config import Config, TransportMode
    from r4.workflows import WorkflowClient

    transport = MagicMock()
    transport.request_workflow_list.return_value = {
        "status": "ok",
        "count": 3,
        "workflows": [OWN, SHARED, SYSTEM],
    }
    config = Config(
        project_id="example-project-b",
        gateway_url="http://gateway:8000",
        umrs_username="researcher2",
        transport_mode=TransportMode.DIRECT,
    )
    return WorkflowClient(_Parent(config, transport=transport)).list_workflows()


def _http_listing() -> list:
    from r4.config import Config, TransportMode
    from r4.workflows import WorkflowClient

    http = MagicMock()
    http.get.return_value = {"status": "ok", "count": 3, "workflows": [OWN, SHARED, SYSTEM]}
    config = Config(
        project_id="example-project-b",
        gateway_url="http://gateway:8000",
        umrs_username="researcher2",
        transport_mode=TransportMode.GATEWAY,
    )
    return WorkflowClient(_Parent(config, http=http)).list_workflows()


@pytest.mark.parametrize("listing", [_zmq_listing, _http_listing], ids=["zmq", "http"])
def test_list_labels_shared_rows_by_owner_on_both_transports(listing) -> None:
    own, shared, system = listing()

    assert shared.tags == ["shared · mnagaso"]
    assert (shared.shared, shared.owner, shared.scope) == (True, "mnagaso", "user")
    assert shared.access == ["circle:example-org/example-project-b", "project:example-project-b"]
    assert own.tags == ["user"]
    assert (own.shared, own.owner) == (False, "researcher2")
    assert system.tags == ["system"]
    assert system.shared is False


def _shared_item() -> SimpleNamespace:
    """The catalog item list_workflows hands every surface for the shared row."""
    return SimpleNamespace(
        workflow_name="example-project-b-track-v0.2.9",
        workflow_id="example-project-b-track-v0.2.9",
        name="example-project-b-track-v0.2.9",
        description="example workflow",
        parameters=[],
        tags=["shared · mnagaso"],
        scope="user",
        owner="mnagaso",
        shared=True,
        access=["circle:example-org/example-project-b", "project:example-project-b"],
    )


def test_catalog_item_scope_label() -> None:
    from r4_contracts.workflows import WorkflowCatalogItem

    def label(**fields) -> str:
        return WorkflowCatalogItem(workflow_name="w", name="w", **fields).scope_label

    assert label(scope="user", owner="mnagaso", shared=True) == "shared · mnagaso"
    assert label(scope="user", shared=True) == "shared"
    assert label(scope="user", owner="researcher2") == "user"
    assert label(scope="system", owner="admin") == "system"
    assert label() == ""


def test_bridge_workflow_list_carries_the_ownership_fields() -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    workflows = SimpleNamespace(list_workflows=lambda: [_shared_item()])
    fake_self = SimpleNamespace(
        session=SimpleNamespace(client=SimpleNamespace(workflows=workflows))
    )

    row = CompanionBridgeServer.workflow_list(fake_self, {})["workflows"][0]

    assert row["tags"] == ["shared · mnagaso"]
    assert row["shared"] is True
    assert row["owner"] == "mnagaso"
    assert row["scope"] == "user"
    assert row["access"] == ["circle:example-org/example-project-b", "project:example-project-b"]


def test_cli_list_table_and_json_show_the_shared_owner(capsys) -> None:
    from r4._cli.workflow import _cmd_list

    wf = SimpleNamespace(list_workflows=lambda all_users=False: [_shared_item()])
    assert _cmd_list(wf, Namespace(as_json=False, all_users=False, name=None)) == 0
    table = capsys.readouterr().out
    line = next(row for row in table.splitlines() if row.startswith("example-project-b-track"))
    assert line.rstrip().endswith("shared · mnagaso")

    assert _cmd_list(wf, Namespace(as_json=True, all_users=False, name=None)) == 0
    (entry,) = json.loads(capsys.readouterr().out)
    assert entry["shared"] is True
    assert entry["owner"] == "mnagaso"
    assert entry["access"] == ["circle:example-org/example-project-b", "project:example-project-b"]


def test_cli_show_names_the_owner(capsys) -> None:
    from r4._cli.workflow import _print_description

    _print_description(SHARED, "example-project-b-track-v0.2.9", 1, shared=True)

    out = capsys.readouterr().out
    assert "scope       : shared · mnagaso" in out
    assert "owner       : mnagaso" in out


def test_cli_show_reads_sharing_from_the_catalog() -> None:
    from r4._cli.workflow import _shared_with_caller

    wf = SimpleNamespace(list_workflows=lambda: [_shared_item()])
    assert _shared_with_caller(wf, "example-project-b-track-v0.2.9", "mnagaso") is True
    assert _shared_with_caller(wf, "example-project-b-track-v0.2.9", "someone-else") is False
    assert _shared_with_caller(wf, "example-project-b-track-v0.2.9", "") is False

    def broken():
        raise RuntimeError("down")

    assert _shared_with_caller(SimpleNamespace(list_workflows=broken), "x", "mnagaso") is False


def test_companion_workflow_list_tool_reports_shared_owner() -> None:
    from r4.companion.workflow_tools import build_workflow_tools

    client = MagicMock()
    client.config = SimpleNamespace(project_id="example-project-b")
    client.workflows.list_workflows.return_value = [_shared_item()]
    tools = {tool["name"]: tool["execute"] for tool in build_workflow_tools(client)}

    (row,) = json.loads(tools["workflow_list"]())["workflows"]

    assert row["tags"] == ["shared · mnagaso"]
    assert row["shared"] is True
    assert row["owner"] == "mnagaso"


def test_acp_workflow_row_carries_the_ownership_fields() -> None:
    from r4.acp import _serialize_workflow_definition

    row = _serialize_workflow_definition(_shared_item())

    assert row["tags"] == ["shared · mnagaso"]
    assert row["shared"] is True
    assert row["owner"] == "mnagaso"
    assert row["access"] == ["circle:example-org/example-project-b", "project:example-project-b"]
