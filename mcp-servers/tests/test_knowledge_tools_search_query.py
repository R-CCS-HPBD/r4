"""knowledge-tools `knowledge_search` rides the unified query facade
(cleanup C-3a, 2026-10-19): the retired ``GET /umrs/knowledge/search``
engine was never injected, so the tool always answered 503. The re-pointed
tool calls ``GET /gw/knowledge/query`` with the facade's parameter names —
the same call the r4 client's ``knowledge.query`` makes — and passes the
facade payload through verbatim. Fails on the old code (it would call
``/gw/knowledge/search`` with ``query=`` instead of ``q=``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PKG = (
    Path(__file__).resolve().parent.parent
    / "hatch-registry"
    / "packages"
    / "knowledge-tools"
    / "server.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("pkg_knowledge_tools_server", _PKG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture(mod):
    calls = []

    def fake_request(method, path, params=None, body=None):
        calls.append({"method": method, "path": path, "params": params, "body": body})
        return {
            "query": {"results": [{"handle": "abc", "text": "hit"}]},
            "wired_backends": ["graph"],
        }

    return calls, fake_request


def test_knowledge_search_calls_the_query_facade():
    mod = _load()
    calls, fake = _capture(mod)
    mod._gateway_request = fake

    server = mod.KnowledgeToolsServer()
    out = server.invoke(
        "knowledge_search", {"query": "cat states", "project_id": "proj-a", "top_k": 3}
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/gw/knowledge/query"
    assert call["params"] == {"q": "cat states", "project_id": "proj-a", "scope": "all", "limit": 3}
    assert call["body"] is None
    # Facade payload passes through (coverage truth rides wired_backends).
    assert out["wired_backends"] == ["graph"]
    assert out["query"]["results"][0]["handle"] == "abc"


def test_knowledge_search_defaults_top_k_and_project(monkeypatch, tmp_path):
    mod = _load()
    calls, fake = _capture(mod)
    mod._gateway_request = fake
    monkeypatch.setenv("R4_PROJECT_ID", "proj-env")

    server = mod.KnowledgeToolsServer()
    server.invoke("knowledge_search", {"query": "x"})

    assert calls[0]["params"]["limit"] == 5
    assert calls[0]["params"]["project_id"] == "proj-env"
