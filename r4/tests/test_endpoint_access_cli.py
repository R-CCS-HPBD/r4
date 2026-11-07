"""r4 endpoint access / --access (ADR-078) — client payloads."""

from __future__ import annotations

from types import SimpleNamespace

from r4._cli.endpoint import _cmd_endpoint_access
from r4.client import Client


def _client():
    cfg = SimpleNamespace(
        model="m",
        gateway_url="https://gw.example",
        api_key="jwt",
        endpoint_url=None,
        endpoint_provider=None,
        model_routes={},
    )
    return Client(config=cfg)


def test_set_endpoint_access_puts_the_full_list(monkeypatch):
    client = _client()
    sent = {}

    def put(url, json_body=None, **kwargs):
        sent.update(url=url, body=json_body)
        return {"job_id": "e1", "access": json_body["access"]}

    monkeypatch.setattr(client.http, "put", put)
    client.set_endpoint_access("e1", ["system"])
    assert sent["url"].endswith("/endpoints/e1/access")
    assert sent["body"] == {"access": ["system"]}


def test_provision_payload_carries_access(monkeypatch):
    client = _client()
    sent = {}

    def post(url, json_body=None, **kwargs):
        sent.update(body=json_body)
        return {"status": "ready", "job_id": "e1", "endpoint": {}}

    monkeypatch.setattr(client.http, "post", post)
    client.provision_endpoint(model="Bespoke-Nimble-9B", access=["group:lab"], wait=False)
    assert sent["body"]["access"] == ["group:lab"]
    client.provision_endpoint(model="Bespoke-Nimble-9B", wait=False)
    assert "access" not in sent["body"]  # omitted = roster default / owner only


def test_cli_shows_and_clears_access(capsys):
    calls = []

    class Fake:
        def get_endpoint_status(self, job_id):
            return {"job_id": job_id, "project_id": "default", "access": ["system"]}

        def set_endpoint_access(self, job_id, access):
            calls.append(access)
            return {"job_id": job_id, "project_id": "default", "access": access}

    args = SimpleNamespace(instance_id="e1", access_set=None, private=False)
    _cmd_endpoint_access(args, Fake())
    assert "access: system" in capsys.readouterr().out
    _cmd_endpoint_access(SimpleNamespace(instance_id="e1", access_set=None, private=True), Fake())
    assert calls == [[]]
    assert "owning project only" in capsys.readouterr().out
