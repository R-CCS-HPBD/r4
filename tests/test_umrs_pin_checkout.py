"""Hermetic tests for ``.github/actions/umrs-pin-checkout`` (U2.0, ADR-117 D-2).

The composite checks out ``umrs/protocol`` at the parent's recorded umrs pin
over anonymous HTTPS, for CI jobs that install umrs-protocol but never fetched
the submodule. Here the "remote" is a local repository with a commit that has
``protocol/`` and one that does not; a scratch HOME and no system config keep
the runner's or developer's git configuration out. The fetch against the real
public UMRS-org remote was exercised by hand (see the U2.0a report); CI runs
the composite in gate-release-integrity first.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = REPO_ROOT / ".github" / "actions" / "umrs-pin-checkout"
SCRIPT = ACTION_DIR / "checkout.sh"


def _git(cwd: Path, *args: str, env: dict[str, str]) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@dataclass
class Remote:
    url: str
    with_protocol: str
    without_protocol: str
    env: dict[str, str]


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
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
    src = tmp_path / "umrs-src"
    src.mkdir()
    _git(src, "init", "-q", env=env)
    (src / "src").mkdir()
    (src / "src" / "pyproject.toml").write_text("[project]\nname = 'umrs'\n")
    (src / "LICENSE").write_text("Apache-2.0\n")
    _git(src, "add", "-A", env=env)
    _git(src, "commit", "-qm", "before U-A0", env=env)
    without = _git(src, "rev-parse", "HEAD", env=env)
    (src / "protocol").mkdir()
    (src / "protocol" / "pyproject.toml").write_text("[project]\nname = 'umrs-protocol'\n")
    _git(src, "add", "-A", env=env)
    _git(src, "commit", "-qm", "U-A0", env=env)
    with_protocol = _git(src, "rev-parse", "HEAD", env=env)
    # Fetch-by-sha of a reachable commit, as GitHub serves it.
    _git(src, "config", "uploadpack.allowReachableSHA1InWant", "true", env=env)
    return Remote(src.as_uri(), with_protocol, without, env)


def _run(cwd: Path, remote: Remote, pin: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {**remote.env, "UMRS_URL": remote.url, "UMRS_PIN": pin, **extra}
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


def test_sparse_checkout_holds_only_protocol(tmp_path: Path, remote: Remote) -> None:
    proc = _run(tmp_path, remote, remote.with_protocol, UMRS_SPARSE="1")
    assert proc.returncode == 0, proc.stderr
    umrs = tmp_path / "umrs"
    assert _git(umrs, "rev-parse", "HEAD", env=remote.env) == remote.with_protocol
    assert (umrs / "protocol" / "pyproject.toml").is_file()
    assert not (umrs / "src").exists()


def test_full_checkout_keeps_the_whole_tree(tmp_path: Path, remote: Remote) -> None:
    proc = _run(tmp_path, remote, remote.with_protocol, UMRS_SPARSE="0")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "umrs" / "src" / "pyproject.toml").is_file()
    assert (tmp_path / "umrs" / "protocol" / "pyproject.toml").is_file()


def test_a_tree_already_at_the_pin_is_reused_without_a_fetch(
    tmp_path: Path, remote: Remote
) -> None:
    assert _run(tmp_path, remote, remote.with_protocol).returncode == 0
    proc = _run(tmp_path, remote, remote.with_protocol, UMRS_URL="file:///nonexistent/umrs")
    assert proc.returncode == 0, proc.stderr
    assert "reusing it" in proc.stdout


def test_an_existing_clone_at_another_commit_moves_to_the_pin(
    tmp_path: Path, remote: Remote
) -> None:
    # A reused self-hosted workspace: a full clone left at an older commit.
    assert _run(tmp_path, remote, remote.without_protocol).returncode == 1
    proc = _run(tmp_path, remote, remote.with_protocol)
    assert proc.returncode == 0, proc.stderr
    umrs = tmp_path / "umrs"
    assert _git(umrs, "rev-parse", "HEAD", env=remote.env) == remote.with_protocol
    assert (umrs / "src" / "pyproject.toml").is_file()  # not made sparse


def test_a_pin_without_protocol_fails_loudly(tmp_path: Path, remote: Remote) -> None:
    proc = _run(tmp_path, remote, remote.without_protocol, UMRS_SPARSE="1")
    assert proc.returncode == 1
    assert "::error title=umrs pin has no protocol/::" in proc.stdout


def test_an_empty_gitlink_dir_never_touches_the_parent_repository(
    tmp_path: Path, remote: Remote
) -> None:
    """actions/checkout leaves umrs/ empty; `git -C umrs` there would run in
    the parent repository."""
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q", env=remote.env)
    (parent / "README").write_text("parent\n")
    _git(parent, "add", "README", env=remote.env)
    _git(parent, "commit", "-qm", "parent", env=remote.env)
    head = _git(parent, "rev-parse", "HEAD", env=remote.env)
    (parent / "umrs").mkdir()
    proc = _run(parent, remote, remote.with_protocol, UMRS_SPARSE="1")
    assert proc.returncode == 0, proc.stderr
    assert _git(parent, "rev-parse", "HEAD", env=remote.env) == head
    assert (parent / "umrs" / ".git").is_dir()


def test_a_global_ssh_rewrite_does_not_redirect_the_anonymous_fetch(
    tmp_path: Path, remote: Remote
) -> None:
    """The test jobs set url.git@github.com:.insteadOf globally; the longest
    insteadOf wins, so the script's self-mapping keeps the fetch on its URL."""
    rewrite = f"url.{tmp_path / 'nowhere'}/.insteadOf"
    proc = _run(
        tmp_path,
        remote,
        remote.with_protocol,
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0=rewrite,
        GIT_CONFIG_VALUE_0=remote.url.rsplit("/", 1)[0] + "/",
    )
    assert proc.returncode == 0, proc.stderr


SPARSE_EXPR = (
    "${{ (inputs.tree == 'sparse' || (inputs.tree == 'auto'"
    " && runner.environment == 'github-hosted')) && '1' || '0' }}"
)


def _sparse_for(tree: str, environment: str) -> str:
    """Evaluate the composite's UMRS_SPARSE expression (as shipped) for one input/runner.

    The expression only uses ==, &&, || and string literals, whose GitHub
    semantics match Python's ==, and, or on these operands.
    """
    action = yaml.safe_load((ACTION_DIR / "action.yaml").read_text())
    expr = action["runs"]["steps"][0]["env"]["UMRS_SPARSE"]
    expr = expr.removeprefix("${{").removesuffix("}}")
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = expr.replace("inputs.tree", repr(tree)).replace("runner.environment", repr(environment))
    return eval(expr, {"__builtins__": {}})  # noqa: S307 - repo constant, no names


def test_the_composite_is_anonymous_and_sparse_only_on_hosted_runners() -> None:
    action = yaml.safe_load((ACTION_DIR / "action.yaml").read_text())
    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["tree"]["default"] == "auto"
    (step,) = action["runs"]["steps"]
    assert step["run"] == 'bash "$GITHUB_ACTION_PATH/checkout.sh"'
    assert step["env"] == {"UMRS_SPARSE": SPARSE_EXPR}
    text = (ACTION_DIR / "action.yaml").read_text() + SCRIPT.read_text()
    for forbidden in ("secrets.", "TOKEN", "ssh-agent", "x-access-token"):
        assert forbidden not in text, forbidden
    assert "https://github.com/UMRS-org/umrs.git" in SCRIPT.read_text()


@pytest.mark.parametrize(
    ("tree", "environment", "sparse"),
    [
        ("auto", "github-hosted", "1"),
        ("auto", "self-hosted", "0"),
        ("full", "github-hosted", "0"),
        ("full", "self-hosted", "0"),
        ("sparse", "github-hosted", "1"),
        ("sparse", "self-hosted", "1"),
    ],
)
def test_the_tree_input_overrides_the_runner_default(
    tree: str, environment: str, sparse: str
) -> None:
    """test-r4's hosted fallback passes tree: full, since it installs ./umrs/src."""
    assert _sparse_for(tree, environment) == sparse
