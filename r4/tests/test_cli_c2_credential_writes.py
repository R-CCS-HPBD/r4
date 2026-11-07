"""Credential-bearing config files are written 0600 from the first byte, atomically
(production-quality cycle 2, cli lane, 2026-10-21).

`r4 auth login` run under an audit hook showed config.json (profile api_key),
umrs connections/<name>.json and connections/<name>.token holding their
content at -rw-r--r-- until a later chmod(0o600); and write_text truncates in
place, so an interrupted save left config.json half-written — which
ProfileConfigStore._load_all reads as {} (every profile silently lost).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

_EXPOSED: list[str] = []
_WATCH = {"on": False}


def _audit(event: str, args: tuple) -> None:
    if not _WATCH["on"] or event != "os.chmod":
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    try:
        st = os.stat(path)
    except OSError:
        return
    if st.st_size > 0 and st.st_mode & 0o077:
        _EXPOSED.append(f"{os.fsdecode(path)} {stat.filemode(st.st_mode)}")


sys.addaudithook(_audit)


@pytest.fixture
def exposure(monkeypatch):
    old = os.umask(0o022)
    _EXPOSED.clear()
    _WATCH["on"] = True
    try:
        yield _EXPOSED
    finally:
        _WATCH["on"] = False
        os.umask(old)


def _store(tmp_path: Path):
    from r4.config import ProfileConfigStore

    return ProfileConfigStore(config_dir=tmp_path)


def test_profile_save_never_holds_the_key_world_readable(tmp_path, exposure):
    store = _store(tmp_path)

    store.save("default", gateway_url="http://gw.test:8080", api_key="k" * 40)

    assert exposure == []
    assert stat.S_IMODE((tmp_path / "config.json").stat().st_mode) == 0o600


def test_interrupted_profile_save_keeps_the_stored_profiles(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.save("default", gateway_url="http://gw.test:8080", project_id="p1")
    store.save("other", gateway_url="http://other.test:8080")

    def _torn_write_text(self, data, *args, **kwargs):
        with open(self, "w", encoding="utf-8") as handle:
            handle.write(data[: len(data) // 2])
        raise OSError("No space left on device")

    def _torn_os_write(fd, data):
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_text", _torn_write_text)
    monkeypatch.setattr(os, "write", _torn_os_write)
    with pytest.raises(OSError):
        store.save("default", project_id="p2")
    monkeypatch.undo()

    assert store.load("default")["project_id"] == "p1"
    assert store.load("other")["gateway_url"] == "http://other.test:8080"
    json.loads((tmp_path / "config.json").read_text())


def test_umrs_profile_and_token_never_world_readable(tmp_path, monkeypatch, exposure):
    from r4.source import Source, sync_umrs_connection_profile

    monkeypatch.setenv("UMRS_CONFIG", str(tmp_path / "umrs"))
    source = Source(name="lab", kind="platform", url="http://gw.test:8080", api_key="t" * 40)

    profile = sync_umrs_connection_profile(source, username="alice")

    assert exposure == []
    token = tmp_path / "umrs" / "connections" / "lab.token"
    assert profile is not None
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert token.read_text().strip() == "t" * 40


def test_login_user_config_never_world_readable(tmp_path, monkeypatch, exposure):
    from argparse import Namespace

    from r4._cli import login as login_cli

    monkeypatch.setattr(login_cli, "_load_resolved_username", lambda profile: "alice")
    monkeypatch.setattr(login_cli, "_resolve_umrs_cfg_dir", lambda: str(tmp_path / "umrs"))

    login_cli._ensure_umrs_user_config(Namespace(profile="default"))

    path = tmp_path / "umrs" / "users" / "alice.json"
    assert exposure == []
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())


def test_profile_save_through_a_symlinked_config_updates_the_target(tmp_path):
    """A dotfiles-managed config.json symlink keeps pointing at the updated file.

    Stab cycle 3 (data-integrity#2): the atomic rename replaced the symlink
    itself with a regular file, so the target kept the old project / api_key.
    """
    cfg = tmp_path / "cfg"
    dot = tmp_path / "dotfiles"
    cfg.mkdir()
    dot.mkdir()
    target = dot / "r4-config.json"
    target.write_text(json.dumps({"profiles": {"default": {"gateway_url": "https://a"}}}))
    link = cfg / "config.json"
    link.symlink_to(target)

    _store(cfg).save("default", project_id="p1")

    assert link.is_symlink()
    saved = json.loads(target.read_text())["profiles"]["default"]
    assert saved == {"gateway_url": "https://a", "project_id": "p1"}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert sorted(p.name for p in cfg.iterdir()) == ["config.json"]
