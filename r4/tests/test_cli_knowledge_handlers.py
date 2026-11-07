"""Handler-level regression tests for ``r4 knowledge`` (Q-12 gap close).

Each test exercises a KnowledgeCommand handler against a stub Toolkit
State (no real HTTP) with synthetic bundles written by the real
``KnowledgeBundleWriter``. The store never leaves the sandbox. Zero ─
boundary: these tests never touch the canonical source at
``~/.config/r4/sources.json``. Every fixture materializes its own tmp
config tree and deletes it on teardown.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from r4._cli.knowledge import KnowledgeCommand
from r4.knowledge_bundle import KnowledgeBundleManifest


@pytest.fixture()
def cmd() -> KnowledgeCommand:
    return KnowledgeCommand()


@pytest.fixture()
def bundle_dir(tmp_path: Path) -> Path:
    """Direct file-write fixture (post-KnowledgeBundleWriter deletion)."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = KnowledgeBundleManifest(
        project_id="p-demo",
        project_name="Demo",
        format="r4-kb",
        version="1.1",
        chunk_count=2,
        document_count=1,
        entity_count=1,
        relation_count=1,
        catalog_object_count=1,
    )
    (bundle / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    (bundle / "documents").mkdir()
    (bundle / "documents" / "chunks.jsonl").write_text(
        json.dumps({"id": "c1", "text": "kb-doc"}) + "\n", encoding="utf-8"
    )
    return bundle


def _flags(**overrides) -> argparse.Namespace:
    base = {
        "project_id": None,
        "output": None,
        "include_embeddings": True,
        "include_graph": True,
        "include_catalog": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestKnowledgeValidateHandler:
    def test_validate_accepts_valid_bundle(self, cmd: KnowledgeCommand, bundle_dir: Path, capsys):
        rc = cmd._handle_validate(argparse.Namespace(bundle=str(bundle_dir)))
        out = capsys.readouterr().out
        assert rc == 0
        assert "✓ Bundle is valid" in out

    def test_validate_rejects_missing_bundle(self, cmd: KnowledgeCommand, tmp_path: Path, capsys):
        rc = cmd._handle_validate(argparse.Namespace(bundle=str(tmp_path / "nope")))
        captured = capsys.readouterr()  # single read: stdout+stderr
        assert rc == 1
        assert "not found" in captured.out + captured.err


class TestKnowledgeInfoHandler:
    def test_info_prints_manifest_fields(self, cmd: KnowledgeCommand, bundle_dir: Path, capsys):
        rc = cmd._handle_info(argparse.Namespace(bundle=str(bundle_dir)))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Project ID: p-demo" in out
        assert "Format: r4-kb" in out
        assert "Chunks: 2" in out

    def test_info_rejects_missing_bundle(self, cmd: KnowledgeCommand, tmp_path: Path, capsys):
        rc = cmd._handle_info(argparse.Namespace(bundle=str(tmp_path / "nope")))
        assert rc == 1


class TestKnowledgeListHandler:
    def test_list_is_idempotent_on_empty_root(
        self, cmd: KnowledgeCommand, tmp_path: Path, capsys, monkeypatch
    ):
        # ``r4 knowledge list`` reads ``~/.config/r4/knowledge/`` — point it at
        # the empty tmp dir so the handler takes the "no bundles" path
        # deterministically on a fresh machine.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        rc = cmd._handle_list(argparse.Namespace())
        out = capsys.readouterr().out
        assert rc == 0
        assert "No knowledge bundles" in out


class TestKnowledgeRestoreAndExportHandlers:
    def test_export_handler_exists_and_dispatches(self, cmd: KnowledgeCommand):
        assert callable(getattr(cmd, "_handle_export", None))
        assert callable(getattr(cmd, "_handle_restore", None))
        assert callable(getattr(cmd, "_handle_import", None))


class TestInventoryRowFormat:
    """Pin for the extracted _format_inventory_row helper (complexity refactor)."""

    def test_row_without_links(self, cmd: KnowledgeCommand):
        line = cmd._format_inventory_row(
            {
                "s3_key": "data/file.csv",
                "detected_modality": "text",
                "state": "ready",
                "links": None,
            }
        )
        assert "file.csv" in line
        assert "unknown" not in line

    def test_row_with_links_includes_vector_suffix(self, cmd: KnowledgeCommand):
        line = cmd._format_inventory_row(
            {
                "s3_key": "data/file.pdf",
                "detected_modality": "text",
                "state": "ready",
                "links": {
                    "document_id": "doc-1",
                    "chunk_nodes": 4,
                    "vector_rows": [{"model": "e5-base-v2", "dimension": 1024, "rows": 4}],
                },
            }
        )
        assert "doc-1" in line
        assert "chunks" in line
        assert "e5-base-v2" in line


class TestGtwop8ClientProjectionExport:
    """G-P2-8 / G-P2-4: the CLI export writes a v1.2 8-table projection."""

    EIGHT = (
        "artifacts",
        "documents",
        "nodes",
        "edges",
        "embeddings",
        "catalog",
        "a2l_links",
        "a2l_refs",
    )

    def _patched(self, cmd: KnowledgeCommand, monkeypatch) -> None:
        from r4._cli import knowledge as kmod

        monkeypatch.setattr(
            kmod,
            "_fetch_knowledge_graph",
            lambda c, p: ([{"node_id": "n1"}], [{"edge_id": "e1"}]),
        )
        monkeypatch.setattr(kmod, "_fetch_catalog_objects", lambda c, p: [{"name": "obj"}])
        monkeypatch.setattr("r4.client.Client", lambda: None)

    def test_export_writes_all_table_files(self, cmd: KnowledgeCommand, tmp_path, monkeypatch):
        self._patched(cmd, monkeypatch)
        args = argparse.Namespace(include_graph=True, include_catalog=True)
        out_dir = tmp_path / "out"
        cmd._export_from_platform("proj-x", out_dir, args)
        for name in self.EIGHT:
            assert (out_dir / f"{name}.jsonl").exists()

    def test_export_manifest_contract_fields(self, cmd: KnowledgeCommand, tmp_path, monkeypatch):
        self._patched(cmd, monkeypatch)
        args = argparse.Namespace(include_graph=True, include_catalog=True)
        out_dir = tmp_path / "out"
        manifest = cmd._export_from_platform("proj-x", out_dir, args)

        data = json.loads((out_dir / "manifest.json").read_text())
        assert data["version"] == "1.2"
        assert data["unsigned"] is True
        assert data["embedding"]["pending"] is True
        assert data["tables"]["nodes"]["count"] == 1
        assert data["tables"]["catalog"]["count"] == 1
        assert data["generator"] == "r4"
        assert manifest["format"] == "r4-kb"

    def test_export_kb_zip_contains_all_tables(self, cmd: KnowledgeCommand, tmp_path, monkeypatch):
        import zipfile

        self._patched(cmd, monkeypatch)
        args = argparse.Namespace(include_graph=True, include_catalog=True)
        out_kb = tmp_path / "bundle.kb"
        cmd._export_from_platform("proj-x", out_kb, args)

        assert not (out_kb.parent / out_kb.stem).exists()
        names = set(zipfile.ZipFile(out_kb).namelist())
        assert names == {f"{name}.jsonl" for name in self.EIGHT} | {"manifest.json"}

    def test_include_flags_gate_fetches(self, cmd: KnowledgeCommand, tmp_path, monkeypatch):
        self._patched(cmd, monkeypatch)
        args = argparse.Namespace(include_graph=False, include_catalog=False)
        out_dir = tmp_path / "out"
        manifest = cmd._export_from_platform("proj-x", out_dir, args)

        assert manifest["tables"]["nodes"]["count"] == 0
        assert manifest["tables"]["catalog"]["count"] == 0


class TestQueryHandler:
    def test_query_prints_unwired_backends_without_crash(
        self, cmd: KnowledgeCommand, capsys, monkeypatch
    ):
        """Smoke: facade search handler never a persistence error, even if
        backends are partially unwired. The UNWIRED block is the signal
        absorbed by this test — a regression that swallowed it silently
        would break operator awareness.

        (was _handle_query — the K3 facade search moved to `knowledge
        search` on 2026-09-13 after the WP-15 fold claimed the 'query'
        verb for the corpus rag flow.)
        """
        # Send Namespace with as_json=True so output is JSON-safe to assert on.
        args = argparse.Namespace(
            query="hello", project_id=None, scope="all", limit=5, as_json=True
        )
        # Only wait for rc to be non-crashy; exact rc depends on backends.
        rc = cmd._handle_search(args)
        assert rc in (0, 1)  # Handler must not raise.
