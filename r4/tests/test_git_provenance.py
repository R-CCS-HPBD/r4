"""ADR-108 D-6 (W2a): ingest says where a file came from in git.

Only basenames reached UMRS before, so nothing tied an artifact to the commit
a result was produced from. Every git call in the scratch repository goes
through ``git_hermetic``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from git_hermetic import git_init, run_git

from r4.git_provenance import ProvenanceReader, scrub_remote


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:ghp_s3cret@github.com/lab/mof.git", "github.com/lab/mof"),
        ("https://github.com/lab/mof", "github.com/lab/mof"),
        ("git@github.com:lab/mof.git", "github.com/lab/mof"),
        ("ssh://git@gitlab.example.org/lab/mof.git", "gitlab.example.org/lab/mof"),
        ("", ""),
    ],
)
def test_the_remote_never_carries_credentials(url: str, expected: str) -> None:
    assert scrub_remote(url) == expected


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "repo"
    (root / "analysis").mkdir(parents=True)
    git_init(root)
    (root / "analysis" / "fit.py").write_text("x = 1\n")
    run_git(root, "add", "analysis/fit.py")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "remote", "add", "origin", "https://bot:tok@github.com/lab/mof.git")
    return root


def test_a_committed_file_reports_path_repo_branch_commit_clean(repo: Path) -> None:
    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    block = ProvenanceReader().of(str(repo / "analysis" / "fit.py"))
    assert block == {
        "path": "analysis/fit.py",
        "commit": head,
        "branch": branch,
        "repo": "github.com/lab/mof",
        "dirty": False,
    }


def test_edited_and_untracked_files_are_dirty(repo: Path) -> None:
    (repo / "analysis" / "fit.py").write_text("x = 2\n")
    (repo / "notes.md").write_text("new\n")
    reader = ProvenanceReader()
    edited, untracked = (
        reader.of(str(repo / "analysis" / "fit.py")),
        reader.of(str(repo / "notes.md")),
    )
    assert (edited["dirty"], untracked["dirty"], untracked["path"]) == (True, True, "notes.md")


def test_outside_a_repository_there_is_none(tmp_path: Path) -> None:
    loose = tmp_path / "loose.txt"
    loose.write_text("hi\n")
    assert ProvenanceReader().of(str(loose)) is None


def test_ingest_paths_sends_provenance_inline_and_on_upload(repo: Path) -> None:
    from r4.knowledge import KnowledgeClient

    big = repo / "data.bin"
    big.write_bytes(b"\xff\xfe binary")
    run_git(repo, "add", "data.bin")
    run_git(repo, "commit", "-q", "-m", "data")
    sent: dict[str, Any] = {}

    def post_multipart(path: str, **kwargs: Any) -> dict[str, Any]:
        sent["upload"] = kwargs
        return {"job_id": "j-up"}

    knowledge = KnowledgeClient(SimpleNamespace(_api_prefix="/gw", post_multipart=post_multipart))
    knowledge.ingest_sources = lambda pid, specs, **kw: (
        sent.setdefault("specs", specs) and {"job_id": "j-in"}
    )  # type: ignore[method-assign]
    knowledge.ingest_paths("p1", [str(repo / "analysis" / "fit.py"), str(big)])
    inline = sent["specs"][0]
    assert (inline["uri"], inline["provenance"]["path"]) == ("fit.py", "analysis/fit.py")
    uploaded = json.loads(sent["upload"]["data"]["provenance"])
    assert (uploaded["path"], uploaded["dirty"]) == ("data.bin", False)


def test_a_file_outside_a_repo_ingests_without_provenance(tmp_path: Path) -> None:
    from r4.knowledge import KnowledgeClient

    loose = tmp_path / "loose.txt"
    loose.write_text("hi\n")
    sent: dict[str, Any] = {}
    knowledge = KnowledgeClient(SimpleNamespace(_api_prefix="/gw"))
    knowledge.ingest_sources = lambda pid, specs, **kw: (
        sent.setdefault("specs", specs) and {"job_id": "j"}
    )  # type: ignore[method-assign]
    knowledge.ingest_paths("p1", [str(loose)])
    assert "provenance" not in sent["specs"][0]
