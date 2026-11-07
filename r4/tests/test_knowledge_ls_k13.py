"""K1.3 one listing truth (knowledge plan 2026-10-24), r4 side.

- ``r4 knowledge ls`` lists the server's ``/k2/artifacts`` rows with who
  added each item, when, its kind, size, indexed state and graph (there was
  no listing of project knowledge; ``r4 knowledge list`` listed local
  bundles, now ``bundles``).
- ``r4 knowledge docs list`` names who added a document.
- ``r4 knowledge query`` no longer forces ``--threshold 0.7`` over the
  server's adaptive cut (F6).
- TUI ``/rag upload`` uploads (F1); ``/rag docs delete <id>`` deletes (F7).

The personal-graph rule is the server's: umrs
``tests/test_k1_spine_listing_pg.py`` pins it for ``ls``, ``docs list`` and
``stats`` on Postgres.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from r4._cli.knowledge import KnowledgeCommand
from r4._cli.knowledge_ls import default_graph_id, parse_since
from r4.cli import _build_parser
from r4.knowledge import KnowledgeClient

PROJECT = "example-project-b"
DEFAULT = default_graph_id(PROJECT)
ARTIFACT = {
    "artifact_id": "art-0123",
    "kind": "document",
    "project_id": PROJECT,
    "size_bytes": 2048,
    "status": "active",
    "embedding_status": "current",
    "metadata": {"source_name": "notes.md"},
    "last_added": "2026-10-24T09:30:00+00:00",
    "contributions": [
        {"graph_id": DEFAULT, "user_id": "alice", "origin_actor": "user", "title": "notes.md"},
        {"graph_id": "g-methods", "user_id": "bob", "origin_actor": "user", "title": ""},
    ],
}


class _FakeUmrs:
    _api_prefix = "/gw"

    def __init__(self) -> None:
        self._config = SimpleNamespace(project_id=PROJECT, umrs_username="alice")
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, dict(kwargs.get("params") or {}), kwargs.get("headers") or {}))
        if url == "/gw/knowledge/ingest/artifacts":
            return {"artifacts": [ARTIFACT], "total": 1, "page": 1, "page_size": 25}
        if url == "/gw/knowledge/graphs":
            return {"graphs": [{"graph_id": "g-methods", "name": "methods"}]}
        if url == "/gw/knowledge/graphs/methods":
            return {"graph_id": "g-methods", "name": "methods"}
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture
def umrs(monkeypatch) -> _FakeUmrs:
    server = _FakeUmrs()
    client = SimpleNamespace(knowledge=KnowledgeClient(server), _config=server._config)  # type: ignore[arg-type]
    monkeypatch.setattr("r4.client.Client", lambda: client)
    return server


def _run(*argv: str) -> int:
    args = _build_parser().parse_args(["knowledge", *argv])
    return KnowledgeCommand().handle(args)


def _listing_call(umrs: _FakeUmrs) -> tuple[dict[str, Any], dict[str, Any]]:
    url, params, headers = next(c for c in umrs.calls if c[0].endswith("/artifacts"))
    return params, headers


def test_ls_shows_an_upload_with_its_contributor_and_graph(umrs, capsys):
    assert _run("ls", "-p", PROJECT) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["ADDED", "WHO", "KIND", "SIZE", "INDEXED", "GRAPH", "ID", "TITLE"]
    row = out[1].split()
    assert row[2:] == [
        "alice,bob",
        "document",
        "2.0KB",
        "yes",
        "default,methods",
        "art-0123",
        "notes.md",
    ]
    params, headers = _listing_call(umrs)
    assert headers == {"X-Project-ID": PROJECT}
    assert params == {"page": 1, "page_size": 25}


def test_ls_filters_reach_the_server(umrs, capsys):
    assert _run("ls", "-p", PROJECT, "--mine", "--graph", "methods", "--kind", "code") == 0
    params, _headers = _listing_call(umrs)
    assert (params["created_by"], params["graph_id"], params["kind"]) == (
        "alice",
        "g-methods",
        "code",
    )
    umrs.calls.clear()
    assert _run("ls", "-p", PROJECT, "--by", "bob", "--since", "2026-10-01") == 0
    params, _headers = _listing_call(umrs)
    assert (params["created_by"], params["since"]) == ("bob", "2026-10-01")


def test_ls_json_is_the_server_payload(umrs, capsys):
    assert _run("ls", "-p", PROJECT, "--json") == 0
    assert json.loads(capsys.readouterr().out)["artifacts"][0]["artifact_id"] == "art-0123"


def test_relative_since_is_an_iso_time():
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    assert parse_since("7d", now=now) == "2026-10-17T12:00:00+00:00"
    assert parse_since("24h", now=now) == "2026-10-23T12:00:00+00:00"
    with pytest.raises(ValueError, match="24h / 7d / 2w or an ISO date"):
        parse_since("last week", now=now)


@pytest.mark.parametrize("verb", ["bundles", "list"])
def test_bundles_keeps_list_as_an_alias(verb, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _run(verb) == 0
    assert "No knowledge bundles found." in capsys.readouterr().out


def test_query_leaves_the_threshold_to_the_server(monkeypatch):
    """F6: ``--threshold`` defaulted to 0.7 and overrode the adaptive cut."""
    seen: dict[str, Any] = {}

    def _query(query: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(answer="ok", sources=[])

    client = SimpleNamespace(rag=SimpleNamespace(query=_query))
    monkeypatch.setattr("r4.client.Client", lambda: client)
    assert _run("query", "what is MOF-5", "-p", PROJECT) == 0
    assert seen["threshold"] is None


def test_docs_list_names_who_added_a_document(monkeypatch, capsys):
    doc = {
        "document_id": "art-0123",
        "filename": "notes.md",
        "metadata": {"contributors": ["alice"], "source": "spine"},
    }
    client = SimpleNamespace(rag=SimpleNamespace(list_documents=lambda **_k: [doc]))
    monkeypatch.setattr("r4.client.Client", lambda: client)
    assert _run("docs", "list", "-p", PROJECT) == 0
    assert "- art-0123: notes.md  (added by alice)" in capsys.readouterr().out


# ── TUI /rag (Python registry, reached by the v3 companion forward) ─────────


class _Renderer:
    def __init__(self) -> None:
        self.info_lines: list[str] = []
        self.error_lines: list[str] = []

    def info(self, text: str) -> None:
        self.info_lines.append(text)

    def error(self, text: str) -> None:
        self.error_lines.append(text)


def _tui_app(rag: Any) -> Any:
    client = SimpleNamespace(config=SimpleNamespace(project_id=PROJECT), rag=rag)
    return SimpleNamespace(client=client, renderer=_Renderer())


def test_tui_rag_upload_uploads(tmp_path):
    """F1: ``/rag upload`` printed a hint and uploaded nothing."""
    from r4.tui.commands import _cmd_rag

    paper = tmp_path / "paper.md"
    paper.write_text("# MOF-5\n")
    uploads: list[tuple[str, str]] = []

    def _upload(path: str, *, project_id: str) -> Any:
        uploads.append((path, project_id))
        return SimpleNamespace(document_id="job-1", status="queued-central")

    app = _tui_app(SimpleNamespace(upload=_upload))
    _cmd_rag(app, ["upload", str(paper)])
    assert uploads == [(str(paper), PROJECT)]
    assert any(
        "queued for knowledge ingest (job job-1)" in line for line in app.renderer.info_lines
    )
    assert not any("shows in /rag docs now" in line for line in app.renderer.info_lines)


def test_tui_rag_docs_delete_deletes(tmp_path):
    """F7: ``/rag docs delete X`` listed the documents of a project named "delete"."""
    from r4.tui.commands import _cmd_rag

    calls: list[tuple[str, Any]] = []

    def _delete(document_id: str, *, project_id: str) -> Any:
        calls.append(("delete", document_id, project_id))
        return SimpleNamespace(counts={"artifacts": 1, "nodes": 3})

    def _list(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(("list", kwargs))
        return []

    app = _tui_app(SimpleNamespace(delete_document=_delete, list_documents=_list))
    _cmd_rag(app, ["docs", "delete", "art-0123", "--yes"])
    assert calls == [("delete", "art-0123", PROJECT)]
    expected = f"deleted art-0123 from project {PROJECT}  artifacts=1, nodes=3"
    assert expected in app.renderer.info_lines


# ── review of K1 part B (2026-10-24) ──────────────────────────────────────


def test_tui_rag_docs_delete_asks_for_yes():
    """``/rag docs delete <id>`` deleted the artifact and everything derived
    at once; ``r4 knowledge docs delete`` asks unless --yes."""
    from r4.tui.commands import _cmd_rag

    calls: list[str] = []
    rag = SimpleNamespace(delete_document=lambda doc, **_k: calls.append(doc))
    app = _tui_app(rag)
    _cmd_rag(app, ["docs", "delete", "art-0123"])
    assert calls == []
    assert "add --yes to delete" in app.renderer.info_lines[-1]


def test_ls_marks_how_each_person_added_an_item(umrs, capsys):
    """The K1.6 exit: a kept answer, an agent ingest and an upload all read
    "alice"; the tier never showed."""
    contributions = [
        {"graph_id": DEFAULT, "user_id": "alice", "origin_actor": "agent_authored_user_kept"},
        {"graph_id": DEFAULT, "user_id": "bob", "origin_actor": "agent"},
        {"graph_id": DEFAULT, "user_id": "mika", "origin_actor": "agent_authored_user_vouched"},
        {"graph_id": DEFAULT, "user_id": "rin", "origin_actor": "user"},
    ]
    umrs_artifact = {**ARTIFACT, "contributions": contributions}
    umrs.get = lambda url, **_k: (  # type: ignore[method-assign]
        {"artifacts": [umrs_artifact], "total": 1, "page": 1}
        if url.endswith("/artifacts")
        else {"graphs": []}
    )
    assert _run("ls", "-p", PROJECT) == 0
    row = capsys.readouterr().out.splitlines()[1]
    assert "alice (kept),bob (agent),mika (vouched),rin" in row


def test_ls_cuts_a_long_title(umrs, capsys):
    title = "A very long default save title that is the first line of an answer, cut"
    contributions = [
        {"graph_id": DEFAULT, "user_id": "alice", "origin_actor": "user", "title": title}
    ]
    umrs_artifact = {**ARTIFACT, "contributions": contributions}
    umrs.get = lambda url, **_k: (  # type: ignore[method-assign]
        {"artifacts": [umrs_artifact], "total": 1} if url.endswith("/artifacts") else {"graphs": []}
    )
    assert _run("ls", "-p", PROJECT) == 0
    row = capsys.readouterr().out.splitlines()[1]
    assert row.endswith(title[:47] + "…")
    assert "art-0123" in row


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("--since", "yesterday"), "--since takes 24h / 7d / 2w or an ISO date"),
        (("--mine",), "--mine needs your user id"),
    ],
)
def test_ls_usage_errors_exit_2_with_the_remedy(umrs, capsys, argv, message):
    umrs._config.umrs_username = None
    assert _run("ls", "-p", PROJECT, *argv) == 2
    err = capsys.readouterr().err
    assert message in err and "knowledge ls failed" not in err


def test_ls_json_errors_are_json(umrs, capsys):
    assert _run("ls", "-p", PROJECT, "--since", "yesterday", "--json") == 2
    out = json.loads(capsys.readouterr().out)
    assert out["exit_code"] == 2 and "--since takes" in out["error"]


def test_ls_graph_name_resolves_within_the_project(umrs, capsys):
    """``--graph methods`` asked the registry by name with no project: a
    graph of that name in another project won and filtered to nothing."""
    graphs = [
        {"graph_id": "g-other", "name": "methods", "project_id": "other-project"},
        {"graph_id": "g-mine", "name": "methods", "project_id": PROJECT},
    ]

    def _get(url: str, **kwargs: Any) -> Any:
        umrs.calls.append((url, dict(kwargs.get("params") or {}), {}))
        if url.endswith("/artifacts"):
            return {"artifacts": [], "total": 0}
        if url == "/gw/knowledge/graphs":
            return {"graphs": graphs}
        return {"graph_id": "g-other", "name": "methods"}

    umrs.get = _get  # type: ignore[method-assign]
    assert _run("ls", "-p", PROJECT, "--graph", "methods") == 0
    params, _headers = _listing_call(umrs)
    assert params["graph_id"] == "g-mine"
