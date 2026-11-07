"""Credential hygiene tests (audit G-1, G-2, G-4).

Pins: key intake prefers env/stdin and warns on the plain flag; credentialed
non-localhost http:// URLs are refused; `r4 source remove` deletes every
credential artifact (profile, .token, auth row) — not just the sources.json row.
"""

from __future__ import annotations

from argparse import Namespace
from io import StringIO

import pytest

from r4._cli.source import (
    _PLAIN_FLAG_WARNING,
    SourceCommand,
    _is_credentialed_http_unsafe,
    _resolve_api_key_material,
)
from r4.source import Source, SourceStore, remove_source_credential_artifacts


def _args(**kwargs) -> Namespace:
    return Namespace(**kwargs)


class TestKeyIntake:
    def test_env_var_wins_and_never_warns(self, monkeypatch):
        monkeypatch.setenv("TEST_R4_KEY", "sk-env-123")
        key, warning, error = _resolve_api_key_material(_args(api_key_env="TEST_R4_KEY"))
        assert key == "sk-env-123"
        assert warning is None and error is None

    def test_empty_env_var_is_an_error(self, monkeypatch):
        monkeypatch.delenv("TEST_R4_KEY", raising=False)
        key, warning, error = _resolve_api_key_material(_args(api_key_env="TEST_R4_KEY"))
        assert key is None
        assert "TEST_R4_KEY" in (error or "")

    def test_stdin_read(self, monkeypatch):
        # StringIO.isatty() is False → the raw-stdin branch, which is what
        # piped keys hit in production scripts.
        monkeypatch.setattr("sys.stdin", StringIO("sk-stdin-123\n"))
        key, warning, error = _resolve_api_key_material(_args(api_key_stdin=True))
        assert key == "sk-stdin-123"
        assert error is None

    def test_empty_stdin_is_an_error(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", StringIO(""))
        key, warning, error = _resolve_api_key_material(_args(api_key_stdin=True))
        assert key is None and error

    def test_plain_flag_warns_but_works(self):
        key, warning, error = _resolve_api_key_material(_args(api_key="sk-plain"))
        assert key == "sk-plain"
        assert warning == _PLAIN_FLAG_WARNING

    def test_no_key_means_no_tuple_payload(self):
        assert _resolve_api_key_material(_args()) == (None, None, None)


class TestCredentialedHttpGuard:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://api.openai.com", True),
            ("http://10.0.0.4:8080", True),
            ("http://localhost:4000", False),
            ("http://127.0.0.1:4000", False),
            ("http://[::1]:4000", False),
            ("https://api.openai.com", False),
            ("api.openai.com", False),
        ],
    )
    def test_classifier(self, url: str, expected: bool) -> None:
        assert _is_credentialed_http_unsafe(url) is expected

    def test_add_refuses_key_over_remote_http(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("TEST_R4_KEY", "sk-x")
        monkeypatch.setattr("sys.stdin", StringIO("y\n"))
        args = _args(
            name="bad",
            url="http://api.openai.com",
            kind="public",
            api_key_env="TEST_R4_KEY",
            allow_insecure_http=False,
        )
        assert SourceCommand()._handle_add(args) == 1
        assert "refusing to store a credential" in capsys.readouterr().err

    def test_allow_insecure_flag_overrides_with_intent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("TEST_R4_KEY", "sk-x")
        monkeypatch.setattr("sys.stdin", StringIO("y\n"))
        args = _args(
            name="bad-http",
            url="http://api.openai.com",
            kind="public",
            api_key_env="TEST_R4_KEY",
            allow_insecure_http=True,
        )
        assert SourceCommand()._handle_add(args) == 0


class TestRemoveCleanup:
    """`r4 source remove` deletes every local credential artifact — and only
    local files: no AuthManager.logout(), which revoked the ACTIVE
    deployment's CURVE session over the network (onboarding plan D-8,
    2026-10-15)."""

    @staticmethod
    def _no_logout(monkeypatch) -> None:
        def refuse(_name):
            raise AssertionError("removal must not call AuthManager.logout (network revoke)")

        monkeypatch.setattr("r4.auth.get_auth_manager", refuse)

    def test_remove_deletes_profile_token_and_auth(self, tmp_path, monkeypatch):
        from r4.auth import APIKeyManager, TokenInfo, TokenStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        for name in ("R4_PROFILE", "R4_API_KEY", "R4_API_KEY_GONE"):
            monkeypatch.delenv(name, raising=False)
        store = SourceStore()
        store.add(Source(name="gone", kind="platform", url="https://h", api_key="sk-x"))
        connections = tmp_path / "umrs" / "connections"
        connections.mkdir(parents=True)
        (connections / "gone.json").write_text("{}")
        (connections / "gone.token").write_text("sk-x\n")
        APIKeyManager().set_api_key("sk-login", "gone")
        TokenStore().save(TokenInfo(access_token="tok-of-gone"), "gone")
        self._no_logout(monkeypatch)

        report = remove_source_credential_artifacts("gone", "https://h")

        assert report == {"profile": True, "token": True, "auth": True, "session": False}
        assert not (connections / "gone.json").exists()
        assert not (connections / "gone.token").exists()
        assert TokenStore().load("gone") is None
        assert APIKeyManager().get_api_key("gone") is None

    def test_cleanup_is_honest_about_absence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("R4_PROFILE", raising=False)
        (tmp_path / "umrs" / "connections").mkdir(parents=True)
        self._no_logout(monkeypatch)
        report = remove_source_credential_artifacts("nope")
        assert report == {"profile": False, "token": False, "auth": False, "session": False}
