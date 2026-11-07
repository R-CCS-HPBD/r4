"""Export/import CLI output (D-40 transport, ADR-032 #1).

`r4 project export --strict` exits non-zero on a partial export (the return
value used to be discarded); `r4 knowledge import` names documents already
present under another id.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from r4._cli import knowledge as knowledge_cli
from r4._cli import project_knowledge


class _PM:
    def get_current(self):
        return "proj"

    def get(self, _project_id):
        return {"id": "proj"}


_PARTIAL = {"status": "ready", "partial": True, "partial_reason": "2 artifacts"}


@pytest.mark.parametrize(("strict", "expected"), [(True, 1), (False, 0)])
def test_project_export_returns_the_printed_exit_code(monkeypatch, capsys, strict, expected):
    class _Knowledge:
        @staticmethod
        def export(_project_id, **_kw):
            return _PARTIAL

    class _Client:
        knowledge = _Knowledge()

    monkeypatch.setattr("r4.client.Client", _Client)
    args = Namespace(project_id="proj", json=False, detach=False, strict=strict)
    assert project_knowledge.project_export(args, _PM()) == expected
    if strict:
        assert "--strict mode" in capsys.readouterr().err


def test_canonical_matches_are_named_and_capped(capsys):
    matches = [
        {
            "incoming_artifact_id": f"in-{i}",
            "local_artifact_id": f"loc-{i}",
            "document_id": f"kdoc-{i}",
        }
        for i in range(7)
    ]
    knowledge_cli._print_canonical_matches(matches)
    out = capsys.readouterr().out
    assert "already present under another id: 7" in out
    assert "in-0 = local loc-0 (kdoc-0)" in out
    assert "in-5" not in out
    assert "2 more" in out


def test_no_matches_prints_nothing(capsys):
    knowledge_cli._print_canonical_matches([])
    assert capsys.readouterr().out == ""


# ── the reader opens the server export archive (<job>.kb.tar.gz) ─────────────


def _archive(tmp_path, members):
    """members: name -> bytes (a regular file) or "symlink" (a link)."""
    import io
    import tarfile

    path = tmp_path / "job-1.kb.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if data == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)
                continue
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_reader_opens_the_server_archive_under_its_bundle_dir(tmp_path):
    from r4.knowledge_bundle import KnowledgeBundleReader

    manifest = b'{"format": "r4-kb", "version": "1.2", "project_id": "proj"}'
    reader = KnowledgeBundleReader(_archive(tmp_path, {"bundle/manifest.json": manifest}))
    assert reader.read_manifest().project_id == "proj"
    extracted = reader._extracted_dir
    reader.cleanup()
    assert not extracted.exists()


@pytest.mark.parametrize(
    "members",
    [{"../escape.json": b"{}"}, {"/abs.json": b"{}"}, {"bundle/link": "symlink"}],
)
def test_reader_refuses_hostile_archives(tmp_path, members):
    from r4.knowledge_bundle import KnowledgeBundleReader

    with pytest.raises(ValueError, match="bundle archive"):
        KnowledgeBundleReader(_archive(tmp_path, members))
    assert not (tmp_path.parent / "escape.json").exists()


def test_verification_label_distinguishes_table_coverage():
    label = knowledge_cli._verification_label
    assert label({"verified": True, "tables_verified": True}) == " (verified)"
    assert "table contents not pinned" in label({"verified": True})
    assert label({}) == " (unverified)"


# ── raw materials (ADR-032 amendment 2026-09-24) ─────────────────────────────


def test_project_export_forwards_the_raw_flags(monkeypatch):
    seen = {}

    class _Knowledge:
        @staticmethod
        def export(project_id, **kw):
            seen.update(kw, project_id=project_id)
            return {"status": "processing"}

    class _Client:
        knowledge = _Knowledge()

    monkeypatch.setattr("r4.client.Client", _Client)
    args = Namespace(
        project_id="proj",
        json=False,
        detach=False,
        strict=False,
        with_raw=True,
        acknowledge_sensitive=True,
    )
    assert project_knowledge.project_export(args, _PM()) == 0
    assert seen == {
        "project_id": "proj",
        "include_raw": True,
        "acknowledge_sensitive": True,
        "chunk_text": False,
        "allow_unsigned": False,  # cycle-3: never unsigned unless asked
    }


def test_client_export_body_carries_raw_only_when_asked():
    from r4.knowledge import KnowledgeClient

    posted = []

    class _Http:
        _api_prefix = "/gw"

        def post(self, url, json):
            posted.append(json)
            return {}

    client = KnowledgeClient.__new__(KnowledgeClient)
    client._client = _Http()
    client._knowledge_url = lambda name: f"/gw/knowledge/{name}"
    client.export("p")
    client.export("p", include_raw=True)
    assert posted == [
        {"project_id": "p"},
        {"project_id": "p", "include_raw": True, "acknowledge_sensitive": False},
    ]


# ── chunk text (ADR-093 D-12) ────────────────────────────────────────────────


def _export_args(argv):
    import argparse

    from r4._cli.project import ProjectCommand

    root = argparse.ArgumentParser()
    ProjectCommand().register(root.add_subparsers(dest="command"))
    return root.parse_args(["project", "export", *argv])


def test_export_parses_chunk_text_off_by_default():
    assert _export_args(["proj"]).chunk_text is False
    assert _export_args(["--chunk-text", "proj"]).chunk_text is True


def test_project_export_forwards_the_chunk_text_flag(monkeypatch):
    seen = {}

    class _Knowledge:
        @staticmethod
        def export(project_id, **kw):
            seen.update(kw)
            return {"status": "processing"}

    class _Client:
        knowledge = _Knowledge()

    monkeypatch.setattr("r4.client.Client", _Client)
    args = _export_args(["--chunk-text", "proj"])
    assert project_knowledge.project_export(args, _PM()) == 0
    assert (seen["chunk_text"], seen["include_raw"]) == (True, False)


def test_client_export_body_carries_chunk_text_only_when_asked():
    from r4.knowledge import KnowledgeClient

    posted = []

    class _Http:
        def post(self, url, json):
            posted.append(json)
            return {}

    client = KnowledgeClient.__new__(KnowledgeClient)
    client._client = _Http()
    client._knowledge_url = lambda name: f"/gw/knowledge/{name}"
    client.export("p")
    client.export("p", chunk_text=True)
    assert posted == [{"project_id": "p"}, {"project_id": "p", "chunk_text": True}]


def test_client_export_body_carries_allow_unsigned_only_when_asked():
    """A server without UMRS_SECRET_KEY refuses an export unless the caller
    opts out explicitly (2026-10-12)."""
    from r4.knowledge import KnowledgeClient

    posted = []

    class _Http:
        def post(self, url, json):
            posted.append(json)
            return {}

    client = KnowledgeClient.__new__(KnowledgeClient)
    client._client = _Http()
    client._knowledge_url = lambda name: f"/gw/knowledge/{name}"
    client.export("p")
    client.export("p", allow_unsigned=True)
    assert posted == [{"project_id": "p"}, {"project_id": "p", "allow_unsigned": True}]


def test_raw_outcome_line(capsys):
    knowledge_cli._print_raw_outcome({"raw_restored": 3, "raw_declined": 0, "raw_unbound": 1})
    assert "Raw objects: 3 restored, 1 unbound" in capsys.readouterr().out
    knowledge_cli._print_raw_outcome({})
    assert capsys.readouterr().out == ""
