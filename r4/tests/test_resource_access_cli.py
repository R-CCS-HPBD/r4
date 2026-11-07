"""Uniform resource access lists (ADR-080) — client payloads + CLI surface.

Mirrors ``test_endpoint_access_cli.py`` (ADR-078): per family the tests
assert the client builds the right URL/payload (http layer mocked exactly
like the endpoint tests) and that the CLI verbs parse and show/set/clear.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

from r4._cli.bucket import BucketCommand
from r4._cli.catalog import CatalogCommand
from r4._cli.catalog import _access as catalog_access
from r4._cli.knowledge import KnowledgeCommand
from r4._cli.rag import RagCommand, _handle_corpus_access
from r4._cli.workflow import WorkflowCommand
from r4._cli.workflow import _cmd_access as workflow_access
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


def _ns(**kw):
    base = {"access_set": None, "private": False}
    base.update(kw)
    return SimpleNamespace(**base)


# ── client URL/payload shape (one test per family) ───────────────────────────


def test_graph_access_client_urls(monkeypatch):
    client = _client()
    sent = {}

    def put(url, json_body=None, **kwargs):
        sent.update(url=url, body=json_body)
        return {"graph_id": "g1", "access": json_body["access"]}

    def get(url, params=None, **kwargs):
        sent.update(get_url=url, get_params=params)
        return {"graph_id": "g1", "access": []}

    monkeypatch.setattr(client.http, "put", put)
    monkeypatch.setattr(client.http, "get", get)
    client.set_graph_access("g1", ["group:lab"])
    assert sent["url"].endswith("/knowledge/graphs/g1/access")
    assert sent["body"] == {"access": ["group:lab"]}
    client.get_graph_access("g1")
    assert sent["get_url"].endswith("/knowledge/graphs/g1/access")


def test_catalog_access_client_urls(monkeypatch):
    client = _client()
    sent = {}

    def put(url, json_body=None, **kwargs):
        sent.update(url=url, body=json_body)
        return {"catalog_id": "c1", "access": json_body["access"]}

    def get(url, params=None, **kwargs):
        sent.update(get_url=url)
        return {"catalog_id": "c1", "access": []}

    monkeypatch.setattr(client.http, "put", put)
    monkeypatch.setattr(client.http, "get", get)
    client.set_catalog_access("c1", ["system"])
    assert sent["url"].endswith("/scientific-catalogs/c1/access")
    assert sent["body"] == {"access": ["system"]}
    client.get_catalog_access("c1")
    assert sent["get_url"].endswith("/scientific-catalogs/c1/access")


def test_workflow_access_client_urls(monkeypatch):
    client = _client()
    sent = {}

    def put(url, json_body=None, **kwargs):
        sent.update(url=url, body=json_body)
        return {"workflow_name": "wf1", "access": json_body["access"]}

    def get(url, params=None, **kwargs):
        sent.update(get_url=url)
        return {"workflow_name": "wf1", "owner": "u1", "access": []}

    monkeypatch.setattr(client.http, "put", put)
    monkeypatch.setattr(client.http, "get", get)
    client.set_workflow_access("wf1", ["project:ops", "user:rin"])
    assert sent["url"].endswith("/workflows/wf1/access")
    assert sent["body"] == {"access": ["project:ops", "user:rin"]}
    client.get_workflow_access("wf1")
    assert sent["get_url"].endswith("/workflows/wf1/access")


def test_bucket_access_client_urls(monkeypatch):
    client = _client()
    sent = {}

    def put(url, json_body=None, **kwargs):
        sent.update(url=url, body=json_body)
        return {"bucket": "u.p.videos", "access": json_body["access"]}

    def get(url, params=None, **kwargs):
        sent.update(get_url=url)
        return {"bucket": "u.p.videos", "owner": "u", "access": []}

    monkeypatch.setattr(client.http, "put", put)
    monkeypatch.setattr(client.http, "get", get)
    client.set_bucket_access("u.p.videos", ["group:lab"])
    assert sent["url"].endswith("/buckets/u.p.videos/access")
    assert sent["body"] == {"access": ["group:lab"]}
    client.get_bucket_access("u.p.videos")
    assert sent["get_url"].endswith("/buckets/u.p.videos/access")


def test_rag_corpus_access_client_urls(monkeypatch):
    client = _client()
    sent = {}

    def put(url, json_body=None, **kwargs):
        sent.update(url=url, body=json_body)
        return {"project_id": json_body["project_id"], "access": json_body["access"]}

    def get(url, params=None, **kwargs):
        sent.update(get_url=url, get_params=params)
        return {"project_id": "p1", "access": []}

    monkeypatch.setattr(client.http, "put", put)
    monkeypatch.setattr(client.http, "get", get)
    client.set_rag_corpus_access("p1", ["project:ops"])
    assert sent["url"].endswith("/rag/corpus-access")
    assert sent["body"] == {"project_id": "p1", "access": ["project:ops"]}
    client.get_rag_corpus_access("p1")
    assert sent["get_url"].endswith("/rag/corpus-access")
    assert sent["get_params"] == {"project_id": "p1"}


# ── CLI surface (show / set / --private per family) ──────────────────────────


def test_catalog_access_cli_show_and_private(capsys):
    calls = []

    class Fake:
        def get_catalog_access(self, cid):
            return {"catalog_id": cid, "access": ["project:ops"]}

        def set_catalog_access(self, cid, access):
            calls.append(access)
            return {"catalog_id": cid, "project_id": "p1", "access": access}

    assert catalog_access(Fake(), _ns(catalog_id="c1")) == 0
    assert "access: project:ops" in capsys.readouterr().out
    assert catalog_access(Fake(), _ns(catalog_id="c1", private=True)) == 0
    assert calls == [[]]
    assert "owning project only" in capsys.readouterr().out


def test_knowledge_graph_access_cli_show_and_set(monkeypatch, capsys):
    calls = []

    class Fake:
        def get_graph_access(self, gid):
            return {"graph_id": gid, "access": ["group:lab"]}

        def set_graph_access(self, gid, access):
            calls.append(access)
            return {"graph_id": gid, "project_id": "p1", "access": access}

    monkeypatch.setattr("r4.client.Client", lambda *a, **k: Fake())
    cmd = KnowledgeCommand()
    assert cmd._handle_graph_access(_ns(graph_id="g1")) == 0
    assert "access: group:lab" in capsys.readouterr().out
    assert cmd._handle_graph_access(_ns(graph_id="g1", access_set=["system"])) == 0
    assert calls == [["system"]]
    assert "access: system" in capsys.readouterr().out


def test_workflow_access_cli_show_and_private(capsys):
    calls = []

    class Fake:
        def get_workflow_access(self, name):
            return {"workflow_name": name, "owner": "u1", "access": []}

        def set_workflow_access(self, name, access):
            calls.append(access)
            return {"workflow_name": name, "owner": "u1", "access": access}

    wf = SimpleNamespace(_parent=Fake())
    assert workflow_access(wf, _ns(workflow_name="wf1")) == 0
    assert "owner only" in capsys.readouterr().out
    assert workflow_access(wf, _ns(workflow_name="wf1", private=True)) == 0
    assert calls == [[]]
    assert "owner only" in capsys.readouterr().out


def test_bucket_access_cli_show_and_set(monkeypatch, capsys):
    calls = []

    class Fake:
        def get_bucket_access(self, bucket):
            return {"bucket": bucket, "owner": "u", "access": ["user:rin"]}

        def set_bucket_access(self, bucket, access):
            calls.append(access)
            return {"bucket": bucket, "owner": "u", "access": access}

    monkeypatch.setattr("r4.client.Client", lambda *a, **k: Fake())
    cmd = BucketCommand()
    assert cmd._access(_ns(bucket="u.p.videos")) == 0
    assert "access: user:rin" in capsys.readouterr().out
    assert cmd._access(_ns(bucket="u.p.videos", access_set=["group:lab"])) == 0
    assert calls == [["group:lab"]]


def test_rag_corpus_access_cli_show_and_private(monkeypatch, capsys):
    calls = []

    class Fake:
        def get_rag_corpus_access(self, pid):
            return {"project_id": pid, "access": []}

        def set_rag_corpus_access(self, pid, access):
            calls.append(access)
            return {"project_id": pid, "access": access}

    monkeypatch.setattr("r4.client.Client", lambda *a, **k: Fake())
    assert _handle_corpus_access(_ns(project="p1")) == 0
    assert "owning project only" in capsys.readouterr().out
    assert _handle_corpus_access(_ns(project="p1", private=True)) == 0
    assert calls == [[]]


# ── argparse wiring: every family's ``access`` verb parses --set/--private ───


def test_access_verbs_parse_set_and_private():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    for cls in (KnowledgeCommand, CatalogCommand, WorkflowCommand, BucketCommand, RagCommand):
        cls().register(sub)
    cases = [
        (["knowledge", "graph-access", "g1"], "graph_id"),
        (["catalog", "access", "c1"], "catalog_id"),
        (["workflow", "access", "wf1"], "workflow_name"),
        (["bucket", "access", "u.p.videos"], "bucket"),
        (["rag", "corpus-access", "p1"], "project"),
    ]
    for argv, id_arg in cases:
        ns = parser.parse_args(argv + ["--set", "group:lab", "--set", "user:x"])
        assert getattr(ns, id_arg) == argv[2]
        assert ns.access_set == ["group:lab", "user:x"]
        assert ns.private is False
        ns = parser.parse_args(argv + ["--private"])
        assert ns.private is True
        assert ns.access_set is None
