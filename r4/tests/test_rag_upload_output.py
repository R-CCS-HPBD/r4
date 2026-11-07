"""`r4 knowledge upload` says the document is queued, not "Chunks: 0".

The central facade answers ``status="queued-central"`` with the ingest job id
in ``document_id``; the CLI printed it as a document id with "Chunks: 0", as if
indexing had finished and produced nothing (stand-alone stack, 2026-10-09).
"""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from r4._cli.rag import _legacy_rag_upload
from r4.rag import UploadResult


def _client(result: UploadResult):
    return SimpleNamespace(rag=SimpleNamespace(upload=lambda *_a, **_k: result))


def _run(capsys, result: UploadResult) -> str:
    _legacy_rag_upload(Namespace(file="note.md", project="default"), _client(result))
    return capsys.readouterr().out


def test_queued_upload_names_the_job_and_how_to_follow_it(capsys):
    out = _run(
        capsys,
        UploadResult(document_id="job-4af1", filename="note.md", status="queued-central"),
    )
    assert "Uploaded: note.md" in out
    assert "Queued for knowledge ingest: job-4af1" in out
    assert "r4 job describe job-4af1" in out
    assert "Chunks: 0" not in out
    assert "Document ID" not in out


def test_indexed_upload_keeps_the_document_lines(capsys):
    out = _run(
        capsys,
        UploadResult(document_id="doc-7", filename="note.md", status="indexed", chunks=4),
    )
    assert "Document ID: doc-7" in out
    assert "Chunks: 4" in out


def test_query_sources_are_named_by_document(capsys):
    """Spine answers carry metadata.source_name; the node id is not a name."""
    from r4._cli.rag import _print_rag_sources
    from r4.rag import RAGSource

    _print_rag_sources(
        [
            RAGSource(
                document_id="node-chunk-art-a83a9490c9fb-0",
                content="Encapsulation with\nalumina slows moisture ingress.",
                score=0.79,
                metadata={"source_name": "perovskite-note.md", "corpus_venue": "spine"},
            ),
            RAGSource(document_id="legacy-17", content="x", score=0.5),
        ]
    )
    out = capsys.readouterr().out
    assert "[1] perovskite-note.md (score: 0.79)" in out
    assert "Encapsulation with alumina slows moisture ingress." in out
    assert "[2] legacy-17 (score: 0.50)" in out
