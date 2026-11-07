"""D-45 pins: generated output carries the claim → evidence → artifact map."""

from __future__ import annotations

from r4.research_loop.publish import generate_paper_draft, generate_report
from r4.research_loop.state import EvidenceRecord, ResearchLoopState


def _state(with_source: bool = True) -> ResearchLoopState:
    state = ResearchLoopState(
        loop_id="d45", objective="Confirm the defect class", domain="materials"
    )
    state.add_hypothesis("Vacancy clusters drive the new defect class")
    hyp = next(iter(state.hypotheses.values()))
    ev = EvidenceRecord(
        id="ev-1",
        experiment_id="exp-1",
        hypothesis_id=hyp.id,
        supports=True,
        strength=0.8,
        summary="seg-recall rose 12 p.p.",
        source_artifact="a2l-art-77" if with_source else None,
    )
    state.evidence_pool[ev.id] = ev
    return state


def test_report_carries_source_artifact() -> None:
    text = generate_report(_state())
    assert "a2l-art-77" in text
    assert "**Source:**" in text


def test_paper_draft_table_has_source_column() -> None:
    text = generate_paper_draft(_state())
    assert "| Evidence | Direction | Strength | Source | Summary |" in text
    assert "a2l-art-77" in text


def test_absent_source_renders_honest_placeholder() -> None:
    text = generate_paper_draft(_state(with_source=False))
    assert "| ev-1 | Supports | 0.80 | — |" in text


# ── D-45 tail: Publication construction ───────────────────────────────


def test_build_publication_constructs_record_with_provenance():
    from r4.research_loop.publish import build_publication

    state = _state()
    pub = build_publication(state)
    assert pub["id"] == "pub-d45"
    assert pub["status"] == "draft"
    assert pub["provenance"]["nodes"], "provenance must carry the loop lineage"
    kinds = {n["primitive_kind"] for n in pub["provenance"]["nodes"]}
    assert "hypothesis" in kinds
    assert pub["hypothesis_ids"] == [next(iter(state.hypotheses))]
    # fixture adds no ExperimentRecord; empty is honest, never fabricated
    assert pub["experiment_ids"] == []
    assert pub["doi"] is None  # never invented


def test_build_publication_empty_state_is_honest():
    from r4.research_loop.publish import build_publication
    from r4.research_loop.state import ResearchLoopState

    empty = ResearchLoopState(loop_id="empty", objective="q", domain="")
    pub = build_publication(empty)
    assert pub["experiment_ids"] == []
    assert pub["provenance"]["nodes"] == []
    assert pub["status"] == "draft"


def test_publication_shape_matches_contract_fields():
    from r4.research_loop.publish import build_publication

    pub = build_publication(_state(), authors=["alice"])
    for key in (
        "id",
        "project_id",
        "title",
        "status",
        "abstract",
        "authors",
        "experiment_ids",
        "dataset_ids",
        "hypothesis_ids",
        "catalog_ids",
        "provenance",
        "doi",
        "created_at",
        "updated_at",
    ):
        assert key in pub, key
    assert pub["authors"] == ["alice"]


# ── D-45 join: catalog ids into the provenance graph ──────────────────


def test_build_publication_stamps_catalog_ids_into_provenance():
    """Pinned catalogs become CATALOG nodes; empty stays empty."""
    from r4.research_loop.publish import build_publication

    pub = build_publication(_state(), catalog_ids=["scat-b", "scat-a"])
    assert pub["catalog_ids"] == ["scat-a", "scat-b"]  # sorted, deduped
    nodes = [n for n in pub["provenance"]["nodes"] if n["primitive_kind"] == "catalog"]
    assert [n["primitive_id"] for n in nodes] == ["scat-a", "scat-b"]
    assert all(n["relation"] == "cataloged_in" for n in nodes)

    pub2 = build_publication(_state(), catalog_ids=["scat-a", "scat-a"])
    assert pub2["catalog_ids"] == ["scat-a"]


def test_build_publication_without_catalogs_is_honest():
    from r4.research_loop.publish import build_publication

    pub = build_publication(_state())
    assert pub["catalog_ids"] == []
    assert not [n for n in pub["provenance"]["nodes"] if n["primitive_kind"] == "catalog"]


# ── D-45 tail: S3-backed draft storage ────────────────────────────────


class _FakeStorageClient:
    """Upload + artifact-register fakes for the draft-storage pins."""

    def __init__(self, *, uri="s3://bucket/publications/pub-d45.md", fail=False):
        self._uri = uri
        self.fail = fail
        self.uploads = []
        self.registrations = []

    def upload_file_bytes(self, filename, content, *, content_type, purpose, key=None):
        if self.fail:
            return {"key": "publications/x", "bucket": "bucket"}  # no uri
        self.uploads.append((filename, content, content_type, purpose))
        return {"key": "publications/x", "bucket": "bucket", "uri": self._uri}

    class _Artifacts:
        def __init__(self, outer):
            self.outer = outer

        def register(self, artifact_type, name, owner_id, uri, **kw):
            self.outer.registrations.append((artifact_type, name, owner_id, uri, kw))
            return {"id": "art-77", "uri": uri}

    @property
    def artifacts(self):
        return self._Artifacts(self)


def test_store_publication_body_uploads_and_registers():
    from r4.research_loop.publish import build_publication, store_publication_body

    pub = build_publication(_state())
    client = _FakeStorageClient()
    stored = store_publication_body(client, pub, "# Draft\nbody", owner_id="alice")
    assert stored["content_uri"] == "s3://bucket/publications/pub-d45.md"
    assert stored["artifact_id"] == "art-77"
    assert client.uploads[0][0] == "publication-pub-d45.md"
    assert client.uploads[0][2] == "text/markdown"
    assert client.registrations[0][0] == "result"
    assert client.registrations[0][4]["metadata"]["publication_id"] == "pub-d45"
    assert pub["content_uri"] == ""  # input record never mutated in place


def test_store_publication_body_refuses_uriless_receipt():
    from r4.research_loop.publish import build_publication, store_publication_body

    pub = build_publication(_state())
    client = _FakeStorageClient(fail=True)
    import pytest

    with pytest.raises(ValueError, match="refusing to fabricate"):
        store_publication_body(client, pub, "body", owner_id="alice")


def test_build_publication_carries_content_uri_param():
    from r4.research_loop.publish import build_publication

    pub = build_publication(_state(), content_uri="s3://bucket/publications/x.md")
    assert pub["content_uri"] == "s3://bucket/publications/x.md"
    assert build_publication(_state())["content_uri"] == ""
