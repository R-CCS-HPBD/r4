"""``r4 source`` and ``r4 auth login`` at the deployment boundary (2026-10-15).

Onboarding plan decision D-8 and Phase 4:

- the source names ``site`` and ``local`` are r4's own (``source add`` and
  ``source add-ollama`` refuse them, in any letter case, before reading a key);
- ``source push`` refuses a platform source (a deployment login) and a
  loopback source (however its URL spells this machine) before any network
  call — the old code posted the key to the active gateway first;
- ``source remove`` never contacts a gateway (no unpush) for a platform or
  loopback source, which can never have been pushed;
- sources gain ``default_model`` and ``project`` (Phase 4-C), ``source add``
  gains ``--default-model``, ``--project`` and ``--replace``, and
  ``source list --json`` shows the two fields (never the key);
- ``r4 auth login --api-key-stdin`` reads the token from a pipe and never
  echoes it.
"""

from __future__ import annotations

import io
import json
from argparse import ArgumentParser, Namespace
from unittest.mock import MagicMock, patch

import pytest

from r4._cli.source import SourceCommand
from r4.source import Source, SourceStore

_ADD_DEFAULTS = {
    "kind": "platform",
    "api_key": None,
    "api_key_env": None,
    "api_key_stdin": False,
    "allow_insecure_http": False,
    "provider": None,
    "aws_region": None,
    "aws_profile": None,
    "description": None,
    "zmq_host": None,
    "zmq_port": None,
    "zmq_tunnel": False,
    "default_model": None,
    "project": None,
    "replace": False,
}


@pytest.fixture
def store(tmp_path, monkeypatch) -> SourceStore:
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("R4_API_KEY", "R4_SOURCE", "R4_PROFILE", "UMRS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    return SourceStore()


def _add_args(name: str, url: str, **overrides) -> Namespace:
    return Namespace(name=name, url=url, **{**_ADD_DEFAULTS, **overrides})


# --------------------------------------------------------- reserved names ---


@pytest.mark.parametrize("name", ["site", "local", "SITE", "Local", " local "])
def test_source_add_refuses_reserved_names_before_reading_a_key(name, store, monkeypatch, capsys):
    stdin = io.StringIO("never-read\n")
    monkeypatch.setattr("sys.stdin", stdin)
    args = _add_args(name, "http://localhost:8080", api_key_stdin=True)
    assert SourceCommand()._handle_add(args) == 1
    assert "reserved source name" in capsys.readouterr().err
    assert store.list() == []
    assert stdin.tell() == 0


def test_add_ollama_refuses_a_reserved_name(store, capsys):
    args = Namespace(name="local", url="http://localhost:11434", description=None, use=False)
    assert SourceCommand()._handle_add_ollama(args) == 1
    assert "reserved source name" in capsys.readouterr().err
    assert store.list() == []


def test_names_that_merely_contain_a_reserved_word_are_fine(store):
    assert SourceCommand()._handle_add(_add_args("local-stack", "http://localhost:8080")) == 0
    assert store.get("local-stack") is not None


# ------------------------------------------------------------------- push ---

LOOPBACK_PUSH_URLS = (
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "https://127.1:8443",
    "https://127.0.0.2",
    "https://[::1]:8443",
    "https://localhost.:8443",
    "https://LOCALHOST",
    "https://[::ffff:127.0.0.1]",
    "https://0.0.0.0:8443",
    "https://2130706433",
)


def _push(name: str):
    sealed = MagicMock()
    with patch("r4.user_sources.UserSourcesClient", return_value=sealed) as factory:
        code = SourceCommand()._handle_push(
            Namespace(name=name, allow_paths="", yes=True, as_json=False)
        )
    return code, factory, sealed


@pytest.mark.parametrize("url", LOOPBACK_PUSH_URLS)
def test_push_refuses_a_loopback_source_before_any_network_call(url, store, capsys):
    store.add(Source(name="here", kind="public", url=url, api_key="sk-local"))
    code, factory, sealed = _push("here")
    assert code == 1
    assert "loopback" in capsys.readouterr().err
    factory.assert_not_called()
    sealed.push.assert_not_called()


def test_push_refuses_a_platform_source_before_any_network_call(store, capsys):
    store.add(Source(name="rccs", kind="platform", url="https://gw.example/rccs", api_key="jwt"))
    code, factory, sealed = _push("rccs")
    assert code == 1
    assert "platform source" in capsys.readouterr().err
    factory.assert_not_called()
    sealed.push.assert_not_called()


def test_push_of_a_remote_public_source_still_goes_ahead(store):
    store.add(Source(name="oai", kind="public", url="https://api.example.com/v1", api_key="sk"))
    code, factory, sealed = _push("oai")
    assert code == 0
    sealed.push.assert_called_once()


# ----------------------------------------------------------------- remove ---


def _remove(name: str, monkeypatch):
    monkeypatch.setattr("r4.auth.get_auth_manager", lambda _name: MagicMock(logout=lambda: False))
    sealed = MagicMock()
    sealed.unpush.return_value = {"leases_revoked": 0}
    with patch("r4.user_sources.UserSourcesClient", return_value=sealed) as factory:
        code = SourceCommand()._handle_remove(Namespace(name=name))
    return code, factory, sealed


@pytest.mark.parametrize(
    ("kind", "url"),
    [
        ("platform", "https://gw.example/rccs"),
        ("platform", "http://localhost:8080"),
        ("public", "http://localhost:11434"),
        ("public", "https://[::1]:8443"),
    ],
)
def test_remove_never_contacts_a_gateway_for_a_never_pushed_source(kind, url, store, monkeypatch):
    store.add(Source(name="gone", kind=kind, url=url))
    code, factory, sealed = _remove("gone", monkeypatch)
    assert code == 0
    assert store.get("gone") is None
    factory.assert_not_called()
    sealed.unpush.assert_not_called()


def test_remove_of_a_remote_public_source_still_unpushes(store, monkeypatch):
    store.add(Source(name="oai", kind="public", url="https://api.example.com/v1"))
    code, _factory, sealed = _remove("oai", monkeypatch)
    assert code == 0
    sealed.unpush.assert_called_once_with("oai")


# ---------------------------------------------------- default model, project ---


def test_sources_round_trip_the_new_fields(store):
    store.add(
        Source(
            name="stand",
            kind="platform",
            url="http://localhost:8080",
            default_model="llama3.2:3b",
            project="default",
        )
    )
    loaded = SourceStore().get("stand")
    assert (loaded.default_model, loaded.project) == ("llama3.2:3b", "default")


def test_a_registry_written_before_the_new_fields_loads(store):
    legacy = {
        "sources": [{"name": "rccs", "kind": "platform", "url": "https://gw/rccs"}],
        "active": "rccs",
    }
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text(json.dumps(legacy))
    loaded = store.get("rccs")
    assert (loaded.default_model, loaded.project) == (None, None)
    assert "default_model" not in loaded.to_dict()


def test_source_add_records_default_model_and_project(store):
    args = _add_args(
        "stand", "http://localhost:8080", default_model="llama3.2:3b", project="default"
    )
    assert SourceCommand()._handle_add(args) == 0
    src = store.get("stand")
    assert (src.default_model, src.project) == ("llama3.2:3b", "default")


def test_source_list_json_shows_the_new_fields_and_never_the_key(store, capsys):
    store.add(
        Source(
            name="stand",
            kind="platform",
            url="http://localhost:8080",
            api_key="SECRET-JWT",
            default_model="llama3.2:3b",
            project="default",
        )
    )
    assert SourceCommand()._handle_list(Namespace(as_json=True, remote=False)) == 0
    out = capsys.readouterr().out
    (row,) = json.loads(out)
    assert (row["default_model"], row["project"], row["has_api_key"]) == (
        "llama3.2:3b",
        "default",
        True,
    )
    assert "SECRET-JWT" not in out


# ---------------------------------------------------------------- replace ---


def _seed_old_row(store: SourceStore, tmp_umrs) -> None:
    from r4.auth import TokenInfo, TokenStore

    store.add(
        Source(
            name="stand",
            kind="platform",
            url="https://old.example",
            api_key="OLD-KEY",
            description="old stack",
            zmq_host="zmq.old.example",
        )
    )
    TokenStore().save(TokenInfo(access_token="OLD-LOGIN"), "stand")
    tmp_umrs.mkdir(parents=True, exist_ok=True)
    (tmp_umrs / "stand.token").write_text("OLD-KEY\n")
    (tmp_umrs / "stand.json").write_text(json.dumps({"name": "stand", "username": "old-user"}))


def test_replace_drops_the_old_row_and_its_stored_credentials(store, tmp_path):
    from r4.auth import TokenStore
    from r4.source_context import credential_for

    connections = tmp_path / "xdg" / "umrs" / "connections"
    _seed_old_row(store, connections)
    args = _add_args(
        "stand", "http://localhost:8080", replace=True, default_model="m", project="default"
    )
    assert SourceCommand()._handle_add(args) == 0
    src = store.get("stand")
    assert (src.url, src.api_key, src.description, src.zmq_host) == (
        "http://localhost:8080",
        None,
        None,
        None,
    )
    assert TokenStore().load("stand") is None
    assert not (connections / "stand.token").exists()
    assert "old-user" not in (connections / "stand.json").read_text()
    assert credential_for(src) is None


def test_replace_with_a_new_key_from_stdin(store, tmp_path, monkeypatch):
    connections = tmp_path / "xdg" / "umrs" / "connections"
    _seed_old_row(store, connections)
    monkeypatch.setattr("sys.stdin", io.StringIO("NEW-KEY\n"))
    args = _add_args("stand", "http://localhost:8080", replace=True, api_key_stdin=True)
    assert SourceCommand()._handle_add(args) == 0
    assert store.get("stand").api_key == "NEW-KEY"
    assert (connections / "stand.token").read_text() == "NEW-KEY\n"


def test_replace_never_touches_the_profiles_own_login(store, monkeypatch):
    from r4.auth import TokenInfo, TokenStore

    TokenStore().save(TokenInfo(access_token="MY-LOGIN"), "default")
    store.add(Source(name="default", kind="platform", url="https://old.example"))
    assert (
        SourceCommand()._handle_add(_add_args("default", "https://new.example", replace=True)) == 0
    )
    assert TokenStore().load("default").access_token == "MY-LOGIN"


def test_without_replace_an_existing_name_is_updated_as_before(store, capsys):
    store.add(Source(name="stand", kind="platform", url="https://old.example"))
    assert SourceCommand()._handle_add(_add_args("stand", "http://localhost:8080")) == 0
    assert "Updated source 'stand'" in capsys.readouterr().out
    assert store.get("stand").url == "http://localhost:8080"


def test_the_registry_is_written_atomically_with_owner_only_mode(store):
    store.add(Source(name="x", kind="public", url="https://api.example.com", api_key="k"))
    assert store._path.stat().st_mode & 0o777 == 0o600
    assert not [p for p in store._path.parent.iterdir() if p.name.endswith(".tmp")]


# ------------------------------------------------- the --source note, model ---


def test_source_flag_applies_the_default_model_and_project(store, monkeypatch):
    import r4.config
    from r4 import cli
    from r4._http import HTTPCore
    from r4.config import get_config

    monkeypatch.delenv("R4_MODEL", raising=False)
    monkeypatch.delenv("R4_PROJECT_ID", raising=False)
    monkeypatch.setattr(r4.config, "_config", None)
    store.add(
        Source(
            name="stand",
            kind="platform",
            url="http://localhost:8080",
            api_key="K",
            default_model="llama3.2:3b",
            project="default",
        )
    )
    cli._apply_tui_source_override("stand")
    assert get_config().model == "llama3.2:3b"
    assert HTTPCore(get_config()).build_headers()["X-Project-ID"] == "default"


def test_source_flag_without_a_credential_says_how_to_log_in(store, monkeypatch, capsys):
    import r4.config
    from r4 import cli

    monkeypatch.setattr(r4.config, "_config", None)
    store.add(Source(name="far", kind="platform", url="https://far.example", project="p"))
    cli._apply_tui_source_override("far")
    err = capsys.readouterr().err
    assert "has no credential of its own" in err
    # r4 source set keeps every other field of the row (2026-10-15 fix round).
    assert "r4 source set far --api-key-stdin" in err


# ------------------------------------------------- r4 auth login from stdin ---


def _auth_parser() -> ArgumentParser:
    from r4._cli.auth import AuthCommand

    parser = ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    return parser


def _quiet_login_side_effects(monkeypatch) -> None:
    from r4._cli import login

    for name in (
        "_verify_identity_via_whoami",
        "_sync_umrs_client_profile",
        "_ensure_umrs_user_config",
        "_try_zmq_session_bootstrap_with_profile",
    ):
        monkeypatch.setattr(login, name, lambda *a, **k: None)


def test_auth_login_reads_the_token_from_stdin_and_never_echoes_it(store, monkeypatch, capsys):
    import r4.auth
    import r4.config
    from r4._cli.login import LoginCommand
    from r4.auth import APIKeyManager

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    _quiet_login_side_effects(monkeypatch)
    token = "opaque-site-token-1234567890"
    monkeypatch.setattr("sys.stdin", io.StringIO(token + "\n"))
    args = _auth_parser().parse_args(
        [
            "auth",
            "login",
            "--api-key-stdin",
            "--gateway-url",
            "https://a.example",
            "--profile",
            "p1",
        ]
    )
    assert LoginCommand().handle(args) == 0
    assert APIKeyManager().get_api_key("p1") == token
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


def test_auth_login_stdin_with_no_token_is_an_error(store, monkeypatch, capsys):
    from r4._cli.login import LoginCommand

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    args = _auth_parser().parse_args(["auth", "login", "--api-key-stdin", "--profile", "p1"])
    assert LoginCommand().handle(args) == 1
    assert "empty API key on stdin" in capsys.readouterr().err


def test_auth_login_plain_api_key_flag_warns_like_source_add(store, monkeypatch, capsys):
    import r4.auth
    import r4.config
    from r4._cli.login import LoginCommand
    from r4._cli.source import _PLAIN_FLAG_WARNING

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    _quiet_login_side_effects(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    args = _auth_parser().parse_args(
        ["auth", "login", "--api-key", "tok-123", "--gateway-url", "https://a.example"]
    )
    assert LoginCommand().handle(args) == 0
    assert _PLAIN_FLAG_WARNING in capsys.readouterr().err


def test_a_failed_registry_write_keeps_the_previous_registry(store, monkeypatch):
    store.add(Source(name="kept", kind="public", url="https://api.example.com"))
    before = store._path.read_text()

    def crash(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("r4.source.os.replace", crash)
    with pytest.raises(OSError):
        store.add(Source(name="new", kind="public", url="https://other.example.com"))
    assert store._path.read_text() == before
    assert not [p for p in store._path.parent.iterdir() if p.name.endswith(".tmp")]


# -------------------------------------------- --source: companion, preflight ---


def test_companion_source_flag_resolves_the_named_source(store):
    from r4._cli.companion import _resolve_companion_source

    store.add(Source(name="far", kind="platform", url="https://far.example"))
    assert _resolve_companion_source(Namespace(source="far")).name == "far"


def test_preflight_refuses_a_source_without_its_own_credential(store, monkeypatch, capsys):
    import r4.auth
    import r4.config
    from r4 import cli
    from r4._cli import _helpers

    profile = {"gateway_url": "https://a.example", "api_key": "A-PROFILE-KEY"}
    (store._path.parent / "config.json").write_text(json.dumps({"profiles": {"default": profile}}))
    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    monkeypatch.setattr(_helpers, "_preflight_gateway", lambda config, action: None)
    store.add(Source(name="far", kind="platform", url="https://far.example"))
    cli._apply_tui_source_override("far")
    with pytest.raises(SystemExit):
        _helpers._preflight_check(action="chat")
    err = capsys.readouterr().err
    assert "No credential for source 'far'" in err
    assert "r4 source set far --api-key-stdin" in err


# ---- `r4 source add`: a --url r4 could never call is refused (c2-cli) ----


@pytest.mark.parametrize("url", ["notaurl", "ftp://x", "http://", "localhost:8080"])
def test_source_add_refuses_an_unusable_url_before_reading_a_key(url, store, monkeypatch, capsys):
    stdin = io.StringIO("never-read\n")
    monkeypatch.setattr("sys.stdin", stdin)
    args = _add_args("bad", url, kind="public", api_key_stdin=True)

    assert SourceCommand()._handle_add(args) == 1
    assert "--url must be an http:// or https:// URL" in capsys.readouterr().err
    assert store.list() == []
    assert stdin.tell() == 0


def test_add_ollama_refuses_an_unusable_url(store, capsys):
    args = Namespace(name="ollama", url="notaurl", description=None, use=False)

    assert SourceCommand()._handle_add_ollama(args) == 1
    assert store.list() == []


def test_source_add_still_takes_http_and_https(store):
    assert (
        SourceCommand()._handle_add(_add_args("a", "https://api.example.com/v1", kind="public"))
        == 0
    )
    assert (
        SourceCommand()._handle_add(_add_args("b", "http://127.0.0.1:11434/v1", kind="public")) == 0
    )
    assert {s.name for s in store.list()} == {"a", "b"}
