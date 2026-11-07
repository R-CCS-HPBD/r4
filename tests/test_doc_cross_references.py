"""Test that canonical documentation cross-references are consistent.

Verifies:
1. All CANONICAL documents in DOCUMENT_LIFECYCLE_INDEX.md exist on disk.
2. All CANONICAL documents are referenced in ARCHITECTURE_DOCUMENT_MAP.md.
3. All documents referenced in ARCHITECTURE_DOCUMENT_MAP.md exist on disk.

Both index documents were moved from ``docs/`` to ``docs/history/`` unchanged
(rename R100 in 26276265, 2026-06-08) when ``docs/`` was reorganised from a
flat layout into categorised subdirectories. The documents they name still
exist, but no longer at ``docs/<NAME>.md`` — the index says so itself in its
2026-06-08 PATH NOTE ("The document names below remain correct; actual paths
now include their category prefix"). Resolution is therefore by basename
anywhere under ``docs/``, which keeps the invariant these tests exist for:
every name a navigation document cites must still resolve to a real file.

Enforcement class: advisory
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
LIFECYCLE_INDEX = DOCS_DIR / "history" / "DOCUMENT_LIFECYCLE_INDEX.md"
DOCUMENT_MAP = DOCS_DIR / "history" / "ARCHITECTURE_DOCUMENT_MAP.md"


def _docs_by_basename() -> dict[str, list[Path]]:
    """Index every markdown file under docs/ by its basename.

    docs/ used to be flat; it is now categorised, so a document cited by bare
    filename may live in any subdirectory.
    """
    index: dict[str, list[Path]] = {}
    for path in DOCS_DIR.rglob("*.md"):
        index.setdefault(path.name, []).append(path)
    return index


def _extract_canonical_docs(lifecycle_path: Path) -> list[str]:
    """Extract document filenames from the CANONICAL section of the lifecycle index."""
    content = lifecycle_path.read_text()

    # Find the CANONICAL section
    canonical_match = re.search(
        r"### CANONICAL.*?\n(.*?)### ",
        content,
        re.DOTALL,
    )
    if not canonical_match:
        return []

    canonical_section = canonical_match.group(1)

    # Extract backtick-quoted filenames from table rows
    filenames = re.findall(r"`([A-Z][A-Z0-9_-]+\.md)`", canonical_section)
    return filenames


def _extract_docmap_references(docmap_path: Path) -> list[str]:
    """Extract document filenames referenced in the architecture document map."""
    content = docmap_path.read_text()

    # Match markdown link targets: [FILENAME.md](FILENAME.md) or [text](FILENAME.md)
    link_targets = re.findall(r"\[.*?\]\(([A-Z][A-Z0-9_-]+\.md)\)", content)
    # Also match backtick-quoted filenames in table cells
    backtick_names = re.findall(r"`([A-Z][A-Z0-9_-]+\.md)`", content)

    return list(set(link_targets + backtick_names))


class TestDocCrossReferences:
    """Test documentation cross-reference consistency."""

    def test_lifecycle_index_exists(self):
        assert LIFECYCLE_INDEX.exists(), "DOCUMENT_LIFECYCLE_INDEX.md must exist"

    def test_document_map_exists(self):
        assert DOCUMENT_MAP.exists(), "ARCHITECTURE_DOCUMENT_MAP.md must exist"

    def test_canonical_docs_exist_on_disk(self):
        """Every CANONICAL doc listed in the lifecycle index must exist."""
        canonical_docs = _extract_canonical_docs(LIFECYCLE_INDEX)
        assert canonical_docs, "Should find at least one CANONICAL document"

        on_disk = _docs_by_basename()
        missing = [name for name in canonical_docs if name not in on_disk]
        assert not missing, f"CANONICAL docs missing from disk: {missing}"

    def test_canonical_docs_in_docmap(self):
        """Every CANONICAL doc should be referenced in the architecture document map."""
        canonical_docs = set(_extract_canonical_docs(LIFECYCLE_INDEX))
        docmap_refs = set(_extract_docmap_references(DOCUMENT_MAP))

        # These are intentionally not in the document map (directories, not files)
        skip = {"adr/", "r4/"}

        missing = canonical_docs - docmap_refs - skip
        assert not missing, (
            f"CANONICAL docs not referenced in ARCHITECTURE_DOCUMENT_MAP.md: {missing}"
        )

    def test_docmap_references_exist_on_disk(self):
        """Every doc referenced in the document map must exist on disk."""
        docmap_refs = _extract_docmap_references(DOCUMENT_MAP)
        assert docmap_refs, "Should find at least one document reference"

        on_disk = _docs_by_basename()
        missing = [name for name in docmap_refs if name not in on_disk]
        assert not missing, f"Document map references missing from disk: {missing}"

    def test_canonical_count_minimum(self):
        """Sanity check: we expect at least 20 CANONICAL documents."""
        canonical_docs = _extract_canonical_docs(LIFECYCLE_INDEX)
        assert len(canonical_docs) >= 20, (
            f"Expected ≥20 CANONICAL docs, found {len(canonical_docs)}"
        )
