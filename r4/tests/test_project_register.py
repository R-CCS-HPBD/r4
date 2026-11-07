"""`r4 project register` / the `project_register` bridge verb (2026-11-03).

Maintainer: "i was not sure [how] to register new project on server".
`r4 project create` wrote the local registry only and nothing in r4 called
POST /gw/projects (ADR-051 D-1: any verified user creates one), so a new
project was unknown to the server until someone ran curl.
"""

from __future__ import annotations

from argparse import Namespace
from typing import Any

import pytest

from r4.errors import APIError, AuthenticationError, R4ConnectionError
from r4.project import ProjectConfig, ProjectManager
from r4.project_register import (
    LOCAL_ONLY_NOTE,
    RegisterError,
    error_text,
    register_project,
    registered_text,
)


class _Config:
    def __init__(self, gateway_url: str = "https://gw.example") -> None:
        self.gateway_url = gateway_url


class _Client:
    def __init__(
        self, gateway_url: str = "https://gw.example", fail: Exception | None = None
    ) -> None:
        self.config = _Config(gateway_url)
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def create_platform_project(self, project_id: str, *, name: str = "") -> dict[str, Any]:
        self.calls.append((project_id, name))
        if self.fail:
            raise self.fail
        return {
            "status": "ok",
            "project": {"project_id": project_id, "name": name, "owner_id": "alice"},
        }


@pytest.fixture
def pm(tmp_path) -> ProjectManager:
    return ProjectManager(config_dir=tmp_path)


def test_register_creates_the_server_row_and_the_local_entry(pm: ProjectManager) -> None:
    client = _Client()
    result = register_project(client, " mof5 ", name="MOF-5", pm=pm)
    assert client.calls == [("mof5", "MOF-5")]
    assert result["local"] == "added"
    assert result["owner"] == "alice"
    record = pm.get("mof5")
    assert record is not None and record.gateway_url == "https://gw.example"
    assert "registered on https://gw.example (owner: alice)" in registered_text(result)


def test_a_local_only_project_gets_its_gateway_recorded(pm: ProjectManager) -> None:
    pm.create(ProjectConfig(project_id="mof5", name="mine"))
    result = register_project(_Client(), "mof5", pm=pm)
    assert result["local"] == "linked"
    record = pm.get("mof5")
    assert (
        record is not None and record.gateway_url == "https://gw.example" and record.name == "mine"
    )


def test_an_entry_with_its_own_gateway_is_kept(pm: ProjectManager) -> None:
    pm.create(ProjectConfig(project_id="mof5", gateway_url="https://other.example"))
    assert register_project(_Client(), "mof5", pm=pm)["local"] == "kept"
    assert pm.get("mof5").gateway_url == "https://other.example"  # type: ignore[union-attr]


def test_never_moves_the_current_project(pm: ProjectManager) -> None:
    pm.create(ProjectConfig(project_id="home"))
    pm.set_current("home")
    register_project(_Client(), "mof5", pm=pm)
    assert pm.get_current() == "home"


def test_no_platform_configured_says_so_without_a_call(pm: ProjectManager) -> None:
    client = _Client(gateway_url="")
    with pytest.raises(RegisterError, match="no platform is configured"):
        register_project(client, "mof5", pm=pm)
    assert client.calls == []


def test_an_empty_id_is_refused(pm: ProjectManager) -> None:
    with pytest.raises(RegisterError, match="project id is required"):
        register_project(_Client(), "  ", pm=pm)


@pytest.mark.parametrize(
    ("exc", "words"),
    [
        (APIError("project id is taken", status_code=409), "already taken on https://gw.example"),
        (
            APIError("projects.conflict", status_code=400, error_code="projects.conflict"),
            "already taken",
        ),
        (AuthenticationError(status_code=401), "not signed in to https://gw.example"),
        (R4ConnectionError("refused"), "cannot reach the platform at https://gw.example"),
        (APIError("boom", status_code=500), "refused to register 'mof5': boom"),
    ],
)
def test_failures_have_plain_words(pm: ProjectManager, exc: Exception, words: str) -> None:
    with pytest.raises(RegisterError, match=words.replace("(", r"\(").replace(".", r"\.")):
        register_project(_Client(fail=exc), "mof5", pm=pm)
    assert pm.get("mof5") is None  # nothing recorded locally on failure
    assert words in error_text(exc, "mof5", "https://gw.example")


def test_client_sends_an_empty_project_header() -> None:
    """ADR-097: the gateway checks X-Project-ID against live memberships; the
    project being created is not one yet, so the header must not name it."""
    from r4.client import Client

    sent: dict[str, Any] = {}

    class _Probe(Client):
        def __init__(self) -> None:  # no config, no network
            self._is_umrs_direct = False

        def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
            sent.update(path=path, json=json, **kwargs)
            return {"project": {}}

    _Probe().create_platform_project("mof5", name="MOF-5")
    assert sent["path"] == "/gw/projects"
    assert sent["json"] == {"project_id": "mof5", "name": "MOF-5"}
    assert sent["headers"] == {"X-Project-ID": ""}


def test_cli_register_prints_success_and_failure(pm: ProjectManager, monkeypatch, capsys) -> None:
    import r4.client as client_module
    from r4._cli import project as cli

    monkeypatch.setattr(client_module, "Client", lambda: _Client())
    cli._project_register(Namespace(project_id="mof5", name=""), pm, "default", None, None)
    out = capsys.readouterr().out
    assert "✓ project mof5 is registered on https://gw.example" in out
    assert "r4 project switch mof5" in out

    monkeypatch.setattr(
        client_module, "Client", lambda: _Client(fail=AuthenticationError(status_code=401))
    )
    with pytest.raises(SystemExit):
        cli._project_register(Namespace(project_id="other", name=""), pm, "default", None, None)
    assert "not signed in" in capsys.readouterr().err


def test_create_on_a_connected_profile_names_register(
    pm: ProjectManager, monkeypatch, capsys
) -> None:
    import r4.config as config_module
    from r4._cli import project as cli

    monkeypatch.setattr(config_module, "get_config", lambda: _Config("https://gw.example"))
    cli._print_local_only_note(ProjectConfig(project_id="mof5"))
    assert capsys.readouterr().out.strip() == LOCAL_ONLY_NOTE.format(project_id="mof5")
    cli._print_local_only_note(ProjectConfig(project_id="mof5", gateway_url="https://gw.example"))
    monkeypatch.setattr(config_module, "get_config", lambda: _Config(""))
    cli._print_local_only_note(ProjectConfig(project_id="mof5"))
    assert capsys.readouterr().out == ""
