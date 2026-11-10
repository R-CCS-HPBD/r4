"""``r4 auth login`` refuses obvious non-credentials (stabilization 2026-10-21, slash-b#0).

The console's ``/login help`` piped the word "help" to ``r4 auth login
--api-key-stdin``; r4 stored it over the real key and the console said
"login ok". A help word, a flag or text with spaces is refused before
anything is written; any other string (opaque gateway API keys have no fixed
shape) is stored as before.
"""

from __future__ import annotations

import io
import json

import pytest

REAL = "REAL-KEY-ALREADY-STORED"

_ENV = ("R4_API_KEY", "R4_GATEWAY_URL", "R4_PROJECT_ID", "R4_PROFILE", "R4_SOURCE")


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
    from r4._cli import login

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    for name in (
        "_verify_identity_via_whoami",
        "_sync_umrs_client_profile",
        "_ensure_umrs_user_config",
        "_try_zmq_session_bootstrap_with_profile",
        "_sync_projects_from_server",
    ):
        monkeypatch.setattr(login, name, lambda *a, **k: None)
    from r4.auth import APIKeyManager

    APIKeyManager().set_api_key(REAL, "default")
    return root / "r4"


def _piped_login(monkeypatch, stdin: str) -> int:
    import argparse

    from r4._cli.auth import AuthCommand
    from r4._cli.login import LoginCommand

    parser = argparse.ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    args = parser.parse_args(
        ["auth", "login", "--api-key-stdin", "--gateway-url", "https://gw.example"]
    )
    return LoginCommand().handle(args)


def _stored_key() -> str | None:
    from r4.auth import APIKeyManager

    return APIKeyManager().get_api_key("default")


@pytest.mark.parametrize(
    "word", ["help", "--help", "-h", "HELP", "?", "usage", "--api-key", "Bearer abc"]
)
def test_a_non_credential_is_refused_and_the_stored_key_kept(xdg, monkeypatch, capsys, word):
    assert _piped_login(monkeypatch, word + "\n") == 1
    err = capsys.readouterr().err
    assert "refusing to store that as a credential" in err
    assert "Nothing was saved" in err
    assert _stored_key() == REAL
    profiles = (
        json.loads((xdg / "config.json").read_text()).get("profiles", {})
        if (xdg / "config.json").exists()
        else {}
    )
    assert word not in json.dumps(profiles)


@pytest.mark.parametrize("key", ["sk-gateway-opaque-0123", "abc.def.ghi", "r4_live_key"])
def test_a_key_only_gateway_api_key_still_logs_in(xdg, monkeypatch, key):
    assert _piped_login(monkeypatch, key + "\n") == 0
    assert _stored_key() == key
