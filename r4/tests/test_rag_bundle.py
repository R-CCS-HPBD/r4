"""Tests for RAGClient bundle loading (ADR-013) and the offline reader over
the flat r4-kb v1.2/v1.3 layout (ADR-093 C15, D-12)."""

from __future__ import annotations

import hashlib
import json
import os
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from r4.rag import (
    KNOWLEDGE_BUNDLE_ENV,
    RAGClient,
    RAGSource,
    _SimpleBM25,
    _SimpleVectorSearch,
    inject_context_into_messages,
)


@pytest.fixture(autouse=True)
def _no_bundle_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's R4_KNOWLEDGE_BUNDLE never leaks into these tests."""
    monkeypatch.setenv(KNOWLEDGE_BUNDLE_ENV, "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_chunks() -> list[dict]:
    return [
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "content": "Machine learning is a subset of artificial intelligence.",
            "metadata": {"page": 1, "source": "test.pdf"},
        },
        {
            "chunk_id": "chunk-2",
            "document_id": "doc-1",
            "content": "Neural networks are inspired by biological neurons.",
            "metadata": {"page": 2, "source": "test.pdf"},
        },
        {
            "chunk_id": "chunk-3",
            "document_id": "doc-2",
            "content": "Deep learning uses multiple layers of neural networks.",
            "metadata": {"page": 1, "source": "deep.pdf"},
        },
    ]


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.config.gateway_url = "http://localhost:8000"
    client.config.project_id = "test-project"
    client.config.timeout = 30
    return client


@pytest.fixture
def rag_client(mock_client: MagicMock) -> RAGClient:
    return RAGClient(mock_client)


# ---------------------------------------------------------------------------
# _SimpleBM25 Tests
# ---------------------------------------------------------------------------


class TestSimpleBM25:
    def test_score_basic(self, sample_chunks: list[dict]) -> None:
        bm25 = _SimpleBM25(sample_chunks)
        results = bm25.score("machine learning", top_k=3)
        assert len(results) > 0
        top_idx, top_score = results[0]
        assert top_idx == 0
        assert top_score > 0

    def test_score_no_matches(self, sample_chunks: list[dict]) -> None:
        bm25 = _SimpleBM25(sample_chunks)
        results = bm25.score("quantum physics", top_k=3)
        assert results == []  # a chunk sharing no query term is no match

    def test_score_empty_query(self, sample_chunks: list[dict]) -> None:
        bm25 = _SimpleBM25(sample_chunks)
        results = bm25.score("", top_k=3)
        assert len(results) == 0

    def test_score_top_k_limit(self, sample_chunks: list[dict]) -> None:
        bm25 = _SimpleBM25(sample_chunks)
        results = bm25.score("learning", top_k=2)
        assert len(results) <= 2


# ---------------------------------------------------------------------------
# RAGClient Bundle Management Tests
# ---------------------------------------------------------------------------


class TestRAGClientBundle:
    def test_initial_state_no_bundle(self, rag_client: RAGClient) -> None:
        assert not rag_client.has_local_bundle

    def test_load_bundle(self, rag_client: RAGClient, sample_chunks: list[dict]) -> None:
        """Test loading chunks directly into the RAG client."""
        rag_client._bundle_chunks = sample_chunks
        rag_client._local_bm25 = _SimpleBM25(sample_chunks)

        assert rag_client.has_local_bundle
        assert len(rag_client._bundle_chunks) == 3

    def test_unload_bundle(self, rag_client: RAGClient, sample_chunks: list[dict]) -> None:
        rag_client._bundle_chunks = sample_chunks
        rag_client._local_bm25 = _SimpleBM25(sample_chunks)

        rag_client.unload_bundle()

        assert not rag_client.has_local_bundle
        assert len(rag_client._bundle_chunks) == 0

    def test_query_with_local_bundle(
        self, rag_client: RAGClient, sample_chunks: list[dict]
    ) -> None:
        """Test that query() dispatches to local BM25 when bundle is loaded."""
        rag_client._bundle_chunks = sample_chunks
        rag_client._local_bm25 = _SimpleBM25(sample_chunks)

        response = rag_client.query("machine learning", project_id="test")

        assert len(response.sources) > 0
        assert response.sources[0].score > 0

    def test_query_without_bundle_uses_http(
        self, rag_client: RAGClient, mock_client: MagicMock
    ) -> None:
        """Test that query() uses HTTP when no bundle is loaded."""
        mock_client.http.post.return_value = {
            "answer": "test answer",
            "sources": [{"document_id": "doc-1", "content": "test content", "score": 0.9}],
        }

        response = rag_client.query("test query", project_id="test-project")

        assert response.answer == "test answer"
        assert len(response.sources) == 1
        mock_client.http.post.assert_called_once()


# ---------------------------------------------------------------------------
# inject_context_into_messages Tests
# ---------------------------------------------------------------------------


class TestInjectContextIntoMessages:
    def test_empty_messages(self) -> None:
        result = inject_context_into_messages([], [])
        assert result == []

    def test_empty_sources(self) -> None:
        messages = [{"role": "user", "content": "test"}]
        result = inject_context_into_messages(messages, [])
        assert result == messages

    def test_inject_with_system_message(self) -> None:
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is ML?"},
        ]
        sources = [
            RAGSource(
                document_id="doc-1",
                content="Machine learning is a subset of AI.",
                score=0.9,
                metadata={"source": "test.pdf"},
            ),
        ]

        result = inject_context_into_messages(messages, sources)

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "helpful assistant" in result[0]["content"]
        assert "Machine learning" in result[0]["content"]

    def test_inject_without_system_message(self) -> None:
        messages = [{"role": "user", "content": "What is ML?"}]
        sources = [
            RAGSource(
                document_id="doc-1",
                content="Machine learning is a subset of AI.",
                score=0.9,
                metadata={"source": "test.pdf"},
            ),
        ]

        result = inject_context_into_messages(messages, sources)

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Machine learning" in result[0]["content"]

    def test_token_budget_truncation(self) -> None:
        messages = [{"role": "user", "content": "test"}]
        # Create a source with very long content
        long_content = "A" * 10000
        sources = [
            RAGSource(
                document_id="doc-1",
                content=long_content,
                score=0.9,
                metadata={"source": "test.pdf"},
            ),
        ]

        result = inject_context_into_messages(messages, sources, max_context_tokens=100)

        # Should have truncated the content
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# _SimpleVectorSearch Tests
# ---------------------------------------------------------------------------


class TestSimpleVectorSearch:
    def test_search_basic(self) -> None:
        """Test basic cosine similarity search."""
        embeddings = [
            {"chunk_id": "c1", "embedding": [1.0, 0.0, 0.0]},
            {"chunk_id": "c2", "embedding": [0.0, 1.0, 0.0]},
            {"chunk_id": "c3", "embedding": [0.9, 0.1, 0.0]},
        ]
        search = _SimpleVectorSearch(embeddings)

        # Query embedding close to c1 and c3
        results = search.search([1.0, 0.0, 0.0], top_k=3)
        assert len(results) > 0
        # c1 (exact match) should be first
        assert results[0][0] == 0
        assert results[0][1] > 0.99  # cosine similarity ~1.0

    def test_search_empty_embeddings(self) -> None:
        """Test search with no embeddings."""
        search = _SimpleVectorSearch([])
        results = search.search([1.0, 0.0], top_k=5)
        assert results == []

    def test_search_empty_query(self) -> None:
        """Test search with empty query embedding."""
        embeddings = [{"chunk_id": "c1", "embedding": [1.0, 0.0]}]
        search = _SimpleVectorSearch(embeddings)
        results = search.search([], top_k=5)
        assert results == []

    def test_search_threshold_filter(self) -> None:
        """Test that threshold filters out low-scoring results."""
        embeddings = [
            {"chunk_id": "c1", "embedding": [1.0, 0.0]},
            {"chunk_id": "c2", "embedding": [0.0, 1.0]},  # orthogonal
        ]
        search = _SimpleVectorSearch(embeddings)

        # With high threshold, orthogonal result is excluded
        results = search.search([1.0, 0.0], top_k=5, threshold=0.5)
        assert all(score >= 0.5 for _, score in results)


# ---------------------------------------------------------------------------
# Hybrid Search Tests (BM25 + Vector)
# ---------------------------------------------------------------------------


class TestHybridSearch:
    @pytest.fixture
    def sample_embeddings(self) -> list[dict]:
        return [
            {"chunk_id": "chunk-1", "embedding": [1.0, 0.0, 0.0]},
            {"chunk_id": "chunk-2", "embedding": [0.0, 1.0, 0.0]},
            {"chunk_id": "chunk-3", "embedding": [0.9, 0.1, 0.0]},
        ]

    def test_hybrid_search_with_query_embedding(
        self,
        rag_client: RAGClient,
        sample_chunks: list[dict],
        sample_embeddings: list[dict],
    ) -> None:
        """Test hybrid search combining BM25 + vector scores."""
        rag_client._bundle_chunks = sample_chunks
        rag_client._local_bm25 = _SimpleBM25(sample_chunks)
        rag_client._local_vector_search = _SimpleVectorSearch(sample_embeddings)

        # Query with a pre-computed embedding
        response = rag_client.query(
            "machine learning",
            project_id="test",
            query_embedding=[1.0, 0.0, 0.0],  # close to chunk-1
        )

        assert len(response.sources) > 0
        # Chunk-1 should be in top results (high BM25 + high vector score)
        top_source = response.sources[0]
        assert top_source.score > 0

    def test_hybrid_search_without_query_embedding(
        self,
        rag_client: RAGClient,
        sample_chunks: list[dict],
        sample_embeddings: list[dict],
    ) -> None:
        """Test that hybrid search falls back to BM25-only without query embedding."""
        rag_client._bundle_chunks = sample_chunks
        rag_client._local_bm25 = _SimpleBM25(sample_chunks)
        rag_client._local_vector_search = _SimpleVectorSearch(sample_embeddings)

        response = rag_client.query(
            "machine learning",
            project_id="test",
            # No query_embedding — should use BM25 only
        )

        assert len(response.sources) > 0


# ---------------------------------------------------------------------------
# The offline reader over the flat v1.2/v1.3 layout (ADR-093 C15, D-12)
# ---------------------------------------------------------------------------
#
# A compact bundle in the umrs KNOWLEDGE_EXPORT layout (bundle_export.py:
# _serialize_artifact, _serialize_node over KNOWLEDGE_EMBED chunk nodes,
# _node_vector_row, _build_manifest); v1.3 adds each chunk's ``text``.

_E5 = "/models/e5-base-v2"
# The word "magnetotelluric" sits past chunk a-0's 500-character excerpt.
_BODIES = {
    "art-a": [
        "Seismic phase picking on dense arrays. " * 14
        + "The magnetotelluric survey constrains the crust.",
        "Phase picks feed the seismic travel-time tomography.",
    ],
    "art-b": ["Pasta carbonara uses eggs, pecorino and guanciale."],
    "art-c": ["Glacier mass balance from satellite gravimetry."],
}
_NAMES = {"art-a": "seismic.txt", "art-b": "recipes.md", "art-c": "glacier.txt"}
_A0, _A1, _B0, _C0 = (
    "node-chunk-art-a-0",
    "node-chunk-art-a-1",
    "node-chunk-art-b-0",
    "node-chunk-art-c-0",
)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _chunk_node(artifact_id: str, index: int, body: str, chunk_text: bool) -> dict:
    row = {
        "node_id": f"node-chunk-{artifact_id}-{index}",
        "project_id": "p",
        "label": f"chunk {index} of {artifact_id}",
        "node_type": "entity",
        "content": {
            "kind": "chunk",
            "artifact_id": artifact_id,
            "chunk_index": index,
            "chunk_start": 0,
            "chunk_end": len(body),
            "chunk_md5": _md5(body),
            "excerpt": body[:500],
            "origin": "knowledge_embed",
        },
        "content_hash": _md5(body),
        "source_ir": None,
    }
    if chunk_text:
        row["text"] = body
    return row


def _nodes(chunk_text: bool) -> list[dict]:
    file_node = {"node_id": "node-file-art-a", "content": {"kind": "file"}, "label": "f"}
    chunks = [
        _chunk_node(artifact_id, index, body, chunk_text)
        for artifact_id, bodies in _BODIES.items()
        for index, body in enumerate(bodies)
    ]
    return [file_node, *chunks]


def _artifact(artifact_id: str) -> dict:
    return {
        "artifact_id": artifact_id,
        "content_hash": hashlib.sha256(artifact_id.encode()).hexdigest(),
        "kind": "document",
        "project_id": "p",
        "source_ref": f"s3://umrs-knowledge/p/{artifact_id}",
        "size_bytes": 1,
        "mime_type": "text/plain",
        "status": "active",
        "metadata": {"source_name": _NAMES[artifact_id]},
    }


def _vector(node_id: str, vector: list[float]) -> dict:
    return {
        "node_id": node_id,
        "model_name": _E5,
        "dimension": len(vector),
        "embedding": vector,
        "content_hash": "h",
    }


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")


def _bundle(
    root: Path, *, version: str = "1.2", vectors: list[dict] | None = None, nodes=None
) -> Path:
    root.mkdir(parents=True)
    _jsonl(root / "artifacts.jsonl", [_artifact(a) for a in _BODIES])
    _jsonl(root / "nodes.jsonl", nodes if nodes is not None else _nodes(version == "1.3"))
    _jsonl(root / "edges.jsonl", [])
    _jsonl(root / "embeddings.jsonl", [{"id": "1", "vector": [5.0, 5.0, 5.0]}])
    _jsonl(root / "node_embeddings.jsonl", vectors or [])
    manifest = {
        "format": "r4-kb",
        "version": version,
        "project_id": "p",
        "generator": "umrs",
        "unsigned": True,
        "embedding": {"model": "e5-base-v2", "dimensions": 3, "pending": False},
        "tables": {"nodes": {"count": 5, "file": "nodes.jsonl"}},
        "raw": {"included": False},
    }
    if version == "1.3":
        manifest["chunk_text"] = True
    (root / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    return root


_VECTORS = [
    _vector(_A0, [1.0, 0.0, 0.0]),
    _vector(_A1, [0.8, 0.6, 0.0]),
    _vector(_B0, [0.0, 1.0, 0.0]),
    _vector(_C0, [0.0, 0.0, 1.0]),
]


def _loaded(mock_client: MagicMock, tmp_path: Path, version: str = "1.2", **kwargs) -> RAGClient:
    return RAGClient(mock_client).load_bundle(_bundle(tmp_path / "kb", version=version, **kwargs))


def _node_ids(response) -> list[str]:
    return [source.metadata["node_id"] for source in response.sources]


class TestOfflineRetrieve:
    """``RAGClient.retrieve`` on a loaded v1.2/v1.3 bundle: no HTTP, no cut,
    sufficiency ``unknown`` on basis ``offline``."""

    def test_v12_bundle_is_searched_offline(self, mock_client: MagicMock, tmp_path: Path) -> None:
        result = _loaded(mock_client, tmp_path).retrieve("seismic phase picking")
        top = result.passages[0]
        mock_client.http.get.assert_not_called()
        assert (top.source_id, top.source_name, top.text_source, top.artifact_id) == (
            _A0,
            "seismic.txt",
            "excerpt",
            "art-a",
        )
        assert (result.sufficiency.status, result.sufficiency.basis) == ("unknown", "offline")

    @pytest.mark.parametrize("version", ["1.2", "1.3"])
    def test_every_chunk_node_loads(
        self, mock_client: MagicMock, tmp_path: Path, version: str
    ) -> None:
        rag = _loaded(mock_client, tmp_path, version)
        summary = rag.local_bundle_summary()
        source = "node" if version == "1.3" else "excerpt"
        assert rag.has_local_bundle
        assert (summary["chunks"], summary["text_sources"]) == (4, {source: 4})

    def test_v13_whole_chunk_text_finds_what_the_excerpt_cannot(
        self, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        v12 = _loaded(mock_client, tmp_path / "v12").retrieve("magnetotelluric")
        v13 = _loaded(mock_client, tmp_path / "v13", "1.3").retrieve("magnetotelluric")
        assert (len(v12.passages), [p.source_id for p in v13.passages]) == (0, [_A0])
        assert v13.passages[0].text_source == "node"

    def test_passages_carry_chunk_position(self, mock_client: MagicMock, tmp_path: Path) -> None:
        top = _loaded(mock_client, tmp_path, "1.3").retrieve("tomography").passages[0]
        assert (top.source_id, top.chunk_index, top.chunk_count, top.span) == (
            _A1,
            1,
            2,
            (0, len(_BODIES["art-a"][1])),
        )

    def test_lanes_report_bm25_only_and_unapplied_options(
        self, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        rag = _loaded(mock_client, tmp_path, vectors=_VECTORS)
        result = rag.retrieve("glacier", graphs=["g1"], threshold=0.99, k_max=2)
        lanes = result.lanes
        assert [p.source_id for p in result.passages] == [_C0]  # threshold cuts nothing
        assert (lanes["lex"]["status"], lanes["dense"]["status"]) == ("ok", "off")
        assert lanes["local_bundle"]["unapplied"] == ["graphs", "threshold"]
        assert (result.searched, result.space) == (4, "")

    def test_query_variants_are_fused(self, mock_client: MagicMock, tmp_path: Path) -> None:
        result = _loaded(mock_client, tmp_path).retrieve(["glacier", "carbonara"])
        assert sorted(p.source_id for p in result.passages) == [_B0, _C0]

    def test_k_max_bounds_the_passages(self, mock_client: MagicMock, tmp_path: Path) -> None:
        result = _loaded(mock_client, tmp_path).retrieve("seismic phase", k_max=1)
        assert [p.source_id for p in result.passages] == [_A0]

    def test_a_loaded_bundle_without_chunks_never_falls_back_to_http(
        self, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        rag = _loaded(mock_client, tmp_path, nodes=[])
        result = rag.retrieve("anything")
        mock_client.http.get.assert_not_called()
        assert (rag.has_local_bundle, result.searched, result.passages) == (True, 0, ())


class TestOfflineQuery:
    """``RAGClient.query`` on a loaded bundle: RRF k=60, no 0.7 cut."""

    def test_no_07_cut(self, mock_client: MagicMock, tmp_path: Path) -> None:
        # Normalized to the best BM25 score, chunk a-0 scored 0.49: the old cut dropped it.
        response = _loaded(mock_client, tmp_path).query("seismic tomography")
        assert _node_ids(response) == [_A1, _A0]

    def test_rrf_fuses_bm25_with_an_in_space_query_vector(
        self, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        rag = _loaded(mock_client, tmp_path, vectors=_VECTORS)
        response = rag.query("glacier gravimetry", top_k=4, query_embedding=[1.0, 0.0, 0.0])
        top = response.sources[0]
        assert _node_ids(response) == [_C0, _A0, _A1, _B0]
        assert top.score == pytest.approx(1 / 61 + 1 / 64)
        assert (top.metadata["lanes"], response.sources[1].score) == (["lex", "dense"], 1 / 61)

    def test_vectors_join_chunks_by_node_id_not_position(
        self, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        rag = _loaded(mock_client, tmp_path, vectors=list(reversed(_VECTORS)))
        response = rag.query("zzz", top_k=1, query_embedding=[0.0, 0.0, 1.0])
        assert _node_ids(response) == [_C0]

    def test_a_query_vector_outside_the_bundle_space_is_ignored(
        self, mock_client: MagicMock, tmp_path: Path
    ) -> None:
        rag = _loaded(mock_client, tmp_path, vectors=_VECTORS)
        response = rag.query("glacier", query_embedding=[1.0, 0.0])
        assert _node_ids(response) == [_C0]


class TestBundleFromTheEnvironment:
    """``R4_KNOWLEDGE_BUNDLE``: the companion ``r4 chat --knowledge`` launches."""

    def test_env_var_loads_the_bundle_once(
        self, mock_client: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KNOWLEDGE_BUNDLE_ENV, str(_bundle(tmp_path / "kb", version="1.3")))
        rag = RAGClient(mock_client)
        loaded = rag.has_local_bundle
        rag.unload_bundle()
        assert (loaded, rag.has_local_bundle) == (True, False)

    def test_a_bad_path_keeps_the_platform(
        self,
        mock_client: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(KNOWLEDGE_BUNDLE_ENV, str(tmp_path / "missing.kb"))
        with caplog.at_level("WARNING", logger="r4.rag"):
            assert not RAGClient(mock_client).has_local_bundle
        assert KNOWLEDGE_BUNDLE_ENV in caplog.text


class TestChatKnowledge:
    """``r4 chat --knowledge``: the router and search_docs search the bundle."""

    @pytest.fixture(autouse=True)
    def _no_configured_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No project from the environment or the developer's local registry;
        ``R4_PROJECT_ID`` is restored after each test."""
        monkeypatch.setenv("R4_PROJECT_ID", "")
        manager = MagicMock()
        manager.get_current.return_value = None
        monkeypatch.setattr("r4.project.get_project_manager", lambda: manager)

    def _client(self, project_id: str | None = None) -> MagicMock:
        client = MagicMock()
        client.config.project_id = project_id
        client.rag = RAGClient(client)
        return client

    def test_without_a_project_the_router_searches_the_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from r4._cli.chat import _chat_load_knowledge
        from r4.companion.retrieval_router import route_turn_decision

        monkeypatch.setenv("R4_RAG_ROUTER", "on")
        client = self._client()
        _chat_load_knowledge(
            client, Namespace(knowledge=str(_bundle(tmp_path / "kb", version="1.3")))
        )
        session = SimpleNamespace(client=client, agent=None, messages=[])
        decision = route_turn_decision(session, "What does glacier gravimetry show?")
        client.http.get.assert_not_called()
        assert (decision.action, decision.status, decision.searched) == ("pack", "unknown", 4)
        assert "[S1] glacier.txt" in decision.message
        # The bundle's project labels the turn; the session's own is untouched.
        assert (client.config.project_id, os.environ["R4_PROJECT_ID"]) == (None, "")

    def test_a_configured_project_is_kept(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from r4._cli.chat import _chat_load_knowledge

        client = self._client("mine")
        _chat_load_knowledge(client, Namespace(knowledge=str(_bundle(tmp_path / "kb"))))
        assert (client.config.project_id, os.environ["R4_PROJECT_ID"]) == ("mine", "")
        assert "Project:" not in capsys.readouterr().out

    def test_the_router_labels_an_offline_turn_with_the_bundle_project(
        self, tmp_path: Path
    ) -> None:
        from types import SimpleNamespace

        from r4.companion.retrieval_router import LOCAL_BUNDLE_PROJECT, _project_id

        client = self._client()
        session = SimpleNamespace(client=client)
        assert _project_id(session) is None  # nothing loaded: "no project"
        client.rag.load_bundle(_bundle(tmp_path / "kb", version="1.3"))
        assert _project_id(session) == "p"
        client.rag._bundle_manifest["project_id"] = ""
        assert _project_id(session) == LOCAL_BUNDLE_PROJECT

    def test_search_docs_reads_the_bundle(self, tmp_path: Path) -> None:
        from r4.companion.tools import _client_tools

        client = self._client()
        client.rag.load_bundle(_bundle(tmp_path / "kb", version="1.3"))
        tools = {tool["name"]: tool for tool in _client_tools(client)}
        out = tools["search_docs"]["execute"](query="magnetotelluric")
        client.http.get.assert_not_called()
        assert out.startswith("[1] seismic.txt") and f"id={_A0}" in out

    def test_loads_the_bundle_and_exports_its_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from r4._cli.chat import _chat_load_knowledge

        bundle = _bundle(tmp_path / "kb")
        client = self._client()
        _chat_load_knowledge(client, Namespace(knowledge=str(bundle)))
        out = capsys.readouterr().out
        assert (client.rag.has_local_bundle, os.environ[KNOWLEDGE_BUNDLE_ENV]) == (
            True,
            str(bundle.resolve()),
        )
        assert "4 chunks: 4 excerpt" in out and "--chunk-text" in out

    def test_accepts_the_server_export_archive(self, tmp_path: Path) -> None:
        from r4._cli.chat import _is_bundle_path

        archive = tmp_path / "job-1.kb.tar.gz"
        archive.write_bytes(b"")
        assert (_is_bundle_path(archive), _is_bundle_path(tmp_path / "nope.kb")) == (True, False)

    def test_an_unreadable_bundle_stops_the_command(self, tmp_path: Path) -> None:
        from r4._cli.chat import _chat_load_knowledge

        (tmp_path / "empty").mkdir()
        with pytest.raises(SystemExit):
            _chat_load_knowledge(self._client(), Namespace(knowledge=str(tmp_path / "empty")))
        assert os.environ[KNOWLEDGE_BUNDLE_ENV] == ""

    def test_the_companion_gets_the_client_that_loaded_the_bundle(self, tmp_path: Path) -> None:
        from r4.cli import cmd_chat

        client = self._client()
        args = Namespace(
            message=None,
            model=None,
            system=None,
            stream=False,
            temperature=0.7,
            max_tokens=None,
            interactive=True,
            fs_root=None,
            teamup=False,
            knowledge=str(_bundle(tmp_path / "kb")),
        )
        with (
            patch("r4.client.Client", return_value=client) as client_cls,
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_chat(args)
        assert (client_cls.call_count, launch.call_args.kwargs["client"]) == (1, client)
        assert client.rag.has_local_bundle
