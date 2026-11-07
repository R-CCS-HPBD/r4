"""Secret files are 0600 from their first byte (cycle-2 security lane, 2026-10-21).

``config.json`` (profile API keys), ``<source>.token`` and ``s3cfg_<user>``
were written with ``write_text`` at the umask mode (0644 under umask 022) and
only then ``chmod 0600``: between the two, any local user could read the key
(shared login nodes). The writes were not atomic either: a crash mid-write
truncated ``config.json`` and the next save dropped every profile.
Repro: $C2/security/creds/t01_modes.sh (instr_run.py stats the file at chmod).

ProfileConfigStore.save and sync_umrs_token are fixed (with tests) by the cli
lane (wave/c2-cli 25c6e926, test_cli_c2_credential_writes.py); this file
covers the two writers that lane did not touch.
"""

from __future__ import annotations

import os
import pathlib
import stat

import pytest

SECRET = "secret-key-material-0123456789"


@pytest.fixture
def world_readable_writes(tmp_path, monkeypatch):
    """Record every write_text that left SECRET readable by group/other."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("UMRS_CONFIG", raising=False)
    old_umask = os.umask(0o022)
    exposed: list[str] = []
    original = pathlib.Path.write_text

    def spy(self, data, *args, **kwargs):
        written = original(self, data, *args, **kwargs)
        if SECRET in data and stat.S_IMODE(self.stat().st_mode) & 0o077:
            exposed.append(f"{self.name} {oct(stat.S_IMODE(self.stat().st_mode))}")
        return written

    monkeypatch.setattr(pathlib.Path, "write_text", spy)
    yield exposed
    os.umask(old_umask)


def _mode(path: pathlib.Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_api_key_config_json(tmp_path, world_readable_writes):
    from r4.auth import APIKeyManager

    APIKeyManager().set_api_key(SECRET, "default")
    assert world_readable_writes == []
    assert _mode(tmp_path / "xdg" / "r4" / "config.json") == 0o600


def test_s3cfg(tmp_path, world_readable_writes):
    from r4._cli.login import _write_file_secure

    target = tmp_path / "xdg" / "umrs" / "s3cfg_alice"
    _write_file_secure(target.parent, target, f"secret_key = {SECRET}\n", "")
    assert world_readable_writes == []
    assert _mode(target) == 0o600
