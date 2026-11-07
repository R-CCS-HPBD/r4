"""D-39 pins: ``r4 catalog`` CLI verbs map onto the scientific-catalog routes."""

from __future__ import annotations

from argparse import Namespace

import pytest


def _args(**over) -> Namespace:
    base = {
        "catalog_command": None,
        "name": "mof-defects",
        "project": "proj-a",
        "version": "0.1.0",
        "description": "",
        "ontology": "",
        "graphs": [],
        "skills": [],
        "mcps": [],
        "embeds": [],
        "contributors": [],
        "licence": "",
        "change_note": "",
        "catalog_id": "scat-1",
        "as_json": False,
    }
    base.update(over)
    return Namespace(**base)


class _FakeClient:
    def __init__(self):
        self.calls = []

    def create_scientific_catalog(self, **kw):
        self.calls.append(("create", kw))
        return {"catalog_id": "scat-1", "name": kw["name"], "version": kw["version"]}

    def list_scientific_catalogs(self, *, project_id=None):
        self.calls.append(("list", project_id))
        return {"catalogs": []}

    def get_scientific_catalog(self, cid):
        self.calls.append(("show", cid))
        return {"catalog_id": cid, "status": "active"}

    def patch_scientific_catalog(self, cid, *, patch):
        self.calls.append(("patch", cid, patch))
        return {"catalog_id": cid}

    def retire_scientific_catalog(self, cid):
        self.calls.append(("retire", cid))
        return {"catalog_id": cid, "status": "retired"}


@pytest.fixture()
def fake_client(monkeypatch):
    from r4._cli import catalog as catalog_cli

    client = _FakeClient()
    monkeypatch.setattr("r4.client.Client", lambda: client)
    return catalog_cli, client


def test_create_passes_member_lists(fake_client, capsys):
    cli, client = fake_client
    rc = cli.cmd_catalog(_args(catalog_command="create", graphs=["kg-1"], skills=["skl-1"]))
    assert rc == 0
    verb, kw = client.calls[0]
    assert verb == "create"
    assert kw["member_graph_ids"] == ["kg-1"]
    assert kw["skill_ids"] == ["skl-1"]
    assert "scat-1" in capsys.readouterr().out


def test_list_empty_is_honest(fake_client, capsys):
    cli, client = fake_client
    rc = cli.cmd_catalog(_args(catalog_command="list"))
    assert rc == 0
    assert "No scientific catalogs." in capsys.readouterr().out


def test_show_and_retire(fake_client):
    cli, client = fake_client
    assert cli.cmd_catalog(_args(catalog_command="show")) == 0
    assert cli.cmd_catalog(_args(catalog_command="retire")) == 0
    assert [c[0] for c in client.calls] == ["show", "retire"]


_PATCH_NIL = {"description": None, "ontology": None, "licence": None, "contributors": None}


def test_patch_requires_a_field(fake_client, capsys):
    cli, _ = fake_client
    rc = cli.cmd_catalog(_args(catalog_command="patch", **_PATCH_NIL))
    assert rc == 1
    assert "Nothing to patch" in capsys.readouterr().out


def test_patch_sends_only_set_fields(fake_client):
    cli, client = fake_client
    rc = cli.cmd_catalog(_args(catalog_command="patch", **{**_PATCH_NIL, "licence": "MIT"}))
    assert rc == 0
    _, _, patch = client.calls[0]
    assert patch == {"licence": "MIT"}


def test_catalog_group_registered_in_dispatch() -> None:
    from r4.cli import _DISPATCH_MODULES, _PRE_REGISTER_COMMANDS, _PRE_REGISTER_MODULES

    assert "catalog" in _DISPATCH_MODULES
    assert "catalog" in _PRE_REGISTER_COMMANDS
    assert "catalog" in _PRE_REGISTER_MODULES


class _FakeClientV2(_FakeClient):
    def __init__(self):
        super().__init__()
        self.pinned = []

    def list_catalog_versions(self, *, project_id, name):
        self.calls.append(("versions", project_id, name))
        return {
            "versions": [
                {
                    "catalog_id": "scat-1",
                    "version": "0.1.0",
                    "status": "active",
                    "change_note": "first",
                    "provenance": {"pins": [{"ref": "1"}]},
                }
            ]
        }

    def add_catalog_pin(self, cid, *, kind, ref, note=""):
        self.calls.append(("pin", cid, kind, ref, note))
        return {"provenance": {"pins": [{"ref": ref}]}}

    def catalog_pins_by_ref(self, ref, *, project_id=None):
        self.calls.append(("pinned-by", ref, project_id))
        if ref == "pub-1":
            return {
                "ref": ref,
                "pins": [
                    {
                        "catalog_id": "scat-1",
                        "project_id": "proj-a",
                        "kind": "paper",
                        "note": "draft",
                    }
                ],
            }
        return {"ref": ref, "pins": []}


def test_versions_command(fake_client, monkeypatch, capsys):
    from r4._cli import catalog as cli_mod

    monkeypatch.setattr("r4.client.Client", lambda: _FakeClientV2())
    rc = cli_mod.cmd_catalog(_args(catalog_command="versions", name="mof-defects"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "v0.1.0" in out and "pins=1" in out and "first" in out


def test_pin_command(monkeypatch):
    from r4._cli import catalog as cli_mod

    client = _FakeClientV2()
    monkeypatch.setattr("r4.client.Client", lambda: client)
    rc = cli_mod.cmd_catalog(_args(catalog_command="pin", kind="job", ref="5434", note="run"))
    assert rc == 0
    assert client.calls[0] == ("pin", "scat-1", "job", "5434", "run")


def test_pinned_by_command_resolves_join(monkeypatch, capsys):
    """D-45 join verb: prints the catalogs that pinned the ref, honest when empty."""
    from r4._cli import catalog as cli_mod

    client = _FakeClientV2()
    monkeypatch.setattr("r4.client.Client", lambda: client)
    rc = cli_mod.cmd_catalog(_args(catalog_command="pinned-by", ref="pub-1", project_id=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert "scat-1" in out and "paper" in out
    assert client.calls[0] == ("pinned-by", "pub-1", None)

    rc = cli_mod.cmd_catalog(_args(catalog_command="pinned-by", ref="pub-x", project_id=None))
    assert rc == 0
    assert "No catalog pins" in capsys.readouterr().out


def test_redact_spelling_parsed(fake_client):
    cli_mod, client = fake_client
    from r4._cli.catalog import _redact_rules

    rules = _redact_rules(["contributors:drop", "provenance:hash"])
    assert rules == [
        {"target": "contributors", "action": "drop"},
        {"target": "provenance", "action": "hash"},
    ]


def test_redact_bad_spelling_named():
    from r4._cli.catalog import _redact_rules

    with pytest.raises(ValueError, match="bad --redact spelling"):
        _redact_rules(["contributors:vaporize"])


def test_create_passes_redaction_rules(monkeypatch):
    from r4._cli import catalog as cli_mod

    client = _FakeClient()
    monkeypatch.setattr("r4.client.Client", lambda: client)
    rc = cli_mod.cmd_catalog(_args(catalog_command="create", redact=["contributors:drop"]))
    assert rc == 0
    _, kw = client.calls[0]
    assert kw["redaction_rules"] == [{"target": "contributors", "action": "drop"}]
