"""P1b (ADR-027): `r4 rag` corpus verbs fold into `r4 knowledge`.

Covers: knowledge subtree offers upload/bulk-upload/docs/stats; those
handlers delegate into the single-sourced `._cli.rag` implementations;
`r4 rag <corpus-verb>` prints the fold notice while behaving identically.
"""

from __future__ import annotations

import argparse

from r4._cli.knowledge import KnowledgeCommand
from r4._cli.rag import RagCommand


def _register(handler_cls):
    parser = argparse.ArgumentParser(prog="r4")
    subparsers = parser.add_subparsers()
    handler_cls().register(subparsers)
    return parser


def test_knowledge_registers_corpus_verbs():
    parser = _register(KnowledgeCommand)
    for argv in (
        ["knowledge", "upload", "f.txt", "-p", "demo"],
        ["knowledge", "bulk-upload", "./dir", "-p", "demo"],
        ["knowledge", "docs", "list", "-p", "demo"],
        ["knowledge", "docs", "delete", "doc-1", "-p", "demo", "--yes"],
        ["knowledge", "stats", "-p", "demo"],
        ["knowledge", "query", "fold proof", "-p", "demo"],
    ):
        args = parser.parse_args(argv)
        assert args is not None


def test_rag_corpus_verbs_emit_fold_notice(capsys, monkeypatch):
    for sub in ("upload", "bulk-upload", "query", "docs", "stats"):
        monkeypatch.setattr(RagCommand, "_FOLDED_SUBCOMMANDS", (sub,))
        handler = RagCommand()
        out = handler._FOLD_NOTICE.format(sub=sub)
        assert "r4 knowledge" in out
        assert "ADR-027" in out or "P1b" in out
        assert sub in out
    # ingest retired 2026-10-18 (H-10.1 follow-up): the notice names only retriever.
    assert "`r4 rag retriever` stays here" in RagCommand._FOLD_NOTICE
    assert "ingest" not in RagCommand._FOLD_NOTICE


def test_rag_fold_tuple_matches_pipeline_config_exclusion():
    # Pipeline-config trees explicitly stay under rag (durable ingest is
    # served by `r4 knowledge graph-ingest` — a different owner).
    assert set(RagCommand._FOLDED_SUBCOMMANDS) == {
        "upload",
        "bulk-upload",
        "query",
        "docs",
        "stats",
    }


def test_knowledge_docs_delegates_to_rag(monkeypatch):
    from r4._cli import rag as rag_module

    calls: list[argparse.Namespace] = []

    def _fake(args):
        calls.append(args)
        return 0

    monkeypatch.setattr(rag_module, "_handle_docs", _fake)
    args = argparse.Namespace(docs_command="list", project="demo", as_json=False)
    assert KnowledgeCommand()._handle_docs(args) == 0
    assert calls and calls[0].docs_command == "list"


def test_knowledge_upload_delegates_to_rag(monkeypatch):
    from r4._cli import rag as rag_module

    calls: list[argparse.Namespace] = []

    def _fake(args):
        calls.append(args)

    monkeypatch.setattr(rag_module, "cmd_rag", _fake)
    args = argparse.Namespace(file="f.txt", project="demo")
    assert KnowledgeCommand()._handle_upload(args) == 0
    assert calls and calls[0].rag_command == "upload"
    assert calls[0].file == "f.txt"


def test_knowledge_handle_dispatches_fold_verbs(monkeypatch):
    seen: list[str] = []
    for verb in ("upload", "bulk-upload", "docs", "stats", "query"):
        method = f"_handle_{verb.replace('-', '_')}"
        monkeypatch.setattr(KnowledgeCommand, method, lambda self, a, v=verb: seen.append(v) or 0)
    for verb in ("stats", "query"):
        args = argparse.Namespace(knowledge_command=verb)
        assert KnowledgeCommand().handle(args) == 0
    assert seen == ["stats", "query"]
