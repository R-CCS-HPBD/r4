"""ADR-031 dependency-direction validator — unit tests.

The fence pins the architectural rule (catalogue plane never references
vector storage outside the designated bridge, and no s3_key-shaped joins
against vectors anywhere), independent of any one SQL implementation.
"""

from __future__ import annotations

from pathlib import Path

from tools.knowledge_plane_fence import validate_plane_direction


def _write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_clean_tree_passes(tmp_path: Path):
    _write(
        tmp_path,
        "umrs/src/umrs/data_catalog/query.py",
        "def search():\n    return []\n",
    )
    assert validate_plane_direction(tmp_path) == []


def test_bridge_is_allowlisted(tmp_path: Path):
    _write(
        tmp_path,
        "umrs/src/umrs/data_catalog/links.py",
        '_ST = "knowledge_node_embeddings"\n',
    )
    assert validate_plane_direction(tmp_path) == []


def test_catalogue_code_cannot_reference_vector_table(tmp_path: Path):
    _write(
        tmp_path,
        "umrs/src/umrs/data_catalog/scoring.py",
        'TABLE = "knowledge_node_embeddings"\n',
    )
    violations = validate_plane_direction(tmp_path)
    assert violations
    assert "bridge" in violations[0]


def test_s3_key_join_is_flagged(tmp_path: Path):
    _write(
        tmp_path,
        "umrs/src/umrs/api/catalog.py",
        "cur.execute(\n"
        "    'SELECT ... JOIN knowledge_node_embeddings ON e.node_id = x.s3_key'\n"
        ")\n",
    )
    violations = validate_plane_direction(tmp_path)
    assert any("s3_key" in v for v in violations)


def test_api_catalog_route_is_in_scope_but_clean_shape(tmp_path: Path):
    _write(
        tmp_path,
        "umrs/src/umrs/api/catalog.py",
        "from umrs.data_catalog.links import hydrate_links\n"
        "out = hydrate_links(pool, project_id='p', s3_key='a')\n",
    )
    assert validate_plane_direction(tmp_path) == []


def test_canonical_side_is_out_of_scope(tmp_path: Path):
    _write(
        tmp_path,
        "umrs/src/umrs/api/knowledge_query.py",
        'Q = "knowledge_node_embeddings"\n',
    )
    assert validate_plane_direction(tmp_path) == []


def test_docs_and_tests_are_exempt(tmp_path: Path):
    _write(
        tmp_path,
        "docs/adr/031.md",
        "knowledge_node_embeddings + s3_key may appear in narrative",
    )
    _write(
        tmp_path,
        "umrs/tests/test_any.py",
        'assert "knowledge_node_embeddings" in query\n',
    )
    assert validate_plane_direction(tmp_path) == []


def test_aggregated_violations_reports_each(tmp_path: Path):
    for idx in range(3):
        _write(
            tmp_path,
            f"umrs/src/umrs/data_catalog/bad{idx}.py",
            'TABLE = "knowledge_node_embeddings"\n',
        )
    violations = validate_plane_direction(tmp_path)
    assert len(violations) == 3


def test_real_tree_has_no_violations():
    root = Path(__file__).resolve().parents[1]
    violations = validate_plane_direction(root)
    assert violations == [], "real repo violates ADR-031 direction: " + "\n".join(violations)
