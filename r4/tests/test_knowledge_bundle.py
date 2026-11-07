"""Tests for the Knowledge Bundle Format (ADR-013, INV-13)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from r4.knowledge_bundle import (
    BUNDLE_ACCEPTED_VERSIONS,
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    KnowledgeBundleManifest,
    KnowledgeBundleReader,
    validate_bundle,
)


def _write_flat_bundle(
    path: Path,
    *,
    manifest: KnowledgeBundleManifest | None = None,
    chunks: list[dict] | None = None,
    embeddings: list[dict] | None = None,
    entities: list[dict] | None = None,
    relations: list[dict] | None = None,
    catalog: list[dict] | None = None,
) -> Path:
    """Direct file-write replacement for the deleted KnowledgeBundleWriter."""
    path.mkdir(parents=True, exist_ok=True)
    manifest = manifest or KnowledgeBundleManifest(
        project_id="test-project",
        project_name="Test Project",
        created_at=KnowledgeBundleManifest.now(),
    )
    (path / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    for rel, rows in {
        "documents/chunks.jsonl": chunks,
        "embeddings/embeddings.jsonl": embeddings,
    }.items():
        _write_jsonl(path / rel, rows)
    _write_graph_and_catalog(path, entities, relations, catalog)
    return path


def _write_jsonl(path: Path, rows: list[dict] | None) -> None:
    """Write rows as JSONL if any (no-op for empty/None)."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _write_graph_and_catalog(
    path: Path,
    entities: list[dict] | None,
    relations: list[dict] | None,
    catalog: list[dict] | None,
) -> None:
    """Write graph JSON pair and catalog JSON (each conditional on content)."""
    if entities or relations:
        (path / "graph").mkdir()
        (path / "graph/entities.json").write_text(
            json.dumps(list(entities or [])), encoding="utf-8"
        )
        (path / "graph/relations.json").write_text(
            json.dumps(list(relations or [])), encoding="utf-8"
        )
    if catalog:
        (path / "catalog").mkdir()
        (path / "catalog/catalog.json").write_text(json.dumps(catalog), encoding="utf-8")


# ---------------------------------------------------------------------------
# Manifest Tests
# ---------------------------------------------------------------------------


class TestKnowledgeBundleManifest:
    def test_default_values(self) -> None:
        manifest = KnowledgeBundleManifest()
        assert manifest.format == BUNDLE_FORMAT
        assert manifest.version == BUNDLE_VERSION
        assert manifest.embedding_model == "intfloat/e5-base-v2"
        assert manifest.embedding_dimension == 768

    def test_to_dict(self) -> None:
        manifest = KnowledgeBundleManifest(
            project_id="test-project",
            project_name="Test Project",
            chunk_count=42,
        )
        d = manifest.to_dict()
        assert d["project_id"] == "test-project"
        assert d["chunk_count"] == 42
        assert d["format"] == BUNDLE_FORMAT

    def test_to_json(self) -> None:
        manifest = KnowledgeBundleManifest(project_id="test")
        j = manifest.to_json()
        data = json.loads(j)
        assert data["project_id"] == "test"

    def test_from_dict(self) -> None:
        data = {
            "format": "r4-kb",
            "version": "1.0",
            "project_id": "my-project",
            "project_name": "My Project",
            "created_at": "2026-07-05T12:00:00Z",
            "embedding_model": "intfloat/e5-base-v2",
            "embedding_dimension": 768,
            "document_count": 10,
            "chunk_count": 50,
            "entity_count": 20,
            "relation_count": 30,
            "catalog_object_count": 10,
        }
        manifest = KnowledgeBundleManifest.from_dict(data)
        assert manifest.project_id == "my-project"
        assert manifest.chunk_count == 50

    def test_roundtrip(self) -> None:
        manifest = KnowledgeBundleManifest(
            project_id="test-project",
            project_name="Test Project",
            chunk_count=42,
            entity_count=10,
            relation_count=15,
        )
        data = manifest.to_dict()
        restored = KnowledgeBundleManifest.from_dict(data)
        assert restored.project_id == "test-project"
        assert restored.chunk_count == 42
        assert restored.entity_count == 10
        assert restored.relation_count == 15


# ---------------------------------------------------------------------------
# Bundle Writer/Reader Tests
# ---------------------------------------------------------------------------


class TestKnowledgeBundleWriterReader:
    @pytest.fixture
    def tmp_bundle_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "test-project.kb.d"

    @pytest.fixture
    def sample_chunks(self) -> list[dict]:
        return [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "content": "This is a test chunk about machine learning.",
                "metadata": {"page": 1, "source": "test.pdf"},
            },
            {
                "chunk_id": "chunk-2",
                "document_id": "doc-1",
                "content": "Another chunk about neural networks.",
                "metadata": {"page": 2, "source": "test.pdf"},
            },
        ]

    @pytest.fixture
    def sample_embeddings(self) -> list[dict]:
        return [
            {
                "chunk_id": "chunk-1",
                "embedding": [0.1, 0.2, 0.3],
                "document_id": "doc-1",
            },
            {
                "chunk_id": "chunk-2",
                "embedding": [0.4, 0.5, 0.6],
                "document_id": "doc-1",
            },
        ]

    @pytest.fixture
    def sample_graph(self) -> tuple[list[dict], list[dict]]:
        entities = [
            {"id": "entity-1", "type": "concept", "name": "Machine Learning"},
            {"id": "entity-2", "type": "concept", "name": "Neural Networks"},
        ]
        relations = [
            {
                "id": "relation-1",
                "source": "entity-1",
                "target": "entity-2",
                "type": "related_to",
            },
        ]
        return entities, relations

    @pytest.fixture
    def sample_catalog(self) -> list[dict]:
        return [
            {
                "object_id": "obj-1",
                "s3_key": "data/test.csv",
                "detected_modality": "structured",
                "summary": "Test dataset",
            },
        ]

    def _write_sample_bundle(
        self,
        tmp_bundle_dir: Path,
        sample_chunks: list[dict],
        sample_embeddings: list[dict],
        sample_graph: tuple,
        sample_catalog: list[dict],
    ) -> Path:
        """Write a flat-layout bundle with every table populated."""
        return _write_flat_bundle(
            tmp_bundle_dir,
            manifest=KnowledgeBundleManifest(
                project_id="test-project",
                project_name="Test Project",
                created_at=KnowledgeBundleManifest.now(),
                document_count=1,
                chunk_count=2,
                entity_count=2,
                relation_count=1,
                catalog_object_count=1,
            ),
            chunks=sample_chunks,
            embeddings=sample_embeddings,
            entities=list(sample_graph[0]),
            relations=list(sample_graph[1]),
            catalog=sample_catalog,
        )

    def test_write_and_read_directory_layout(
        self,
        tmp_bundle_dir: Path,
        sample_chunks: list[dict],
        sample_embeddings: list[dict],
        sample_graph: tuple,
        sample_catalog: list[dict],
    ) -> None:
        bundle_path = self._write_sample_bundle(
            tmp_bundle_dir, sample_chunks, sample_embeddings, sample_graph, sample_catalog
        )
        expected = (
            "manifest.json",
            "documents/chunks.jsonl",
            "embeddings/embeddings.jsonl",
            "graph/entities.json",
            "graph/relations.json",
            "catalog/catalog.json",
        )
        for rel in expected:
            assert (bundle_path / rel).exists(), rel

    def test_write_and_read_directory_contents(
        self,
        tmp_bundle_dir: Path,
        sample_chunks: list[dict],
        sample_embeddings: list[dict],
        sample_graph: tuple,
        sample_catalog: list[dict],
    ) -> None:
        bundle_path = self._write_sample_bundle(
            tmp_bundle_dir, sample_chunks, sample_embeddings, sample_graph, sample_catalog
        )
        reader = KnowledgeBundleReader(bundle_path)
        manifest = reader.read_manifest()
        assert manifest.project_id == "test-project"
        assert manifest.chunk_count == 2
        assert reader.read_chunks()[0]["chunk_id"] == "chunk-1"
        assert reader.read_embeddings()[0]["embedding"] == [0.1, 0.2, 0.3]
        entities, relations = reader.read_graph()
        assert len(entities) == 2
        assert len(relations) == 1
        assert len(reader.read_catalog()) == 1
        reader.cleanup()

    def test_read_flat_kb_archive(
        self,
        tmp_path: Path,
        sample_chunks: list[dict],
        sample_embeddings: list[dict],
    ) -> None:
        """Reader must still open a legacy flat .kb zip written by hand."""
        import zipfile

        bundle_dir = _write_flat_bundle(
            tmp_path / "work",
            chunks=sample_chunks,
            embeddings=sample_embeddings,
        )
        kb_path = tmp_path / "test-project.kb"
        with zipfile.ZipFile(kb_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in bundle_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(bundle_dir))

        assert kb_path.exists()
        assert kb_path.suffix == ".kb"

        reader = KnowledgeBundleReader(kb_path)
        read_manifest = reader.read_manifest()
        assert read_manifest.project_id == "test-project"

        read_chunks = reader.read_chunks()
        assert len(read_chunks) == 2

        reader.cleanup()

    def test_validate_bundle(
        self,
        tmp_bundle_dir: Path,
        sample_chunks: list[dict],
    ) -> None:
        _write_flat_bundle(
            tmp_bundle_dir,
            manifest=KnowledgeBundleManifest(
                project_id="test-project",
                project_name="Test Project",
                created_at=KnowledgeBundleManifest.now(),
                document_count=1,
                chunk_count=2,
            ),
            chunks=sample_chunks,
        )

        validated_manifest = validate_bundle(tmp_bundle_dir)
        assert validated_manifest.project_id == "test-project"
        assert validated_manifest.format == BUNDLE_FORMAT

    def test_reader_nonexistent_bundle(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            KnowledgeBundleReader(tmp_path / "nonexistent.kb")

    def test_reader_missing_manifest(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "incomplete.kb.d"
        bundle_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            reader = KnowledgeBundleReader(bundle_dir)
            reader.read_manifest()


class TestGtwop1AndGtwop4BundleContract:
    """r4 side of the v1.2 bundle contract (G-P2-1/G-P2-4)."""

    def _write_8_table_dir(self, tmp_path: Path, version: str = "1.2") -> Path:
        bundle = tmp_path / "server-bd"
        bundle.mkdir()
        (bundle / "nodes.jsonl").write_text(
            "".join(json.dumps({"node_id": f"n{i}", "label": "X"}) + "\n" for i in range(2)),
            encoding="utf-8",
        )
        (bundle / "edges.jsonl").write_text(json.dumps({"edge_id": "e1"}) + "\n", encoding="utf-8")
        manifest = {
            "format": "r4-kb",
            "version": version,
            "project_id": "p",
            "generator": "umrs",
            "exported_at": "2026-09-06T00:00:00+00:00",
            "unsigned": False,
            "embedding": {
                "model": "e5-base-v2",
                "dimensions": 1024,
                "distance": "cosine",
                "normalization": "unknown",
                "pending": False,
            },
            "tables": {"nodes": {"count": 2, "file": "nodes.jsonl"}},
        }
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return bundle

    def test_read_graph_falls_back_to_8_table_layout(self, tmp_path: Path) -> None:
        bundle = self._write_8_table_dir(tmp_path)
        entities, relations = KnowledgeBundleReader(bundle).read_graph()
        assert len(entities) == 2
        assert relations == [{"edge_id": "e1"}]

    def test_validate_accepts_v1_2_manifest(self, tmp_path: Path) -> None:
        bundle = self._write_8_table_dir(tmp_path)
        manifest = validate_bundle(bundle)
        assert manifest.version == "1.2"
        assert manifest.unsigned is False

    def test_validate_rejects_unknown_version(self, tmp_path: Path) -> None:
        bundle = self._write_8_table_dir(tmp_path, version="0.9")
        with pytest.raises(ValueError, match="version"):
            validate_bundle(bundle)

    def test_manifest_from_dict_maps_embedding_pin(self, tmp_path: Path) -> None:
        bundle = self._write_8_table_dir(tmp_path)
        manifest = KnowledgeBundleManifest.from_file(bundle / "manifest.json")
        assert manifest.embedding_model == "e5-base-v2"
        assert manifest.embedding_dimension == 1024
        assert manifest.embedding["pending"] is False
        assert manifest.generator == "umrs"


# ---------------------------------------------------------------------------
# The flat v1.2 / v1.3 layout umrs writes (ADR-093 C15, D-12)
# ---------------------------------------------------------------------------
#
# The rows below mirror umrs/src/umrs/knowledge/bundle_export.py byte for
# byte in shape: ``_serialize_artifact`` (+ ``canonical_identity``),
# ``_serialize_node`` over a KNOWLEDGE_EMBED chunk node
# (``jobs/knowledge_adapters._write_embedded_chunks``), ``_node_vector_row``,
# ``bundle_raw.write_raw`` and ``_build_manifest``. v1.3 (``--chunk-text``)
# adds the chunk text to each chunk node row.

_STAMP = "2026-10-10T00:00:00+00:00"
_E5_PATH = "/models/e5-base-v2"
# Chunk 0's body runs past the 500-character excerpt; the word
# "magnetotelluric" sits beyond it, so only whole chunk text can find it.
_DOC_A = (
    "Seismic phase picking on dense arrays. " * 14
    + "The magnetotelluric survey constrains the crust."
)
_DOC_B = "Pasta carbonara uses eggs, pecorino and guanciale."


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _artifact_row(artifact_id: str, raw: bytes, metadata: dict) -> dict:
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "artifact_id": artifact_id,
        "content_hash": digest,
        "kind": "document",
        "project_id": "p",
        "source_ref": f"s3://umrs-knowledge/p/{digest}",
        "size_bytes": len(raw),
        "mime_type": "text/plain",
        "status": "active",
        "metadata": metadata,
        "created_at": _STAMP,
        "updated_at": _STAMP,
        "canonical_identity": {
            "contract": "cid-1",
            "document_id": f"kdoc-{digest[:32]}",
            "identity": digest,
            "normalization": "text-v1",
        },
    }


def _chunk_node(artifact_id: str, index: int, document: str, start: int, end: int) -> dict:
    body = document[start:end]
    return {
        "node_id": f"node-chunk-{artifact_id}-{index}",
        "project_id": "p",
        "label": f"chunk {index} of {artifact_id}",
        "node_type": "entity",
        "content": {
            "kind": "chunk",
            "artifact_id": artifact_id,
            "chunk_index": index,
            "chunk_start": start,
            "chunk_end": end,
            "chunk_md5": _md5(body),
            "excerpt": body[:500],
            "origin": "knowledge_embed",
        },
        "content_hash": _md5(body),
        "source_ir": None,
        "created_at": _STAMP,
        "updated_at": _STAMP,
    }


def _other_nodes() -> list[dict]:
    """A file node and an entity node: never offline chunks."""
    return [
        {
            "node_id": "node-file-art-a",
            "project_id": "p",
            "label": "seismic.txt",
            "node_type": "file",
            "content": {"kind": "file", "artifact_id": "art-a"},
            "content_hash": "x",
            "source_ir": None,
        },
        {
            "node_id": "ent-1",
            "project_id": "p",
            "label": "Seismic phase",
            "node_type": "entity",
            "content": {"kind": "entity", "name": "Seismic phase"},
            "content_hash": "y",
            "source_ir": None,
        },
    ]


def _bundle_nodes(chunk_text: bool) -> list[dict]:
    chunks = [
        _chunk_node("art-a", 0, _DOC_A, 0, len(_DOC_A) - 20),
        _chunk_node("art-a", 1, _DOC_A, len(_DOC_A) - 20, len(_DOC_A)),
        _chunk_node("art-b", 0, _DOC_B, 0, len(_DOC_B)),
    ]
    if chunk_text:
        for row in chunks:
            content = row["content"]
            document = _DOC_A if content["artifact_id"] == "art-a" else _DOC_B
            row["text"] = document[content["chunk_start"] : content["chunk_end"]]
    return [_other_nodes()[0], *chunks, _other_nodes()[1]]


def _pin(model: str = "e5-base-v2", dims: int = 3, pending: bool = False) -> dict:
    return {
        "model": model,
        "model_path": _E5_PATH,
        "revision": "",
        "dimensions": dims,
        "distance": "cosine",
        "normalization": "unknown",
        "pending": pending,
    }


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def _write_umrs_bundle(
    root: Path,
    *,
    version: str = "1.2",
    nodes: list[dict] | None = None,
    vectors: list[dict] | None = None,
    raw: dict[str, bytes] | None = None,
    pin: dict | None = None,
) -> Path:
    """A bundle directory in the exact umrs KNOWLEDGE_EXPORT layout."""
    root.mkdir(parents=True, exist_ok=True)
    artifacts = [
        _artifact_row("art-a", _DOC_A.encode(), {"source_name": "seismic.txt"}),
        _artifact_row("art-b", _DOC_B.encode(), {"staged_from": "uploads/p/recipes.md"}),
    ]
    tables = {
        "artifacts.jsonl": artifacts,
        "documents.jsonl": [],
        "nodes.jsonl": nodes if nodes is not None else _bundle_nodes(version == "1.3"),
        "edges.jsonl": [],
        # memory_vectors rows: never chunk vectors (no node_id, "vector" key).
        "embeddings.jsonl": [{"id": "7", "vector": [9.0, 9.0, 9.0], "document_id": "d"}],
        "node_embeddings.jsonl": vectors or [],
        "catalog.jsonl": [],
        "a2l_links.jsonl": [],
        "a2l_refs.jsonl": [],
    }
    for name, rows in tables.items():
        _write_rows(root / name, rows)
    raw_block = _write_raw(root, artifacts, raw) if raw else {"included": False}
    manifest = {
        "format": "r4-kb",
        "version": version,
        "project_id": "p",
        "generator": "umrs",
        "generator_version": "0.0.0",
        "exported_at": _STAMP,
        "unsigned": True,
        "skills": [],
        "mcp_configs": [],
        "catalog": {},
        "ownership": {"contributors": [], "licence": ""},
        "collision_policy": "abort",
        "provenance_canonical": False,
        "identity": {"contract": "cid-1", "normalizations": {"text-v1": 2}},
        "reconciliation_snapshot": None,
        "embedding": pin if pin is not None else _pin(),
        "tables": {
            name.removesuffix(".jsonl"): {"count": len(rows), "file": name}
            for name, rows in tables.items()
        },
        "raw": raw_block,
    }
    if version == "1.3":
        manifest["chunk_text"] = True
    (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2), "utf-8")
    (root / "signature.sig").write_text("", "utf-8")
    return root


def _write_raw(root: Path, artifacts: list[dict], raw: dict[str, bytes]) -> dict:
    """``bundle_raw.write_raw``: raw/<sha256> objects + raw_objects.jsonl."""
    (root / "raw").mkdir()
    rows = []
    for artifact in artifacts:
        data = raw.get(artifact["artifact_id"])
        if data is None:
            continue
        digest = hashlib.sha256(data).hexdigest()
        (root / "raw" / digest).write_bytes(data)
        rows.append(
            {
                "artifact_id": artifact["artifact_id"],
                "content_hash": digest,
                "size_bytes": len(data),
                "mime_type": "text/plain",
                "file": f"raw/{digest}",
            }
        )
    (root / "raw_objects.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), "utf-8"
    )
    return {"included": True, "count": len(rows), "bytes": 0, "skipped": {}}


def _node_vector(node_id: str, vector: list[float], model: str = _E5_PATH) -> dict:
    return {
        "node_id": node_id,
        "model_name": model,
        "dimension": len(vector),
        "embedding": vector,
        "content_hash": "h",
    }


def _by_node(chunks: list[dict]) -> dict[str, dict]:
    return {row["node_id"]: row for row in chunks}


class TestFlatLayoutChunks:
    """``read_chunks`` on the v1.2/v1.3 layout: one row per chunk node."""

    def test_v12_loads_every_chunk_node_and_nothing_else(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb")
        chunks = KnowledgeBundleReader(bundle).read_chunks()
        assert sorted(_by_node(chunks)) == [
            "node-chunk-art-a-0",
            "node-chunk-art-a-1",
            "node-chunk-art-b-0",
        ]

    def test_v12_text_is_the_excerpt_marked_as_such(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb")
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-a-0"]
        assert (row["text_source"], len(row["content"])) == ("excerpt", 500)
        assert row["metadata"]["text_source"] == "excerpt"

    def test_v13_text_is_the_whole_chunk_from_the_node(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb", version="1.3")
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-a-0"]
        assert row["text_source"] == "node"
        assert row["content"] == _DOC_A[: len(_DOC_A) - 20]

    def test_raw_slice_is_used_only_when_its_md5_matches(self, tmp_path: Path) -> None:
        nodes = _bundle_nodes(chunk_text=False)
        nodes[2]["content"]["chunk_md5"] = "0" * 32  # art-a chunk 1: a stale md5
        bundle = _write_umrs_bundle(tmp_path / "kb", nodes=nodes, raw={"art-a": _DOC_A.encode()})
        rows = _by_node(KnowledgeBundleReader(bundle).read_chunks())
        verified, stale = rows["node-chunk-art-a-0"], rows["node-chunk-art-a-1"]
        assert (verified["text_source"], verified["content"]) == ("artifact", _DOC_A[:-20])
        assert stale["text_source"] == "excerpt"

    def test_flat_rows_carry_passage_fields(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb", version="1.3")
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-a-1"]
        fields = ("chunk_id", "document_id", "artifact_id", "chunk_index", "chunk_count", "span")
        assert tuple(row[k] for k in fields) == (
            "node-chunk-art-a-1",
            "art-a",
            "art-a",
            1,
            2,
            [len(_DOC_A) - 20, len(_DOC_A)],
        )

    def test_source_name_comes_from_artifacts_jsonl(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb")
        rows = _by_node(KnowledgeBundleReader(bundle).read_chunks())
        names = (
            rows["node-chunk-art-a-0"]["source_name"],
            rows["node-chunk-art-b-0"]["source_name"],
        )
        assert names == ("seismic.txt", "recipes.md")  # source_name, staged_from basename

    def test_rag_upload_chunk_finds_its_artifact_by_document_id(self, tmp_path: Path) -> None:
        node = _chunk_node("art-b", 0, _DOC_B, 0, len(_DOC_B))
        content = node["content"]
        del content["artifact_id"], content["excerpt"]
        content["document_id"] = "doc-9"
        bundle = _write_umrs_bundle(tmp_path / "kb", nodes=[node])
        artifacts = [
            _artifact_row("artifact-rag-doc-9", b"", {"document_id": "doc-9", "filename": "x.pdf"})
        ]
        _write_rows(bundle / "artifacts.jsonl", artifacts)
        row = KnowledgeBundleReader(bundle).read_chunks()[0]
        assert (row["artifact_id"], row["source_name"], row["text_source"]) == (
            "artifact-rag-doc-9",
            "x.pdf",
            "",
        )

    def test_canonized_legacy_chunk_text_counts_as_node_text(self, tmp_path: Path) -> None:
        node = _chunk_node("art-b", 0, _DOC_B, 0, len(_DOC_B))
        node["content"]["text"] = "legacy passage about guanciale"
        bundle = _write_umrs_bundle(tmp_path / "kb", nodes=[node])
        row = KnowledgeBundleReader(bundle).read_chunks()[0]
        assert (row["content"], row["text_source"]) == ("legacy passage about guanciale", "node")

    def test_raw_object_outside_the_raw_table_is_never_read(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb", raw={"art-a": _DOC_A.encode()})
        _write_rows(bundle / "raw_objects.jsonl", [])  # the object is no longer listed
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-a-0"]
        assert row["text_source"] == "excerpt"

    def test_server_archive_loads_its_chunks(self, tmp_path: Path) -> None:
        import tarfile

        bundle = _write_umrs_bundle(tmp_path / "work" / "bundle", version="1.3")
        archive = tmp_path / "job-1.kb.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(bundle, arcname="bundle")
        reader = KnowledgeBundleReader(archive)
        assert len(reader.read_chunks()) == 3
        reader.cleanup()


def _bundle_with_raw_a(root: Path, raw: bytes, document: str) -> Path:
    """A v1.2 bundle whose art-a raw object is ``raw`` and whose chunks index
    ``document`` (the text the embed job chunked from it)."""
    nodes = [_chunk_node("art-a", 0, document, 0, len(document))]
    bundle = _write_umrs_bundle(root, nodes=nodes, raw={"art-a": raw})
    artifacts = [_artifact_row("art-a", raw, {"source_name": "seismic.txt"})]
    _write_rows(bundle / "artifacts.jsonl", artifacts)
    return bundle


class TestOfflineRawDecoding:
    """The offline reader decodes a raw object as the embed job chunked it
    (umrs ``hydrate.decode_chunked``; review chunk-data-12, 2026-10-10)."""

    def test_a_text_object_holding_a_nul_still_gives_its_verified_slice(
        self, tmp_path: Path
    ) -> None:
        raw = _DOC_A.encode().replace(b"phase", b"ph\x00ase", 1)
        bundle = _bundle_with_raw_a(tmp_path / "kb", raw, _DOC_A)
        (row,) = KnowledgeBundleReader(bundle).read_chunks()
        assert (row["text_source"], row["content"]) == ("artifact", _DOC_A)

    @pytest.mark.parametrize("magic", [b"%PDF-1.7\n", b"PK\x03\x04"])
    def test_a_pdf_or_pptx_object_is_never_decoded_as_text(self, magic: bytes) -> None:
        from r4.knowledge_bundle import decode_chunked

        assert decode_chunked(magic + _DOC_B.encode()) is None
        assert decode_chunked(b"  plain\x00 text \n") == "plain text"

    def test_a_pdf_chunk_falls_back_to_its_excerpt(self, tmp_path: Path) -> None:
        raw = b"%PDF-1.7\n" + _DOC_A.encode()
        bundle = _bundle_with_raw_a(tmp_path / "kb", raw, raw.decode())
        (row,) = KnowledgeBundleReader(bundle).read_chunks()
        assert row["text_source"] == "excerpt"


class TestFlatLayoutEmbeddings:
    """``read_embeddings`` keys node_embeddings.jsonl by node id."""

    def test_rows_are_keyed_by_node_id_with_their_space(self, tmp_path: Path) -> None:
        vectors = [_node_vector("node-chunk-art-b-0", [0.0, 1.0, 0.0])]
        bundle = _write_umrs_bundle(tmp_path / "kb", vectors=vectors)
        rows = KnowledgeBundleReader(bundle).read_embeddings()
        assert [(r["node_id"], r["space"], r["embedding"]) for r in rows] == [
            ("node-chunk-art-b-0", "e5-base-v2", [0.0, 1.0, 0.0])
        ]  # the memory_vectors dump (embeddings.jsonl) is not read

    def test_chunk_vectors_join_by_node_id(self, tmp_path: Path) -> None:
        vectors = [
            _node_vector("node-chunk-art-b-0", [0.0, 1.0, 0.0]),
            _node_vector("ent-1", [1.0, 1.0, 1.0]),  # not a chunk
            _node_vector("node-chunk-art-a-0", [1.0, 0.0, 0.0]),
        ]
        bundle = _write_umrs_bundle(tmp_path / "kb", vectors=vectors)
        reader = KnowledgeBundleReader(bundle)
        ids = [row["node_id"] for row in reader.read_chunks()]
        space, by_node = reader.chunk_vectors(ids)
        assert (space, {k: list(v) for k, v in by_node.items()}) == (
            "e5-base-v2",
            {"node-chunk-art-b-0": [0.0, 1.0, 0.0], "node-chunk-art-a-0": [1.0, 0.0, 0.0]},
        )

    def test_one_space_only_the_pinned_one(self, tmp_path: Path) -> None:
        vectors = [
            _node_vector("node-chunk-art-a-0", [1.0, 0.0, 0.0]),
            _node_vector("node-chunk-art-a-1", [1.0, 0.0], model="/models/bge-small"),
            _node_vector("node-chunk-art-b-0", [0.0, 1.0], model="/models/bge-small"),
        ]
        bundle = _write_umrs_bundle(tmp_path / "kb", vectors=vectors)
        space, by_node = KnowledgeBundleReader(bundle).chunk_vectors(
            ["node-chunk-art-a-0", "node-chunk-art-a-1", "node-chunk-art-b-0"]
        )
        assert (space, sorted(by_node)) == ("e5-base-v2", ["node-chunk-art-a-0"])

    def test_pending_pin_takes_the_space_most_chunks_are_in(self, tmp_path: Path) -> None:
        vectors = [
            _node_vector("node-chunk-art-a-0", [1.0, 0.0, 0.0]),
            _node_vector("node-chunk-art-a-1", [1.0, 0.0], model="/models/bge-small"),
            _node_vector("node-chunk-art-b-0", [0.0, 1.0], model="/models/bge-small"),
        ]
        pin = _pin(model="", dims=0, pending=True)
        bundle = _write_umrs_bundle(tmp_path / "kb", vectors=vectors, pin=pin)
        space, by_node = KnowledgeBundleReader(bundle).chunk_vectors(
            ["node-chunk-art-a-0", "node-chunk-art-a-1", "node-chunk-art-b-0"]
        )
        assert (space, len(by_node)) == ("bge-small", 2)


class TestFlatLayoutValidation:
    def test_validate_accepts_v1_3(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb", version="1.3")
        manifest = validate_bundle(bundle)
        assert (manifest.version, manifest.chunk_text) == ("1.3", True)

    def test_v1_3_is_an_accepted_version(self) -> None:
        assert {"1.2", "1.3"} <= BUNDLE_ACCEPTED_VERSIONS

    def test_validate_v12_does_not_warn_on_counts_it_never_declared(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb")
        with caplog.at_level("WARNING", logger="r4.knowledge_bundle"):
            validate_bundle(bundle)
        assert "count mismatch" not in caplog.text


class TestFlatLayoutTextOrder:
    """The umrs reader's order (``bundle_reader.chunk_passage``)."""

    def _cut_text_nodes(self) -> list[dict]:
        nodes = _bundle_nodes(chunk_text=True)
        nodes[1]["text"] = nodes[1]["text"][:100]  # art-a chunk 0, cut at the cap
        nodes[1]["text_truncated"] = True
        return nodes

    def test_cut_node_text_yields_to_the_verified_raw_slice(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(
            tmp_path / "kb",
            version="1.3",
            nodes=self._cut_text_nodes(),
            raw={"art-a": _DOC_A.encode()},
        )
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-a-0"]
        assert (row["text_source"], row["content"]) == ("artifact", _DOC_A[:-20])

    def test_cut_node_text_beats_the_excerpt(self, tmp_path: Path) -> None:
        bundle = _write_umrs_bundle(tmp_path / "kb", version="1.3", nodes=self._cut_text_nodes())
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-a-0"]
        assert (row["text_source"], len(row["content"]), row["metadata"]["text_truncated"]) == (
            "node",
            100,
            True,
        )

    def test_c9_header_rides_the_row(self, tmp_path: Path) -> None:
        nodes = _bundle_nodes(chunk_text=True)
        nodes[3]["text_header"] = "Recipes › Carbonara\n"
        nodes[3]["content"]["header_chars"] = len("Recipes › Carbonara\n")
        bundle = _write_umrs_bundle(tmp_path / "kb", version="1.3", nodes=nodes)
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-b-0"]
        assert (row["header"], row["content"]) == ("Recipes › Carbonara\n", _DOC_B)

    def test_a_c9_header_without_text_header_comes_from_the_node(self, tmp_path: Path) -> None:
        """A v1.2 bundle (or a v1.3 chunk exported through the artifact slice)
        carries no ``text_header``; the node's own ``content.header`` is
        searched with the body, as the server's ``search_text`` is."""
        nodes = _bundle_nodes(chunk_text=False)
        nodes[3]["content"]["header"] = "Recipes › Carbonara"
        nodes[3]["content"]["header_chars"] = len("Recipes › Carbonara\n")
        bundle = _write_umrs_bundle(tmp_path / "kb", nodes=nodes)
        row = _by_node(KnowledgeBundleReader(bundle).read_chunks())["node-chunk-art-b-0"]
        assert row["header"] == "Recipes › Carbonara"
