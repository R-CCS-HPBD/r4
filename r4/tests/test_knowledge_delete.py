"""ADR-093 MD-16: ``Client.knowledge.delete_artifact``, ``RAGClient.delete_document``
and ``r4 knowledge delete`` / ``r4 knowledge docs delete`` (decided 2026-10-11).

The client is a thin DTO wrapper over ``DELETE /gw/knowledge/artifacts/{id}``
(one request, no retry); the CLI confirms unless ``--yes`` and prints the
counts the server returns, never a bare "Deleted".
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from r4._cli.knowledge import KnowledgeCommand
from r4.errors import APIError
from r4.knowledge import ArtifactDeleteResult, KnowledgeClient

SERVER_BODY: dict[str, Any] = {
    "artifact_id": "art-a",
    "project_id": "p1",
    "deleted": True,
    "counts": {
        "artifacts": 1,
        "nodes": 6,
        "edges": 5,
        "embeddings": 2,
        "ir": 1,
        "findings": 1,
        "events_detached": 2,
    },
    "objects": [
        {"ref": "k1", "bucket": "b", "key": "k1", "outcome": "deleted"},
        {"ref": "k2", "bucket": "b", "key": "k2", "outcome": "failed", "detail": "S3 refused"},
    ],
    "notes": [],
}


class _StubHttp:
    _api_prefix = "/gw"

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.response = SERVER_BODY if response is None else response
        self.error = error

    def delete(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def test_delete_artifact_calls_the_route_once_and_wraps_the_result():
    http = _StubHttp()

    result = KnowledgeClient(http).delete_artifact("art-a", project_id="p1")

    assert http.calls == [
        ("/gw/knowledge/artifacts/art-a", {"params": {"project_id": "p1"}, "headers": None})
    ]
    assert isinstance(result, ArtifactDeleteResult)
    assert result.deleted is True
    assert result.counts["nodes"] == 6 and result.counts["events_detached"] == 2
    assert [o["key"] for o in result.failed_objects] == ["k2"]


def test_delete_artifact_defaults_to_the_verified_project_and_quotes_the_id():
    http = _StubHttp()

    KnowledgeClient(http).delete_artifact("a/b c")

    url, kwargs = http.calls[0]
    assert url == "/gw/knowledge/artifacts/a%2Fb%20c"
    assert kwargs["params"] is None


def test_an_agent_context_client_says_so_and_the_server_decides():
    http = _StubHttp()
    knowledge = KnowledgeClient(http)
    knowledge.actor, knowledge.session_id = "agent", "s-1"

    knowledge.delete_artifact("art-a")

    assert http.calls[0][1]["headers"] == {"x-r4-actor": "agent", "x-r4-session-id": "s-1"}


def test_a_404_surfaces_once_without_retry():
    http = _StubHttp(error=APIError("not found", status_code=404))

    with pytest.raises(APIError) as exc:
        KnowledgeClient(http).delete_artifact("art-gone")

    assert exc.value.status_code == 404
    assert len(http.calls) == 1


def test_the_rag_client_surfaces_the_delete_result():
    from types import SimpleNamespace

    from r4.rag import RAGClient

    calls: list[tuple[str, dict]] = []

    def _delete(url: str, **kwargs: Any) -> dict:
        calls.append((url, kwargs))
        return {**SERVER_BODY, "document_id": "d1", "artifact_id": "artifact-rag-d1"}

    parent = SimpleNamespace(
        config=SimpleNamespace(gateway_url="https://gw", project_id="p1"),
        http=SimpleNamespace(delete=_delete),
    )
    rag = RAGClient(parent)

    result = rag.delete_document("d1")

    assert calls == [
        ("https://gw/gw/rag/documents/d1", {"params": {"project_id": "p1"}, "timeout": 30})
    ]
    assert isinstance(result, ArtifactDeleteResult)
    assert result.document_id == "d1" and result.artifact_id == "artifact-rag-d1"
    assert result.counts["artifacts"] == 1


def test_the_rag_client_lets_a_404_through():
    from types import SimpleNamespace

    from r4.rag import RAGClient

    def _delete(url: str, **kwargs: Any) -> dict:
        raise APIError("resolves to no knowledge artifact", status_code=404)

    parent = SimpleNamespace(
        config=SimpleNamespace(gateway_url="https://gw", project_id="p1"),
        http=SimpleNamespace(delete=_delete),
    )

    with pytest.raises(APIError):
        RAGClient(parent).delete_document("doc-unknown")


class _NoDeleteClient:
    class rag:  # noqa: N801 — attribute-shaped stub
        @staticmethod
        def delete_document(*_a: Any, **_k: Any) -> None:
            raise AssertionError("an agent tool reached delete_document")


def test_the_agent_rag_tools_offer_no_delete():
    """The LIVE agent catalog (``companion.tools.get_builtin_tools``) offers
    no RAG/knowledge delete tool — the pin rides the served list so it cannot
    point at a retired module (cleanup C-2(i); the old home,
    ``tui/rag_tools.py``, is deleted in C-2(ii))."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from r4.companion.tools import get_builtin_tools

    client = MagicMock()
    client.config = SimpleNamespace(
        gateway_url="http://gateway.test",
        api_key="test",
        model="test-model",
        project_id="demo",
    )
    names = {tool["name"] for tool in get_builtin_tools(client)}

    assert "rag_delete_document" not in names
    assert not {
        n for n in names if "delete" in n and (n.startswith("rag_") or n.startswith("knowledge_"))
    }


# ── the CLI ──────────────────────────────────────────────────────────────────


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="r4")
    KnowledgeCommand().register(parser.add_subparsers())
    return parser.parse_args(argv)


class _FakeKnowledge:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.error = error

    def delete_artifact(self, artifact_id: str, *, project_id: str | None = None):
        self.calls.append((artifact_id, project_id))
        if self.error is not None:
            raise self.error
        return ArtifactDeleteResult.from_dict(SERVER_BODY)


@pytest.fixture()
def fake_client(monkeypatch):
    from r4 import client as client_module

    knowledge = _FakeKnowledge()

    class _FakeClient:
        def __init__(self) -> None:
            self.knowledge = knowledge

    monkeypatch.setattr(client_module, "Client", _FakeClient)
    return knowledge


def test_delete_parses_with_project_and_yes():
    args = _parse(["knowledge", "delete", "art-a", "-p", "p1", "--yes"])

    assert args.knowledge_command == "delete"
    assert (args.artifact_id, args.project, args.yes) == ("art-a", "p1", True)


def test_delete_with_yes_prints_the_counts(fake_client, capsys):
    code = KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-a", "-p", "p1", "-y"]))

    out = capsys.readouterr().out
    assert code == 0
    assert fake_client.calls == [("art-a", "p1")]
    assert "Deleted artifact art-a from project p1" in out
    for line in ("nodes           : 6", "edges           : 5", "events_detached : 2"):
        assert line in out
    assert "⚠ object k2: failed — S3 refused" in out


def test_delete_asks_and_a_no_deletes_nothing(fake_client, monkeypatch, capsys):
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "n")

    code = KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-a", "-p", "p1"]))

    assert code == 1
    assert fake_client.calls == []
    assert "art-a" in prompts[0] and "cannot be undone" in prompts[0]
    assert "Aborted." in capsys.readouterr().out


def test_delete_without_a_terminal_answer_deletes_nothing(fake_client, monkeypatch):
    def _eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    assert KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-a", "-p", "p1"])) == 1
    assert fake_client.calls == []


def test_delete_asks_and_a_yes_deletes(fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _p: "yes")

    assert KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-a", "-p", "p1"])) == 0
    assert fake_client.calls == [("art-a", "p1")]


def test_delete_uses_the_current_project(fake_client, monkeypatch):
    monkeypatch.setattr("r4._cli.knowledge._current_project_id", lambda: "cur")

    assert KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-a", "--yes"])) == 0
    assert fake_client.calls == [("art-a", "cur")]


def test_delete_failure_is_reported_and_exits_nonzero(fake_client, capsys):
    fake_client.error = APIError("knowledge artifact 'art-x' not found", status_code=404)

    code = KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-x", "-p", "p1", "-y"]))

    assert code == 1
    assert "Delete failed" in capsys.readouterr().err


def test_delete_json_prints_the_whole_result(fake_client, capsys):
    import json

    KnowledgeCommand().handle(_parse(["knowledge", "delete", "art-a", "-p", "p1", "-y", "-j"]))

    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["artifacts"] == 1 and payload["deleted"] is True


def test_docs_delete_prints_what_the_server_deleted(monkeypatch, capsys):
    from r4 import client as client_module

    calls: list[tuple[str, str | None]] = []

    class _Rag:
        def delete_document(self, document_id: str, project_id: str | None = None):
            calls.append((document_id, project_id))
            return ArtifactDeleteResult.from_dict(
                {**SERVER_BODY, "document_id": document_id, "artifact_id": "artifact-rag-d1"}
            )

    class _FakeClient:
        rag = _Rag()

    monkeypatch.setattr(client_module, "Client", _FakeClient)

    code = KnowledgeCommand().handle(
        _parse(["knowledge", "docs", "delete", "d1", "-p", "p1", "--yes"])
    )

    out = capsys.readouterr().out
    assert code == 0
    assert calls == [("d1", "p1")]
    assert "Deleted document d1 (artifact artifact-rag-d1) from project p1" in out
    assert "nodes           : 6" in out


def test_docs_delete_reports_a_404_instead_of_deleted(monkeypatch, capsys):
    from r4 import client as client_module

    class _Rag:
        def delete_document(self, *_a: Any, **_k: Any):
            raise APIError("resolves to no knowledge artifact", status_code=404)

    class _FakeClient:
        rag = _Rag()

    monkeypatch.setattr(client_module, "Client", _FakeClient)

    code = KnowledgeCommand().handle(_parse(["knowledge", "docs", "delete", "d9", "--yes"]))

    captured = capsys.readouterr()
    assert code == 1
    assert "Deleted" not in captured.out
    assert "Delete failed" in captured.err
