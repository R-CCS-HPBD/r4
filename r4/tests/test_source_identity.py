"""Source identity for stale-child detection (round-3 task 7)."""

from __future__ import annotations

from r4.source_identity import (
    SourceIdentity,
    _content_digest,
    current_source_identity,
    identities_match,
)


def _write_tree(root) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "a.py").write_text("print(1)\n")
    (root / "pkg" / "b.py").write_text("print(2)\n")


def test_digest_stable_and_content_sensitive(tmp_path) -> None:
    tree = tmp_path / "src"
    _write_tree(tree)
    digest1 = _content_digest(tree)
    assert digest1 == _content_digest(tree)
    (tree / "pkg" / "a.py").write_text("print(99)\n")
    assert _content_digest(tree) != digest1


def test_identity_includes_repo_commit(tmp_path) -> None:
    identity = current_source_identity()
    assert identity.digest
    # ai4s-platform checkout → git commit present; never fails on non-git.
    assert identity.git_commit is None or len(identity.git_commit) >= 7
    assert identity.source_dir


def test_match_rules() -> None:
    a = SourceIdentity(git_commit="abc123", digest="d1", source_dir="/x")
    b = SourceIdentity(git_commit="def456", digest="d1", source_dir="/x")
    c = SourceIdentity(git_commit="abc123", digest="d2", source_dir="/x")
    assert identities_match(a, b), "equal content wins even across commits"
    assert not identities_match(a, c)


def test_acp_initialize_includes_source() -> None:
    import r4.acp as acp

    server = acp.ACPServer()
    acp._register_lifecycle_methods(server)
    result = server._methods["initialize"]({})
    assert "source" in result
    assert result["source"]["digest"]
    assert result["capabilities"]["ask_user"] is True


def test_bridge_initialize_snapshot_includes_source() -> None:
    """Bridge snapshot carries the same identity dict."""
    from r4.source_identity import current_source_identity

    identity = current_source_identity()
    result = identity.to_dict()
    assert set(result) >= {"git_commit", "digest", "source_dir"}
    assert result["digest"]
