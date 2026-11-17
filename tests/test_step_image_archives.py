"""The gated-run archive name and keep-newest pruning in a cluster's step-image
archive dir (ADR-112 B2; docs/development/gated-delegation-plan-2026-11-08.md
§5 B2.0 §2, §5.3 step 16, §6; 2026-11-12).

``deploy/ansible/roles/front_end/files/step-image-archives.sh`` is streamed by
hpc-code-sync-cluster.yaml over SSH (``bash -s -- alias|prune ...``). A
person's gate and gated job read the platform step image only as
``<archive dir>/<archive_sha256>.tar``; ``alias`` places that name next to the
verified ``umrs-step-<sha12>-<arch>.tar``, ``prune`` keeps the newest archives
and drops a pruned archive's name unless a kept sidecar names the same hash.
Hermetic: a throwaway archive dir, no network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy/ansible/roles/front_end/files/step-image-archives.sh"
SHA12 = "0123456789ab"
ARCH = "aarch64"


def _run(*args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": path or os.environ["PATH"], "LANG": "C"}
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )


def _out(proc: subprocess.CompletedProcess[str]) -> dict:
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1, proc.stdout + proc.stderr
    return json.loads(lines[0])


def _archive(directory: Path, tag: str, body: str, digest: str, age: int = 0) -> Path:
    path = directory / f"umrs-step-{tag}-{ARCH}.tar"
    path.write_text(body)
    Path(f"{path}.sha256").write_text(f"{digest}  {path.name}\n")
    Path(f"{path}.id").write_text("f" * 64 + "\n")
    stamp = 1_000_000 - age * 100
    for item in (path, Path(f"{path}.sha256"), Path(f"{path}.id")):
        os.utime(item, (stamp, stamp))
    return path


@pytest.fixture
def images(tmp_path: Path) -> Path:
    directory = tmp_path / "images"
    directory.mkdir()
    directory.chmod(0o755)
    return directory


def _alias(images: Path, digest: str, **kw) -> subprocess.CompletedProcess[str]:
    return _run("alias", str(images), ARCH, SHA12, digest, **kw)


def test_alias_hard_links_the_archive_under_its_sha256_readable_by_others(images):
    archive = _archive(images, SHA12, "image bytes", "b" * 64)
    archive.chmod(0o600)  # what a umask-077 stager leaves
    proc = _alias(images, "b" * 64)
    assert proc.returncode == 0, proc.stderr
    out = _out(proc)
    alias = images / f"{'b' * 64}.tar"
    assert (out["ok"], out["how"], out["alias"]) == (True, "link", str(alias))
    assert alias.samefile(archive) and not alias.is_symlink()
    assert (alias.stat().st_mode & 0o777) == 0o644
    assert isinstance(out["others_can_read"], bool)


def test_a_second_alias_run_changes_nothing(images):
    _archive(images, SHA12, "image bytes", "b" * 64)
    assert _alias(images, "b" * 64).returncode == 0
    before = (images / f"{'b' * 64}.tar").stat()
    proc = _alias(images, "b" * 64)
    assert _out(proc)["how"] == "present"
    after = (images / f"{'b' * 64}.tar").stat()
    # No mode change: a ctime move would void the gate's archive cache.
    assert (after.st_ino, after.st_ctime_ns) == (before.st_ino, before.st_ctime_ns)


@pytest.mark.parametrize("planted", ["stale", "symlink"])
def test_alias_replaces_whatever_held_the_name(images, tmp_path, planted):
    archive = _archive(images, SHA12, "image bytes", "b" * 64)
    alias = images / f"{'b' * 64}.tar"
    if planted == "stale":
        alias.write_text("other bytes")
    else:
        (tmp_path / "elsewhere.tar").write_text("other bytes")
        alias.symlink_to(tmp_path / "elsewhere.tar")
    assert _out(_alias(images, "b" * 64))["how"] == "link"
    assert alias.samefile(archive) and not alias.is_symlink()
    if planted == "symlink":
        assert (tmp_path / "elsewhere.tar").read_text() == "other bytes"


def test_alias_copies_where_the_filesystem_has_no_hard_links(images, tmp_path):
    archive = _archive(images, SHA12, "image bytes", "b" * 64)
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "ln").write_text("#!/bin/sh\nexit 1\n")
    (shim / "ln").chmod(0o755)
    proc = _alias(images, "b" * 64, path=f"{shim}:{os.environ['PATH']}")
    assert _out(proc)["how"] == "copy"
    alias = images / f"{'b' * 64}.tar"
    assert alias.read_text() == "image bytes" and not alias.samefile(archive)
    assert not list(images.glob("*.tmp"))


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("missing", "missing or not a regular file"),
        ("symlink", "missing or not a regular file"),
        ("other_hash", "does not name"),
    ],
)
def test_alias_refuses_an_archive_its_sidecar_does_not_vouch_for(images, tmp_path, setup, reason):
    if setup == "symlink":
        target = _archive(tmp_path, "000000000000", "x", "b" * 64)
        (images / f"umrs-step-{SHA12}-{ARCH}.tar").symlink_to(target)
    elif setup == "other_hash":
        _archive(images, SHA12, "image bytes", "c" * 64)
    proc = _alias(images, "b" * 64)
    assert proc.returncode == 1
    assert _out(proc)["ok"] is False and reason in _out(proc)["reason"]
    assert not (images / f"{'b' * 64}.tar").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["alias", "/x", "arm64", SHA12, "b" * 64],
        ["alias", "/x", ARCH, "0123", "b" * 64],
        ["alias", "/x", ARCH, SHA12, "B" * 64],
        ["alias", "/x", ARCH, SHA12, "b" * 64 + ";id"],
        ["prune", "/x", ARCH, "0"],
        ["prune", "/x", "x86", "3"],
        ["remove", "/x"],
        [],
    ],
)
def test_usage_errors_exit_2(args):
    assert _run(*args).returncode == 2


def test_alias_reports_a_dir_other_logins_cannot_reach(images):
    _archive(images, SHA12, "image bytes", "b" * 64)
    images.chmod(0o700)
    assert _out(_alias(images, "b" * 64))["others_can_read"] is False


TAGS = ["aaaaaaaaaaa1", "aaaaaaaaaaa2", "aaaaaaaaaaa3", "aaaaaaaaaaa4", "aaaaaaaaaaa5"]
#: aaaaaaaaaaa5 (pruned) has the bytes of aaaaaaaaaaa1 (kept).
SUMS = dict(zip(TAGS, ["1" * 64, "2" * 64, "3" * 64, "4" * 64, "1" * 64], strict=True))
UNTOUCHED = [f"{'e' * 64}.tar", "umrs-step-aaaaaaaaaaa9-x86_64.tar", "r4-mcp-latest-arm64.tar"]


def _five_archives_with_names(images: Path) -> None:
    for age, tag in enumerate(TAGS):
        archive = _archive(images, tag, tag, SUMS[tag], age)
        name = images / f"{SUMS[tag]}.tar"
        if not name.exists():
            os.link(archive, name)
    for name in UNTOUCHED:
        (images / name).write_text("x")


def test_prune_keeps_the_newest_and_drops_only_unnamed_gated_names(images):
    _five_archives_with_names(images)
    tags, untouched = TAGS, UNTOUCHED
    proc = _run("prune", str(images), ARCH, "3")
    assert proc.returncode == 0, proc.stderr
    out = _out(proc)
    names = {p.name for p in images.iterdir()}
    assert sorted(out["pruned"]) == [f"umrs-step-{t}-{ARCH}.tar" for t in tags[3:]]
    assert out["aliases_removed"] == [f"{'4' * 64}.tar"]
    # aaaaaaaaaaa5 was pruned, but kept aaaaaaaaaaa1 names the same hash.
    assert {f"{'1' * 64}.tar", f"{'2' * 64}.tar", f"{'3' * 64}.tar", *untouched} <= names
    assert not {f"{'4' * 64}.tar", f"umrs-step-{tags[4]}-{ARCH}.tar.sha256"} & names


def test_prune_of_a_missing_dir_is_a_no_op(tmp_path):
    out = _out(_run("prune", str(tmp_path / "absent"), ARCH, "3"))
    assert out == {"ok": True, "pruned": [], "aliases_removed": []}


def test_the_streamed_form_ansible_runs_works(images):
    _archive(images, SHA12, "image bytes", "b" * 64)
    proc = subprocess.run(
        ["bash", "-s", "--", "alias", str(images), ARCH, SHA12, "b" * 64],
        input=SCRIPT.read_text(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0 and _out(proc)["ok"] is True, proc.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_the_script_is_shellcheck_clean():
    proc = subprocess.run(
        ["shellcheck", "-s", "bash", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout
