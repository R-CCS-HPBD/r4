"""Tests for ``r4 search`` command failure semantics."""

from __future__ import annotations

import json
from argparse import Namespace

import pytest


def _search_args(**overrides) -> Namespace:
    values = {
        "query": "find endpoint provisioning code",
        "project": None,
        "depth": "shallow",
        "scope": "code_only",
        "max_steps": 5,
        "max_sources": 10,
        "no_citations": False,
        "no_web": False,
        "no_rag": False,
        "no_code": False,
        "stream": False,
        "as_json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_search_returns_nonzero_for_empty_zero_confidence_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import r4.client as client_module
    import r4.search.pipeline as pipeline_module
    from r4._cli.search import cmd_search
    from r4.search.core import SearchResult

    class FakeClient:
        class _config:
            gateway_url = "http://gateway.test"

        def readiness(self):
            return {"ready": True}

    class FakePipeline:
        def __init__(self, *_args, **_kwargs):
            pass

        def on_event(self, _callback):
            pass

        async def search(self, query):
            return SearchResult(
                query=query,
                answer="I couldn't find relevant information to answer this question.",
                sources=[],
                confidence=0.0,
                warnings=["No search sources found."],
            )

    monkeypatch.setattr(client_module, "Client", FakeClient)
    monkeypatch.setattr(pipeline_module, "AgenticSearchPipeline", FakePipeline)

    assert cmd_search(_search_args()) == 1


def test_search_json_output_stays_parseable_for_empty_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import r4.client as client_module
    import r4.search.pipeline as pipeline_module
    from r4._cli.search import cmd_search
    from r4.search.core import SearchResult

    class FakeClient:
        class _config:
            gateway_url = "http://gateway.test"

        def readiness(self):
            return {"ready": True}

    class FakePipeline:
        def __init__(self, *_args, **_kwargs):
            pass

        def on_event(self, _callback):
            raise AssertionError("JSON mode must not register progress callback")

        async def search(self, query):
            return SearchResult(query=query, answer="empty", sources=[], confidence=0.0)

    monkeypatch.setattr(client_module, "Client", FakeClient)
    monkeypatch.setattr(pipeline_module, "AgenticSearchPipeline", FakePipeline)

    assert cmd_search(_search_args(as_json=True)) == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["sources"] == []
    assert parsed["confidence"] == 0.0


def test_search_config_kwargs_accepts_lab_only_scope() -> None:
    from r4._cli.search import _search_config_kwargs
    from r4.search.core import SearchDepth, SearchScope

    kwargs = _search_config_kwargs(
        _search_args(scope="lab_only", project="proj-1"),
        SearchDepth,
        SearchScope,
    )

    assert kwargs["scope"] == SearchScope.LAB_ONLY
    assert kwargs["project_id"] == "proj-1"
