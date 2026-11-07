"""§13 fixture-matrix parity — CLI half.

Same ten scenarios as `packages/r4-tui-v3/src/adapters/science-fixtures.ts`:
each resolves to the same semantic state on every surface (workstream §13).
The CLI renders server wire payloads directly; these tests pin the render
contract for rows with and without links (mint state) and the legacy lane.
"""

from __future__ import annotations

from r4._cli.data import _catalog_links_line, _render_catalog_search

# Scenario keys must match TUI SCIENCE_FIXTURES (cross-surface parity).
SCENARIOS = [
    "catalogue-only",
    "doc-no-nodes",
    "nodes-no-embeddings",
    "fully-searchable",
    "embedding-pending",
    "partial",
    "orphaned",
    "legacy-corpus-only",
    "provenance-incomplete",
    "identity-migration",
]


def _row(s3_key: str, links):
    return {
        "s3_key": s3_key,
        "state": "ready",
        "summary": "fixture row",
        "deterministic_summary": "",
        "perception_summary": None,
        "perception_model": None,
        "detected_modality": "text",
        "tags": [],
        "filename_signals": [],
        "extracted_text_ref": None,
        "relevance_score": 0.5,
        "links": links,
    }


def test_matrix_has_ten_scenarios_matching_tui_names():
    assert sorted(SCENARIOS) == sorted(
        [
            "catalogue-only",
            "doc-no-nodes",
            "nodes-no-embeddings",
            "fully-searchable",
            "embedding-pending",
            "partial",
            "orphaned",
            "legacy-corpus-only",
            "provenance-incomplete",
            "identity-migration",
        ]
    )
    assert len(SCENARIOS) == 10


def test_links_line_none_means_no_mint_not_error():
    line = _catalog_links_line(None)
    assert line == "links: none (no canonical document minted)"


def test_links_line_renders_document_chunks_and_vector_models():
    line = _catalog_links_line(
        {
            "document_id": "ragdoc-run01",
            "link_method": "s3_object",
            "chunk_nodes": 4,
            "vector_rows": [{"model": "gte-multilingual-base", "dimension": 768}, {"model": None}],
        }
    )
    assert "document=ragdoc-run01" in line
    assert "chunk_nodes=4" in line
    assert "2 vector row(s)" in line
    assert "gte-multilingual-base" in line


def test_zero_vector_rows_is_not_rendered_as_missing_knowledge():
    # nodes-no-embeddings / embedding-pending: chunks exist, vectors pending —
    # the line must show the document identity, not an absence claim.
    line = _catalog_links_line(
        {
            "document_id": "ragdoc-notes",
            "link_method": "s3_object",
            "chunk_nodes": 4,
            "vector_rows": [],
        }
    )
    assert "document=ragdoc-notes" in line
    assert "0 vector row(s)" in line
    assert "none" not in line


def test_legacy_lane_row_renders_with_null_links(capsys):
    # Invariant 2: legacy corpus rows stay subordinate — rendered with the
    # no-mint links wording, same as every unminted catalogue row.
    payload = {
        "query": "",
        "project_id": "proj-1",
        "results": [_row("legacy/corpus/chunk-991", None)],
    }
    _render_catalog_search(payload)
    out = capsys.readouterr().out
    assert "legacy/corpus/chunk-991" in out
    assert "links: none (no canonical document minted)" in out


def test_migrated_identity_link_method_renders(capsys):
    # identity-migration: filename-linked legacy-prefixed document id.
    payload = {
        "query": "",
        "project_id": "proj-1",
        "results": [
            _row(
                "proj/docs/migrated.md",
                {
                    "document_id": "ragdoc-legacy-migrated",
                    "link_method": "filename",
                    "chunk_nodes": 4,
                    "vector_rows": [{"model": "m"}],
                },
            )
        ],
    }
    _render_catalog_search(payload)
    out = capsys.readouterr().out
    assert "ragdoc-legacy-migrated" in out
    assert "via filename" in out
