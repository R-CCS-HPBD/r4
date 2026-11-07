"""The built-in ``web_search`` backend (``r4.agent.web_search``, 2026-10-30).

Before it, no surface wired ``set_web_search_callback``: the availability
gate dropped ``web_search`` in every mode and its handler only returned a
stub. The backend is a SearXNG instance named by ``R4_WEB_SEARCH_URL``.
Hermetic: the page fetch (``_get``) is stubbed, nothing leaves the host.
"""

from __future__ import annotations

import json

import pytest

from r4.agent import tools as agent_tools
from r4.agent import web_search
from r4.companion.tool_availability import unavailable_reason

_HITS = {"results": [{"title": "T", "content": "S", "url": "https://a.example/"}] * 3}


@pytest.fixture(autouse=True)
def _no_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(web_search.SEARXNG_URL_ENV, raising=False)
    monkeypatch.setattr(agent_tools, "_web_search_fn", None)


def test_searxng_json_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(web_search.SEARXNG_URL_ENV, "https://searx.example.org/")
    urls: list[str] = []
    monkeypatch.setattr(web_search, "_get", lambda url: urls.append(url) or json.dumps(_HITS))
    hits = web_search.search("q x", 2)
    assert urls == ["https://searx.example.org/search?q=q+x&format=json"]
    assert hits == [{"title": "T", "snippet": "S", "url": "https://a.example/"}] * 2


def test_the_tool_handler_runs_the_builtin_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(web_search.SEARXNG_URL_ENV, "https://searx.example.org")
    monkeypatch.setattr(web_search, "_get", lambda url: json.dumps(_HITS))
    result = agent_tools.get_builtin_registry().handle("web_search", {"query": "q"})
    assert result.error is None
    assert str(result.output).startswith("1. T\n   S\n   https://a.example/")


def test_without_a_backend_the_handler_says_how_to_configure_one() -> None:
    result = agent_tools.get_builtin_registry().handle("web_search", {"query": "q"})
    assert result.error is not None and "R4_WEB_SEARCH_URL" in result.error


def test_an_injected_callback_replaces_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search, "_get", lambda url: pytest.fail("built-in backend ran"))
    monkeypatch.setattr(agent_tools, "_web_search_fn", lambda q, n: [{"title": "T", "url": "u"}])
    result = agent_tools.get_builtin_registry().handle("web_search", {"query": "q"})
    assert str(result.output).startswith("1. T")


def test_the_gate_offers_web_search_once_a_backend_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = {"name": "web_search"}
    reason = unavailable_reason(tool, None)
    assert reason is not None and "R4_WEB_SEARCH_URL" in reason
    monkeypatch.setenv(web_search.SEARXNG_URL_ENV, "https://searx.example.org")
    assert unavailable_reason(tool, None) is None
