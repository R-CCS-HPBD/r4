"""Companion project tools: project_ensure (create + switch + client sync)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import r4.project as project_module
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.project_tools import build_project_tools
from r4.project import ProjectManager


@pytest.fixture()
def isolated_project_manager(tmp_path, monkeypatch):
    """Fresh ProjectManager singleton isolated to a temp config dir."""
    manager = ProjectManager(config_dir=tmp_path / "r4cfg")
    monkeypatch.setattr(project_module, "_project_manager", manager)
    return manager


def _client() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(project_id=None))


def _tools(client=None) -> dict:
    return {t["name"]: t for t in build_project_tools(client or _client())}


def test_registry_exposes_only_project_ensure():
    # project_list / project_use already live in tools.py; no duplicates here.
    assert set(_tools()) == {"project_ensure"}


def test_ensure_creates_project_and_makes_it_current(isolated_project_manager):
    client = _client()
    payload = json.loads(
        _tools(client)["project_ensure"]["execute"](
            project_id="mermaids-seismic", name="MERMAIDS seismic filter"
        )
    )
    assert payload["type"] == "project_ensure"
    assert payload["created"] is True
    assert payload["current"] == "mermaids-seismic"
    assert isolated_project_manager.get_current() == "mermaids-seismic"
    assert isolated_project_manager.get("mermaids-seismic").name == "MERMAIDS seismic filter"


def test_ensure_syncs_client_config_for_followup_knowledge_calls(isolated_project_manager):
    client = _client()
    _tools(client)["project_ensure"]["execute"](project_id="p2")
    assert client.config.project_id == "p2"


def test_ensure_is_idempotent_for_existing_projects(isolated_project_manager):
    tools = _tools()
    first = json.loads(tools["project_ensure"]["execute"](project_id="p3", name="Old"))
    second = json.loads(tools["project_ensure"]["execute"](project_id="p3"))
    assert first["created"] is True
    assert second["created"] is False
    assert isolated_project_manager.get("p3").name == "Old"


def test_ensure_rejects_blank_project_id(isolated_project_manager):
    payload = json.loads(_tools()["project_ensure"]["execute"](project_id="  "))
    assert payload["code"] == "invalid_project_id"
    assert isolated_project_manager.get_current() is None


def test_project_use_syncs_client_config(isolated_project_manager):
    from r4.companion.tools import _project_and_teamup_tools

    client = _client()
    isolated_project_manager.create(project_module.ProjectConfig(project_id="p4", name="P4"))
    tools = {t["name"]: t for t in _project_and_teamup_tools(client)}
    tools["project_use"]["execute"]("p4")
    assert client.config.project_id == "p4"


def test_knowledge_tools_fall_back_to_current_project(isolated_project_manager):
    """Client config lacking project_id must resolve the registry's current."""
    from unittest.mock import MagicMock

    isolated_project_manager.create(project_module.ProjectConfig(project_id="p5"))
    isolated_project_manager.set_current("p5")

    from r4.companion.knowledge_tools import build_knowledge_tools

    client = SimpleNamespace(
        config=SimpleNamespace(project_id=None),
        knowledge=MagicMock(),
    )
    client.knowledge.get_graph.return_value = {"nodes": [], "edges": []}
    client.knowledge.list_loops.return_value = []
    tools = {t["name"]: t for t in build_knowledge_tools(client)}
    payload = json.loads(tools["knowledge_status"]["execute"]())
    assert payload["type"] == "knowledge_status"
    assert payload["project_id"] == "p5"


def test_permissions_classification():
    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    assert pm.is_safe("project_list") is True
    assert pm.is_safe("project_ensure") is False
    assert pm.is_safe("knowledge_status") is True
