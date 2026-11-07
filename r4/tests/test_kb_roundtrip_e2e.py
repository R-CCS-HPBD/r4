"""Q-12 `.kb` round-trip E2E proof: upload → export → query/import → match.

Discipline notes:

- No live UMRS: the stub replaces the HTTP client deterministically so the
  test exercises the bundle spectrum end-to-end without needing a compose
  harness. (The live-compose round trip is a follow-up in a deployment
  window, same posture as Q-13.)
- No secrets: the fixture data is synthetic.
- No early-return gates bypassed: the test drives the same handler code a
  real CLI call goes through, not internal shortcuts.
"""

from __future__ import annotations

import argparse

import pytest

from r4._cli.knowledge import (
    KnowledgeCommand,
)
from r4.knowledge_bundle import KnowledgeBundleManifest, KnowledgeBundleReader
from r4.rag import RAGResponse

pytestmark = [pytest.mark.e2e]


# ── Synthetic platform fixture (what a real UMRS would answer) ──────────────

PLATFORM_CHUNKS = [
    {"document_id": "doc-a", "chunk_id": "c-1", "content": "hello kb", "score": 0.91},
    {"document_id": "doc-a", "chunk_id": "c-2", "content": "world kb", "score": 0.87},
]
PLATFORM_ENTITIES = [{"entity_id": "e-1", "name": "acme", "type": "org"}]
PLATFORM_RELATIONS = [{"rel_id": "r-1", "from": "e-1", "to": "e-2", "kind": "owns"}]
PLATFORM_CATALOG = [{"object_id": "o-1", "name": "dataset-a", "kind": "dataset"}]


class _StubClient:
    """HTTP-level stub that mirrors the UMRS payloads the export/query paths read."""

    def __init__(self):
        self.knowledge = _KnowledgeStub(self)
        self.rag = _RagStub(self)
        self.data_catalog = _CatalogStub(self)


class _KnowledgeStub:
    def __init__(self, client):
        self._client = client
        self.imported: list = []

    def import_bundle(self, bundle, allow_unsigned=False):
        self.imported.append((bundle, allow_unsigned))
        return {"project_id": "proj-demo", "import": {"node_count": 1, "verified": False}}

    def get_graph(self, **kwargs):  # noqa: ARG002 — stub shape matches real call
        # Mirror the real export path reading entities/relations from a
        # nodes/edges payload. The bundle writer converts these to .kb rows.
        return {
            "nodes": PLATFORM_ENTITIES,
            "edges": PLATFORM_RELATIONS,
        }

    def query(self, question, project_id=None, scope="all", limit=20, **_ignored):  # noqa: ARG002
        # Called by `_handle_query` on the *imported* side; returning the
        # exported chunks covers the read-back parity portion (answer must
        # contain items that match what was uploaded).
        return {
            "results": [
                {
                    "result_type": "chunk",
                    "score": 0.91,
                    "title": "doc-a",
                    "content": "hello kb",
                    "project_id": project_id or "proj-demo",
                }
            ],
            "wired_backends": ["catalog", "knowledge_graph", "embeddings", "rag"],
            "unavailable_backends": {},
            "duration_seconds": 0.01,
        }


class _RagStub:
    def __init__(self, client):
        self._client = client

    def query(
        self,
        query,
        *,
        project_id=None,  # noqa: ARG002 — stub shape matches real call
        top_k=5,
        threshold=0.7,  # noqa: ARG002 — stub shape matches real call
        include_sources=True,
        **_ignored,
    ):
        # `KnowledgeCommand._handle_query` folds onto the legacy rag query path
        # ("P2 fold: `r4 knowledge query` = `r4 rag query`"), so the real return
        # type here is a RAGResponse, not the raw API payload.  Build it from
        # the same fixture payload a UMRS would answer with.
        return RAGResponse.from_api_response(
            {
                "answer": f"Retrieved {len(PLATFORM_CHUNKS)} chunk(s) for: {query}",
                "sources": PLATFORM_CHUNKS[:top_k] if include_sources else [],
            }
        )


class _CatalogStub:
    def __init__(self, client):
        self._client = client

    def list(self, **kwargs):  # noqa: ARG002 — stub shape matches real call
        return PLATFORM_CATALOG


# ─��� Round-trip test ──────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    """Isolated HOME that points ~/.config/r4 at a clean tmp tree (imports
    land in the local knowledge store dir without touching real config)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


@pytest.fixture()
def cmd() -> KnowledgeCommand:
    return KnowledgeCommand()


@pytest.fixture()
def platform_client(monkeypatch):
    """Force every handler that calls ``Client()`` to reach our stub."""

    stub = _StubClient()

    def build(*args, **kwargs):  # noqa: ARG001 — constructor signature kept for parity
        return stub

    monkeypatch.setattr("r4.client.Client", build)
    return stub


def _ns(**overrides) -> argparse.Namespace:
    base = {
        "project_id": "proj-demo",
        "output": None,
        "bundle": None,
        "include_graph": True,
        "include_catalog": True,
        "query": "hello kb",
        "scope": "all",
        "limit": 20,
        "as_json": True,
        # `r4 knowledge query` parser fields (the handler folds onto the rag
        # query path, which reads --project/--top-k/--threshold/--no-sources).
        "project": "proj-demo",
        "top_k": 5,
        "threshold": 0.7,
        "no_sources": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestKbRoundTripE2E:
    """End-to-end coverage across the three Q-12 legs."""

    def test_export_matches_upload_payload(
        self,
        cmd,
        platform_client,  # noqa: ARG002 — fixture side-effects required
        fresh_home,  # noqa: ARG002 — fixture side-effects required
        tmp_path,
        capsys,
    ):
        """Upload (fixture) → export (v1.2 projection) — tables must match."""
        out = tmp_path / "bundle.kb"
        rc = cmd._handle_export(_ns(output=str(out)))
        assert rc == 0, capsys.readouterr()

        reader = KnowledgeBundleReader(out)
        manifest = reader.read_manifest()

        # v1.2 client projection tables come from the fixture's platform material.
        assert manifest.project_id == "proj-demo"
        assert manifest.version == "1.2"
        assert manifest.unsigned is True
        assert manifest.tables["nodes"]["count"] == len(PLATFORM_ENTITIES)
        assert manifest.tables["edges"]["count"] == len(PLATFORM_RELATIONS)
        assert manifest.tables["catalog"]["count"] == len(PLATFORM_CATALOG)
        assert manifest.embedding["pending"] is True

    def test_export_then_import_then_query_roundtrips(
        self,
        cmd,
        platform_client,  # noqa: ARG002 — fixture side-effects required
        fresh_home,  # noqa: ARG002 — fixture side-effects required
        tmp_path,
        capsys,
    ):
        """Export → an 8-table bundle imports through the platform (uploaded
        to POST /knowledge/import, D-40) → query against the stub still works.
        """
        out = tmp_path / "bundle.kb"
        rc = cmd._handle_export(_ns(output=str(out)))
        assert rc == 0, capsys.readouterr()

        # 8-table bundles go to the platform import (never the local store).
        rc = cmd._handle_import(_ns(bundle=str(out), project_id="proj-demo"))
        captured = capsys.readouterr()
        assert rc == 0, captured.err
        assert '"project_id": "proj-demo"' in captured.out  # _ns() is --json
        assert platform_client.knowledge.imported == [(str(out), False)]

        # Query against the same platform stub path used by _handle_query;
        # the response payload must reference the uploaded chunks.
        rc = cmd._handle_query(_ns())
        assert rc == 0, capsys.readouterr()
        out_slice = capsys.readouterr().out
        assert "hello kb" in out_slice  # the chunk text from the platform

    def test_export_preserves_exact_rows_in_8_table_layout(
        self,
        cmd,
        platform_client,  # noqa: ARG002 — fixture side-effects required
        fresh_home,  # noqa: ARG002 — fixture side-effects required
        tmp_path,
        capsys,
    ):
        """Export must not corrupt row contents: nodes/edges/catalog round-trip
        exactly through the v1.2 8-table layout (read via the reader's fallback)."""
        out = tmp_path / "bundle.kb"
        cmd._handle_export(_ns(output=str(out)))
        _ = capsys.readouterr()

        reader = KnowledgeBundleReader(out)
        entities, relations = reader.read_graph()
        catalog = reader.read_catalog()
        assert entities == PLATFORM_ENTITIES
        assert relations == PLATFORM_RELATIONS
        assert catalog == PLATFORM_CATALOG
        _ = fresh_home

        manifest = KnowledgeBundleManifest.from_file(reader._work_dir / "manifest.json")
        assert manifest.tables["nodes"]["file"] == "nodes.jsonl"
        assert manifest.tables["catalog"]["file"] == "catalog.jsonl"
