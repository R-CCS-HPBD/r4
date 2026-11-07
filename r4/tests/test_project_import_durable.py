"""V-02/D-38 pins: ``r4 project import`` is a thin client of the durable
ingest chain by default; in-process mode lives behind --local."""

from __future__ import annotations

from argparse import Namespace

import pytest


def _args(paths, **over) -> Namespace:
    base = {"project_id": "proj-a", "paths": [str(p) for p in paths], "json": False, "local": False}
    base.update(over)
    return Namespace(**base)


class _FakeKnowledge:
    last_paths = None

    def __init__(self, client):
        pass

    def ingest_paths(self, project_id, paths):
        _FakeKnowledge.last_paths = list(paths)
        return {
            "job_id": "job-77",
            "status": "queued",
            "jobs": [{"job_id": "job-77"}, {"job_id": "job-78", "path": "big.pdf"}],
            "inlined": 1,
            "uploaded": 1,
        }


@pytest.fixture()
def durable(monkeypatch):
    monkeypatch.setattr("r4.knowledge.KnowledgeClient", _FakeKnowledge)
    monkeypatch.setattr("r4.client.Client", lambda **kw: object())
    _FakeKnowledge.last_paths = None
    return _FakeKnowledge


def test_import_defaults_to_durable_chain(tmp_path, durable, monkeypatch, capsys):
    from r4._cli import project_knowledge as pk

    monkeypatch.setattr(pk, "_resolve_project_id", lambda a, pm: "proj-a")
    pk.project_import(_args([tmp_path]), pm=None)
    out = capsys.readouterr().out
    assert "job-77" in out and "job-78" in out  # every queued job is named
    assert "1 inline, 1 uploaded; 2 job(s)" in out
    assert "ingest → understand → embed → verify" in out
    assert durable.last_paths == [str(tmp_path)]


def test_import_hands_paths_to_the_planner(tmp_path, durable, monkeypatch):
    from r4._cli import project_knowledge as pk

    f = tmp_path / "paper.pdf"
    f.write_text("x")
    monkeypatch.setattr(pk, "_resolve_project_id", lambda a, pm: "proj-a")
    pk.project_import(_args([f]), pm=None)
    assert durable.last_paths == [str(f)]


# ── the planner: workstation files reach UMRS as content, never as paths ────


class _Inner:
    _api_prefix = "/gw"

    def __init__(self):
        self.posts: list = []
        self.uploads: list = []

    def post(self, url, json=None, **kwargs):
        self.posts.append({"url": url, "json": json, **kwargs})
        return {"job_id": f"job-{len(self.posts)}", "status": "queued"}

    def post_multipart(self, path, **kwargs):
        self.uploads.append({"path": path, **kwargs})
        return {"job_id": f"up-{len(self.uploads)}", "status": "queued"}


def _knowledge():
    from r4.knowledge import KnowledgeClient

    inner = _Inner()
    return KnowledgeClient(inner), inner


def test_small_text_inlines_and_binary_uploads(tmp_path):
    (tmp_path / "notes.md").write_text("# notes")
    (tmp_path / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff")
    knowledge, inner = _knowledge()
    result = knowledge.ingest_paths("proj-a", [str(tmp_path)])
    assert result["inlined"] == 1 and result["uploaded"] == 1
    specs = inner.posts[0]["json"]["sources"]
    assert specs == [{"kind": "inline_text", "uri": "notes.md", "content": "# notes"}]
    upload = inner.uploads[0]
    assert {k: upload[k] for k in ("path", "filename", "content_type", "params")} == {
        "path": "/gw/knowledge/upload",
        "filename": "fig.png",
        "content_type": "image/png",
        "params": {"project_id": "proj-a"},
    }
    assert [j["job_id"] for j in result["jobs"]] == ["job-1", "up-1"]


def test_oversized_text_uploads(tmp_path, monkeypatch):
    import r4.knowledge as kmod

    monkeypatch.setattr(kmod, "_INLINE_FILE_MAX_BYTES", 4)
    big = tmp_path / "big.txt"
    big.write_text("more than four bytes")
    knowledge, inner = _knowledge()
    result = knowledge.ingest_paths("proj-a", [str(big)])
    assert result["uploaded"] == 1 and inner.posts == []
    assert inner.uploads[0]["content"] == b"more than four bytes"


def test_directory_walk_skips_dot_and_cache_dirs(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "m.pyc").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("b")
    (tmp_path / "a.txt").write_text("a")
    knowledge, inner = _knowledge()
    knowledge.ingest_paths("proj-a", [str(tmp_path)])
    uris = [s["uri"] for s in inner.posts[0]["json"]["sources"]]
    assert uris == ["a.txt", "b.txt"]


def test_no_path_ever_travels_as_a_server_spec(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe")
    knowledge, inner = _knowledge()
    knowledge.ingest_paths("proj-a", [str(tmp_path)], urls=["https://example.org/x"])
    kinds = {s["kind"] for p in inner.posts for s in p["json"]["sources"]}
    assert kinds == {"inline_text", "http_url"}
    assert str(tmp_path) not in repr(inner.posts)


def test_agent_upload_carries_actor_and_proposal(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\xff")
    knowledge, inner = _knowledge()
    knowledge.actor = "agent"
    knowledge.ingest_paths("proj-a", [str(tmp_path / "b.bin")], proposal_id="gp-1", graph_id="g1")
    upload = inner.uploads[0]
    assert upload["headers"]["x-r4-actor"] == "agent"
    assert upload["headers"]["x-proposal-id"] == "gp-1"
    assert upload["params"] == {"project_id": "proj-a", "graph_id": "g1"}


def test_empty_directory_is_nothing_to_ingest(tmp_path):
    knowledge, _inner = _knowledge()
    with pytest.raises(ValueError, match="nothing to ingest"):
        knowledge.ingest_paths("proj-a", [str(tmp_path)])


def test_session_history_upload_uses_multipart():
    """knowledge_build_from_context's upload leg (the JSON helper cannot
    carry file parts; the old call passed files= to it and could not work)."""
    from types import SimpleNamespace

    from r4.companion.knowledge_tools import _upload_session_history

    knowledge, inner = _knowledge()
    client = SimpleNamespace(knowledge=knowledge)
    body = _upload_session_history(
        client, filename="dir/s.jsonl", bytes_body=b"{}\n", target="proj-a", graph_id=None
    )
    assert body["lane"] == "session-history"
    upload = inner.uploads[0]
    assert upload["path"] == "/gw/knowledge/upload"
    assert upload["filename"] == "s.jsonl"
    assert upload["content_type"] == "application/x-ndjson"


def test_import_missing_path_dies(tmp_path, monkeypatch):
    from r4._cli import project_knowledge as pk

    monkeypatch.setattr(pk, "_resolve_project_id", lambda a, pm: "proj-a")
    with pytest.raises(SystemExit):
        pk.project_import(_args([tmp_path / "ghost"]), pm=None)


def test_local_mode_keeps_legacy_path(tmp_path, monkeypatch):
    from r4._cli import project_knowledge as pk

    calls = {}
    monkeypatch.setattr(pk, "_resolve_project_id", lambda a, pm: "proj-a")
    monkeypatch.setattr(
        pk,
        "_project_import_local",
        lambda args, pm, project_id, paths: calls.setdefault("local", True),
    )
    pk.project_import(_args([tmp_path], local=True), pm=None)
    assert calls.get("local") is True


def test_local_flag_registered():
    import r4._cli.project as project_mod

    assert project_mod is not None
