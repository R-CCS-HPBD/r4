"""``r4 auth logout`` removes every stored copy of the login (stabilization 2026-10-21, connect#1).

``r4 auth login`` syncs the key onto the platform source row of the same
name (and the UMRS ``<profile>.token``). Logout cleared only the auth store
and the token file: the row kept the JWT, config resolution prefers the
active source's key, so every later r4 process and the companion bridge kept
sending ``Bearer <logged-out JWT>`` — and a second logout said "Already
logged out" while the credential stayed live.
"""

from __future__ import annotations

import json

import pytest

LOGIN = "LOGGED-IN-SECRET-TOKEN"
PROVIDER_KEY = "sk-provider-key-kept"

_ENV = ("R4_API_KEY", "R4_GATEWAY_URL", "R4_PROJECT_ID", "R4_PROFILE", "R4_SOURCE", "UMRS_CONFIG")


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    import r4.auth
    import r4.config

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    monkeypatch.setattr("sys.argv", ["r4", "auth", "logout"])
    return root


def _logged_in(root) -> None:
    """The state `r4 auth login --api-key-stdin` leaves behind."""
    from r4.auth import APIKeyManager
    from r4.source import Source, SourceStore, sync_umrs_token

    APIKeyManager().set_api_key(LOGIN, "default")
    store = SourceStore()
    row = Source(name="default", kind="platform", url="https://gw.example", api_key=LOGIN)
    store.add(row)
    store.add(Source(name="pub", kind="public", url="https://p.example/v1", api_key=PROVIDER_KEY))
    store.set_active("default")
    sync_umrs_token(row)


def _logout(capsys) -> str:
    import r4.config
    from r4._cli.logout import LogoutCommand

    r4.config._config = None
    import argparse

    assert LogoutCommand().handle(argparse.Namespace(profile="default")) == 0
    return capsys.readouterr().out


def _every_stored_text(root) -> str:
    return "\n".join(p.read_text() for p in root.rglob("*") if p.is_file())


def test_logout_removes_the_login_from_the_source_row_and_umrs(xdg, capsys):
    _logged_in(xdg)
    out = _logout(capsys)
    assert "Logged out from profile: default" in out
    assert LOGIN not in _every_stored_text(xdg)
    from r4.source import SourceStore

    assert SourceStore().get("default").api_key is None
    assert not (xdg / "umrs" / "connections" / "default.token").exists()


def test_no_request_carries_the_logged_out_login(xdg, capsys):
    _logged_in(xdg)
    _logout(capsys)
    from r4._http import HTTPCore
    from r4.config import Config

    headers = HTTPCore(Config.from_env()).build_headers()
    assert LOGIN not in headers.get("Authorization", "")


def test_a_public_sources_provider_key_is_not_a_login_and_stays(xdg, capsys):
    _logged_in(xdg)
    _logout(capsys)
    rows = json.loads((xdg / "r4" / "sources.json").read_text())["sources"]
    assert {r["name"]: r.get("api_key") for r in rows}["pub"] == PROVIDER_KEY


def test_already_logged_out_only_when_no_copy_is_left(xdg, capsys):
    _logged_in(xdg)
    from r4.auth import APIKeyManager

    # Only the row copy is left (the state the old logout produced).
    APIKeyManager().delete_api_key("default")
    assert "Logged out from profile: default" in _logout(capsys)
    assert "Already logged out" in _logout(capsys)
