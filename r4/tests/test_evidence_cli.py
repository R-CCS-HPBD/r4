"""V-07 pins: ``r4 evidence`` CLI maps onto the evidence/scimem routes."""

from __future__ import annotations

from argparse import Namespace

import pytest


def _args(**over) -> Namespace:
    base = {
        "evidence_command": None,
        "memory_command": None,
        "project": None,
        "status": None,
        "source_type": None,
        "claim_id": None,
        "limit": 50,
        "evidence_id": "ev-1",
        "entry_id": "m-1",
        "query": "failure",
        "as_json": False,
    }
    base.update(over)
    return Namespace(**base)


class _FakeClient:
    def __init__(self):
        self.calls = []

    def list_evidence(self, **kw):
        self.calls.append(("list_evidence", kw))
        return {"evidence": [{"evidence_id": "ev-1", "status": "active", "content": "c"}]}

    def get_evidence_claim(self, claim_id):
        self.calls.append(("claim", claim_id))
        return {"evidence": []}

    def update_evidence_status(self, eid, status):
        self.calls.append(("ev_status", eid, status))
        return {"evidence_id": eid, "status": status}

    def list_memory_entries(self, **kw):
        self.calls.append(("mem_list", kw))
        return {"entries": []}

    def search_memory(self, query, **kw):
        self.calls.append(("mem_search", query, kw))
        return {"results": [{"id": "m-1", "status": "retracted", "summary": "failed"}]}

    def update_memory_status(self, eid, status):
        self.calls.append(("mem_status", eid, status))
        return {"updated": True}


@pytest.fixture()
def fake(monkeypatch):
    from r4._cli import evidence as cli_mod

    client = _FakeClient()
    monkeypatch.setattr("r4.client.Client", lambda: client)
    return cli_mod, client


def test_list_passes_filters(fake, capsys):
    cli, client = fake
    rc = cli.cmd_evidence(_args(evidence_command="list", status="retracted"))
    assert rc == 0
    _, kw = client.calls[0]
    assert kw["status"] == "retracted"
    assert "ev-1" in capsys.readouterr().out


def test_claim_uses_claims_route(fake, capsys):
    cli, client = fake
    rc = cli.cmd_evidence(_args(evidence_command="claim", claim_id="c-9"))
    assert rc == 0
    assert client.calls[0] == ("claim", "c-9")
    assert "No evidence rows." in capsys.readouterr().out


def test_status_transition(fake):
    cli, client = fake
    rc = cli.cmd_evidence(_args(evidence_command="status", status="retracted"))
    assert rc == 0
    assert client.calls[0] == ("ev_status", "ev-1", "retracted")


def test_memory_search(fake, capsys):
    cli, client = fake
    rc = cli.cmd_evidence(_args(evidence_command="memory", memory_command="search"))
    assert rc == 0
    assert client.calls[0][0] == "mem_search"
    assert "failed" in capsys.readouterr().out


def test_memory_status(fake):
    cli, client = fake
    rc = cli.cmd_evidence(
        _args(evidence_command="memory", memory_command="status", status="retracted")
    )
    assert rc == 0
    assert client.calls[0] == ("mem_status", "m-1", "retracted")


def test_evidence_group_registered() -> None:
    from r4.cli import _DISPATCH_MODULES, _PRE_REGISTER_COMMANDS, _PRE_REGISTER_MODULES

    assert "evidence" in _DISPATCH_MODULES
    assert "evidence" in _PRE_REGISTER_COMMANDS
    assert "evidence" in _PRE_REGISTER_MODULES
