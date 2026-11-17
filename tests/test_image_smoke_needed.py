"""Hermetic tests for ``scripts/deps/image-smoke-needed.sh`` (ADR-100).

The script decides whether a pull request rebuilds the umrs-server image. A
scratch parent repository records a real gitlink to a scratch ``umrs``
repository; each case moves the pointer (or edits a parent file) and reads
the verdict. U2.0 (ADR-117 D-2) added ``protocol/`` inside umrs and the
parent's ``r4-contracts/pyproject.toml``: umrs-protocol and r4-contracts
install into the image from the checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deps" / "image-smoke-needed.sh"


@dataclass
class Repos:
    parent: Path
    env: dict[str, str]

    @property
    def umrs(self) -> Path:
        return self.parent / "umrs"

    def git(self, cwd: Path, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def umrs_commit(self, rel: str, text: str) -> str:
        path = self.umrs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.git(self.umrs, "add", "-A")
        self.git(self.umrs, "commit", "-qm", f"umrs: {rel}")
        return self.git(self.umrs, "rev-parse", "HEAD")

    def parent_commit(self, *, pin: str | None = None, rel: str = "", text: str = "") -> str:
        if pin:
            self.git(self.parent, "update-index", "--add", "--cacheinfo", f"160000,{pin},umrs")
        if rel:
            (self.parent / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.parent / rel).write_text(text)
            self.git(self.parent, "add", rel)
        self.git(self.parent, "commit", "-qm", "parent")
        return self.git(self.parent, "rev-parse", "HEAD")

    def verdict(self, base: str, head: str) -> tuple[str, str]:
        done = subprocess.run(
            ["bash", str(SCRIPT), base, head],
            cwd=self.parent, env=self.env, capture_output=True, text=True, timeout=60, check=True,
        )  # fmt: skip
        return done.stdout.strip(), done.stderr.strip()


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    r = Repos(tmp_path / "parent", env)
    r.umrs.mkdir(parents=True)
    r.git(r.parent, "init", "-q")
    r.git(r.umrs, "init", "-q")
    return r


def _base(r: Repos) -> str:
    first = r.umrs_commit("src/umrs/app.py", "x = 1\n")
    return r.parent_commit(pin=first, rel="README", text="parent\n")


def test_a_pointer_move_that_touches_protocol_builds(repos: Repos):
    base = _base(repos)
    head = repos.parent_commit(pin=repos.umrs_commit("protocol/pyproject.toml", "[project]\n"))
    verdict, reason = repos.verdict(base, head)
    assert verdict == "true", reason
    assert "changes the image's dependency files" in reason


def test_a_pointer_move_elsewhere_in_umrs_does_not_build(repos: Repos):
    base = _base(repos)
    head = repos.parent_commit(pin=repos.umrs_commit("src/umrs/app.py", "x = 2\n"))
    assert repos.verdict(base, head)[0] == "false"


def test_an_r4_contracts_pyproject_change_builds(repos: Repos):
    base = _base(repos)
    head = repos.parent_commit(rel="r4-contracts/pyproject.toml", text="[project]\n")
    verdict, reason = repos.verdict(base, head)
    assert verdict == "true", reason
    assert reason == "parent dependency files changed"


def test_an_r4_contracts_source_change_alone_does_not_build(repos: Repos):
    base = _base(repos)
    head = repos.parent_commit(rel="r4-contracts/src/r4_contracts/x.py", text="y = 1\n")
    assert repos.verdict(base, head)[0] == "false"
