"""ADR-031 dependency-direction validator (static fence, normative).

Enforces the plane hierarchy at CODE level — not by reasoning about any one
SQL implementation, but by forbidding the architectural anti-patterns that
created the 358-row legacy drift:

1. **Catalogue plane code MUST NOT know about vector storage**: no direct
   references to the embedding table outside the designated bridge
   (`umrs/src/umrs/data_catalog/links.py`) and projections that inject the
   shared pool (`umrs/src/umrs/api/catalog.py` only pulls `hydrate_links`).

2. **No cross-plane joins via ``s3_key``**: a module may hold an S3 key AND
   reference the vector table only inside the bridge (which passes the key
   into DOCUMENT resolution — never ``JOIN ... s3_key`` against
   embeddings). The anti-pattern this catches literally:
   `...JOIN knowledge_node_embeddings ... s3_key...` in the same file
   outside the allowlist.

3. **Contract is dependency direction, not implementation**: the bridge is
   the ONE file allowed to hold both worlds; everything else is one-sided.

Usage:

    python tools/knowledge_plane_fence.py           # exits 0/1
    python -m pytest tests/test_knowledge_plane_fence.py   # CI gate

Enforcement class: PERMANENT (ADR-031 dependency-direction invariants).
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Designated bridge (the only catalogue-side file allowed to reference
#    both catalogue truths AND vector-plane identifiers).
_ALLOWED_CROSS_FILES = frozenset(
    {
        "umrs/src/umrs/data_catalog/links.py",
    }
)

# Allowed raw occurrences — docs, migrations, tests, ADRs, fences: they are
# not operation paths and may name both planes via narrative text.
_EXEMPT_PATH_PREFIXES = (
    "docs/",
    "umrs/docs/",
    "umrs/tests/",
    "umrs/src/umrs/migrations/",
    "tests/",
    ".github/",
    "memories/",
)

# Violation markers within catalogue-side modules.
_EMBEDDING_TABLE_TOKENS = re.compile(r"knowledge_node_embeddings|knowledge_node_vectors")
_S3_JOIN_MARKER = re.compile(
    r"JOIN\s+knowledge_node_embeddings[^\n]*s3_key|ON\s+[^\n]*s3_key[^\n]*JOIN\s+knowledge_node_embeddings",
    re.IGNORECASE,
)


def _is_exempt(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES)


def _violations_for_file(rel: str, content: str) -> list[str]:
    violations: list[str] = []
    if _is_exempt(rel) or rel in _ALLOWED_CROSS_FILES:
        return violations

    cross_references = _EMBEDDING_TABLE_TOKENS.findall(content)
    if cross_references:
        violations.append(
            "references a vector-plane table identifier outside the designated "
            f"bridge ({sorted(set(cross_references))}); only "
            "umrs/src/umrs/data_catalog/links.py may hold catalogue-plane/"
            "vector-plane joins"
        )
    for match in _S3_JOIN_MARKER.finditer(content):
        violations.append(
            "s3_key-shaped join against the vector plane (ADR-031 anchor "
            f"direction violation): {match.group(0)[:80].strip()!r}"
        )
    return violations


def validate_plane_direction(repo_root: Path) -> list[str]:
    """Scan catalogue-plane modules for plane-direction violations.

    Scope is deliberately the CATALOGUE plane (data_catalog + api/catalog*):
    the canonical spine legitimately holds vector identifiers (backed by
    node_id joins, which the ADR allows), so shading the whole api/ tree
    would flag legitimate canonical code.
    """
    candidates = [
        repo_root / "umrs" / "src" / "umrs" / "data_catalog",
        repo_root / "umrs" / "src" / "umrs" / "api" / "catalog.py",
        repo_root / "umrs" / "src" / "umrs" / "api" / "catalog_store.py",
    ]
    violations: list[str] = []
    for base in candidates:
        files = sorted(base.rglob("*.py")) if base.is_dir() else ([base] if base.exists() else [])
        for py in files:
            rel = py.relative_to(repo_root).as_posix()
            text = py.read_text(encoding="utf-8", errors="replace")
            for violation in _violations_for_file(rel, text):
                violations.append(f"{rel}: {violation}")
    return violations


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    violations = validate_plane_direction(root)
    if violations:
        print("FAIL: ADR-031 dependency-direction violations found:\n")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print("OK: plane-direction invariant holds.")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI path
    raise SystemExit(main())
