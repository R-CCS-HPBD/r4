"""The deployment boundary in ``r4 source`` (onboarding plan 2026-10-15, D-8; fix round 1).

- ``source remove`` contacts no gateway at all for a platform or loopback
  source: no unpush, and no CURVE-session revoke (``AuthManager.logout()``
  used to DELETE the ACTIVE session at ``UMRS_BASE_URL`` with the removed
  source's credential and delete the active ``curve-session.json``);
- the CURVE session file a bound transport keeps per source goes with the
  row on ``remove``, ``add --replace``, a re-point and a new key;
- a re-pointed source never inherits the login stored under its name, and a
  stored login counts only when it records the source's deployment;
- a login re-sync keeps every field of the row (``default_model``,
  ``project``, OIDC, CURVE, tunnel) and never writes a reserved name; the
  reservation holds for every writer (``SourceStore.add``);
- the printed fix for a source with no credential is a command that works
  and keeps the row (``r4 source set``), including for http:// LAN and
  tunnel sources, and the model-probe hint is a valid command;
- loopback detection reads the IDNA full stops (``127。0。0。1``) as httpx
  does, and distinct loopback addresses are distinct deployments;
- ``source push`` refuses a public row that is really a deployment login;
- recovery hints never put a token in argv.
"""

from __future__ import annotations

import io
import json
import shlex
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from r4._cli.source import SourceCommand
from r4.source import Source, SourceStore

A_URL = "https://a.example"
B_URL = "https://b.example"

_ENV = (
    "R4_API_KEY",
    "R4_API_KEY_B",
    "R4_GATEWAY_URL",
    "R4_PROJECT_ID",
    "R4_PROFILE",
    "R4_SOURCE",
    "R4_MODEL",
    "UMRS_BASE_URL",
    "UMRS_CONFIG",
    "UMRS_SERVER_HOST",
    "UMRS_CONTROL_PORT",
    "UMRS_TUNNEL_MODE",
)


@pytest.fixture
def store(tmp_path, monkeypatch) -> SourceStore:
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
    return SourceStore()


def _cfg_dir() -> Path:
    import os

    return Path(os.environ["XDG_CONFIG_HOME"]) / "r4"


def _session_file(url: str | None = None) -> Path:
    from r4.curve_session import session_file_name_for

    name = session_file_name_for(url) if url else "curve-session.json"
    return _cfg_dir() / name


def _write_session(path: Path, session_id: str) -> None:
    import time

    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "client_public_key": "pub",
                "client_secret_key": "secret",
                "server_public_key": "srv",
                "expires_at": time.time() + 3600,
            }
        )
    )


# ---------------------------------------------------------------- remove ---


@pytest.mark.parametrize(
    ("name", "kind", "url"),
    [
        ("b", "platform", B_URL),
        ("local-stack", "platform", "http://localhost:8080"),
        ("ollama", "public", "http://127.0.0.1:11434/v1"),
    ],
)
def test_remove_contacts_no_gateway_and_keeps_the_active_session(
    name, kind, url, store, monkeypatch
):
    """A0-A-R4 / A1-A-R1 / A2-A-R2-1: no revoke of the ACTIVE CURVE session."""
    from r4.auth import APIKeyManager, TokenInfo, TokenStore
    from r4.curve_session import CurveSessionManager

    store.add(Source(name="a", kind="platform", url=A_URL, api_key="JWT-OF-A"))
    store.set_active("a")
    store.add(Source(name=name, kind=kind, url=url))
    monkeypatch.setenv("UMRS_BASE_URL", A_URL)
    monkeypatch.setenv("R4_API_KEY", "ENV.KEY.OFACTIVE")
    TokenStore().save(TokenInfo(access_token="JWT.OF.SOURCE"), name)
    APIKeyManager().set_api_key("KEY-OF-SOURCE", name)
    _write_session(_session_file(), "ACTIVE-SESSION-OF-A")
    deletes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        CurveSessionManager,
        "_http_delete",
        staticmethod(lambda url, *, jwt_token: deletes.append((url, jwt_token))),
    )

    def no_network(*_a: Any, **_k: Any) -> None:
        raise AssertionError("source remove must not open a connection")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    with patch("r4.user_sources.UserSourcesClient", side_effect=no_network):
        assert SourceCommand()._handle_remove(Namespace(name=name)) == 0
    assert deletes == []
    assert _session_file().exists()
    assert TokenStore().load(name) is None
    import os

    os.environ.pop("R4_API_KEY")
    assert APIKeyManager().get_api_key(name) is None


def test_remove_keeps_the_login_of_the_profile_r4_runs_under(store):
    from r4.auth import TokenInfo, TokenStore

    TokenStore().save(TokenInfo(access_token="MY-LOGIN"), "default")
    store.add(Source(name="default", kind="platform", url=A_URL))
    assert SourceCommand()._handle_remove(Namespace(name="default")) == 0
    assert TokenStore().load("default").access_token == "MY-LOGIN"


# ------------------------------------------------- per-source session files ---


def _add_args(name: str, url: str, **overrides: Any) -> Namespace:
    defaults = {
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
    return Namespace(name=name, url=url, **{**defaults, **overrides})


def test_replace_drops_the_old_rows_curve_session(store, monkeypatch):
    """A1-A-R2: a replaced row never rides the old row's CURVE session."""
    store.add(Source(name="x", kind="platform", url="https://x.example", api_key="old.alice.jwt"))
    _write_session(_session_file("https://x.example"), "sess-of-alice")
    monkeypatch.setattr("sys.stdin", io.StringIO("new.bob.jwt\n"))
    args = _add_args("x", "https://x.example", replace=True, api_key_stdin=True)
    assert SourceCommand()._handle_add(args) == 0
    assert not _session_file("https://x.example").exists()


def test_remove_drops_the_sources_curve_session(store):
    store.add(Source(name="x", kind="platform", url="https://x.example"))
    _write_session(_session_file("https://x.example"), "sess-of-x")
    assert SourceCommand()._handle_remove(Namespace(name="x")) == 0
    assert not _session_file("https://x.example").exists()


def test_a_repoint_drops_the_stored_login_and_session(store):
    """A0-A-R5: `source add b --url NEW` (no --replace) keeps nothing of the old URL."""
    from r4.auth import APIKeyManager
    from r4.source_context import credential_for

    store.add(Source(name="b", kind="platform", url=B_URL))
    APIKeyManager().set_api_key("B-LOGIN-SECRET", "b")
    _write_session(_session_file(B_URL), "sess-of-b")
    assert SourceCommand()._handle_add(_add_args("b", "https://new-host.example")) == 0
    assert credential_for(store.get("b")) is None
    assert APIKeyManager().get_api_key("b") is None
    assert not _session_file(B_URL).exists()


def test_a_login_that_records_no_gateway_is_no_sources_login(store):
    """A0-A-R5: `printf K | r4 auth login --profile b` used to record nothing."""
    from r4.auth import APIKeyManager
    from r4.config import ProfileConfigStore
    from r4.source_context import credential_for, stored_login

    store.add(Source(name="b", kind="platform", url=B_URL))
    APIKeyManager().set_api_key("B-LOGIN-SECRET", "b")
    assert stored_login("b", B_URL) is None
    ProfileConfigStore().save("b", login_gateway_url=B_URL)
    assert credential_for(store.get("b")) == "B-LOGIN-SECRET"
    assert stored_login("b", "https://new-host.example") is None


# ----------------------------------------------------------- source set ---


def _tunnel_lan_source() -> Source:
    return Source(
        name="far",
        kind="platform",
        url="http://10.0.0.31:8080",
        zmq_host="10.0.0.31",
        zmq_control_port=6100,
        zmq_tunnel_mode=True,
        description="login node",
        default_model="m-1",
        project="p-1",
        zmq_auth_type="curve",
        oidc_issuer="https://issuer.example",
    )


def _run_hint(hint: str, key: str, monkeypatch) -> int:
    """Run the `r4 source ...` command a hint prints, with the key on stdin."""
    command = hint.rsplit("| ", 1)[1]
    argv = shlex.split(command)
    assert argv[0] == "r4"
    parser = ArgumentParser(prog="r4")
    SourceCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(key + "\n"))
    return SourceCommand().handle(parser.parse_args(argv[1:]))


def test_the_login_hint_works_and_keeps_every_field(store, monkeypatch):
    """A0-A-R7 / A2-A-R2-6: an http:// LAN tunnel source, as printed."""
    from r4.source_context import login_hint

    store.add(_tunnel_lan_source())
    assert _run_hint(login_hint(store.get("far")), "FAR-KEY", monkeypatch) == 0
    row = store.get("far")
    assert row.api_key == "FAR-KEY"
    assert (row.zmq_tunnel_mode, row.description, row.zmq_host, row.zmq_control_port) == (
        True,
        "login node",
        "10.0.0.31",
        6100,
    )
    assert (row.default_model, row.project, row.zmq_auth_type, row.oidc_issuer) == (
        "m-1",
        "p-1",
        "curve",
        "https://issuer.example",
    )


def test_source_set_changes_only_what_it_names(store, capsys):
    store.add(_tunnel_lan_source())
    args = Namespace(
        source_command="set",
        name="far",
        api_key=None,
        api_key_env=None,
        api_key_stdin=False,
        allow_insecure_http=False,
        default_model="m-2",
        project=None,
    )
    assert SourceCommand().handle(args) == 0
    row = store.get("far")
    assert (row.default_model, row.project, row.zmq_tunnel_mode) == ("m-2", "p-1", True)
    assert "every other field kept" in capsys.readouterr().out


def test_source_set_keeps_the_g4_check_and_never_prints_the_key(store, monkeypatch, capsys):
    store.add(_tunnel_lan_source())
    monkeypatch.setattr("sys.stdin", io.StringIO("SECRET-KEY\n"))
    args = Namespace(
        source_command="set",
        name="far",
        api_key=None,
        api_key_env=None,
        api_key_stdin=True,
        allow_insecure_http=False,
        default_model=None,
        project=None,
    )
    assert SourceCommand().handle(args) == 1
    captured = capsys.readouterr()
    assert "G-4" in captured.err and "SECRET-KEY" not in captured.out + captured.err
    assert store.get("far").api_key is None


def test_source_set_refuses_an_unknown_name_and_an_empty_change(store, capsys):
    base = Namespace(
        source_command="set",
        api_key=None,
        api_key_env=None,
        api_key_stdin=False,
        allow_insecure_http=False,
        default_model=None,
        project=None,
    )
    assert SourceCommand().handle(Namespace(**{**vars(base), "name": "nope"})) == 1
    store.add(Source(name="x", kind="platform", url=A_URL))
    assert SourceCommand().handle(Namespace(**{**vars(base), "name": "x"})) == 1
    assert "nothing to set" in capsys.readouterr().err


def test_a_new_key_drops_the_sources_curve_session(store, monkeypatch):
    store.add(Source(name="x", kind="platform", url="https://x.example", api_key="old.jwt.x"))
    _write_session(_session_file("https://x.example"), "sess-old")
    monkeypatch.setattr("sys.stdin", io.StringIO("new.jwt.x\n"))
    args = Namespace(
        source_command="set",
        name="x",
        api_key=None,
        api_key_env=None,
        api_key_stdin=True,
        allow_insecure_http=False,
        default_model=None,
        project=None,
    )
    assert SourceCommand().handle(args) == 0
    assert not _session_file("https://x.example").exists()


# ------------------------------------------------- probe and listing hints ---


def _cli_parser() -> ArgumentParser:
    from r4.cli import _build_parser

    return _build_parser()


def test_the_model_probe_hint_is_a_valid_command(store, monkeypatch):
    """A0-A-R8 / A2-A-R2-3(1): `r4 auth login --source b` does not exist."""
    from r4.errors import AuthenticationError
    from r4.model_routing import _probe_error_message

    src = Source(name="b", kind="platform", url=B_URL)
    message = _probe_error_message(src, AuthenticationError(status_code=401))
    command = message.rsplit("| ", 1)[1]
    argv = shlex.split(command)
    args = _cli_parser().parse_args(argv[1:])
    assert (args.command, args.source_command, args.name) == ("source", "set", "b")


@pytest.fixture
def refusing(monkeypatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"detail": "no credential"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    class _Refusing(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _Refusing)
    return seen


def _profile_for_a() -> None:
    profile = {"gateway_url": A_URL, "api_key": "A-KEY", "project_id": "proj-a"}
    (_cfg_dir() / "config.json").write_text(json.dumps({"profiles": {"default": profile}}))


def test_auth_status_for_a_keyless_bound_source_names_the_source_fix(
    store, refusing, monkeypatch, capsys
):
    """A2-A-R2-3(2): not `r4 auth login --api-key <token>`, which replaces A's login."""
    from r4 import cli

    _profile_for_a()
    store.add(Source(name="site-a", kind="platform", url=A_URL))
    store.add(Source(name="b", kind="platform", url=B_URL))
    store.set_active("b")
    with pytest.raises(SystemExit):
        cli._resolve_auth_command(Namespace(auth_command="status", profile="default"))
    out = capsys.readouterr().out
    assert "r4 source set b --api-key-stdin" in out
    assert "--api-key <" not in out


def test_model_listing_across_sources_says_how_to_fix_a_source(store, refusing, capsys):
    """A2-A-R2-3(3): the per-source [warn] row carries the source's own fix."""
    from r4._cli._helpers import _available_models_all_sources

    _profile_for_a()
    store.add(Source(name="site-a", kind="platform", url=A_URL))
    store.add(Source(name="b", kind="platform", url=B_URL))
    store.set_active("site-a")
    with pytest.raises(SystemExit):
        _available_models_all_sources([store.get("b")], None)
    assert "r4 source set b --api-key-stdin" in capsys.readouterr().err


def test_endpoint_listing_error_row_carries_the_source_fix(store, refusing):
    from r4._cli.endpoint import _list_endpoints_for_source
    from r4.config import get_config

    _profile_for_a()
    store.add(Source(name="site-a", kind="platform", url=A_URL))
    store.add(Source(name="b", kind="platform", url=B_URL))
    store.set_active("site-a")
    _rows, error = _list_endpoints_for_source(store.get("b"), Namespace(all=False), get_config())
    assert "r4 source set b --api-key-stdin" in error["hint"]


# -------------------------------------------------- login sync, reservation ---


def _rich_row(name: str, url: str) -> Source:
    return Source(
        name=name,
        kind="platform",
        url=url,
        api_key="K1",
        default_model="m-1",
        project="p-1",
        zmq_tunnel_mode=True,
        zmq_auth_type="curve",
        oidc_issuer="https://issuer.example",
        oidc_client_id="cid",
    )


@pytest.mark.parametrize("login_profile", ["s", "default"], ids=["by-name", "by-url"])
def test_a_login_sync_keeps_every_field_of_the_row(store, login_profile):
    """A0-A-R6 / A1-A-R8 / A2-A-R2-5: `r4 auth login` / `r4 onboard` re-sync."""
    from r4._cli.onboard import _sync_onboard_source

    store.add(_rich_row("s", "https://s.example"))
    _sync_onboard_source(login_profile, "https://s.example", "K2")
    row = store.get("s")
    assert row.api_key == "K2"
    assert (row.default_model, row.project, row.zmq_tunnel_mode) == ("m-1", "p-1", True)
    assert (row.zmq_auth_type, row.oidc_issuer, row.oidc_client_id) == (
        "curve",
        "https://issuer.example",
        "cid",
    )


@pytest.mark.parametrize("name", ["local", "site", "LOCAL"])
def test_a_login_never_writes_a_reserved_source_name(store, name, capsys):
    from r4._cli.onboard import _sync_onboard_source

    _sync_onboard_source(name, "http://localhost:9999", "k")
    assert store.list() == []
    assert store.get_active() is None
    assert "reserved source name" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["local", "site", "Site"])
def test_every_writer_is_refused_a_reserved_name(store, name):
    """A0-A-R10: the companion `source_add` tool wrote the reserved names."""
    from r4.companion.client_extra_tools import _source_add
    from r4.source import ReservedSourceNameError

    with pytest.raises(ReservedSourceNameError):
        _source_add(name, "https://evil.example", "platform")
    with pytest.raises(ValueError):
        store.add(Source(name=name, kind="public", url="https://x.example"))
    assert store.list() == []


def test_a_row_written_before_the_reservation_can_still_be_updated(store):
    legacy = {"sources": [{"name": "local", "kind": "platform", "url": "http://localhost:8080"}]}
    store._path.write_text(json.dumps(legacy))
    store.add(Source(name="local", kind="platform", url="http://localhost:8080", project="p"))
    assert store.get("local").project == "p"


def test_onboard_saves_the_profile(store, monkeypatch):
    """`r4 onboard` imported a ProfileStore that never existed (ImportError)."""
    from r4._cli import onboard
    from r4.config import ProfileConfigStore

    inputs = {
        "gateway_url": "https://gw.example",
        "api_key": "K",
        "project_id": "",
        "model": "m",
        "umrs_username": "",
    }
    monkeypatch.setattr(onboard, "_collect_onboard_inputs", lambda: inputs)
    monkeypatch.setattr(onboard, "_yes_no", lambda *_a, **_k: False)
    assert onboard._run_onboard(Namespace(profile="default")) == 0
    stored = ProfileConfigStore().load("default")
    assert (stored["gateway_url"], stored["login_gateway_url"]) == (
        "https://gw.example",
        "https://gw.example",
    )


# ------------------------------------------------------ loopback, deployments ---

IDNA_LOOPBACK_URLS = (
    "https://127。0。0。1:11434",
    "https://127．0．0．1:11434",
    "https://127｡0｡0｡1:11434",
    "https://localhost．:11434",
    "https://127。1:11434",
    "http://127。0。0。1:11434",
)


@pytest.mark.parametrize("url", IDNA_LOOPBACK_URLS)
def test_idna_full_stops_are_loopback(url):
    """A0-A-R9 / A1-A-R3: httpx reads 。．｡ as dots."""
    from r4.source_context import is_loopback_url

    assert is_loopback_url(url)


@pytest.mark.parametrize("url", IDNA_LOOPBACK_URLS)
def test_push_refuses_an_idna_loopback_source_before_any_network_call(url, store, capsys):
    store.add(Source(name="sneaky", kind="public", url=url, api_key="sk-test"))
    with patch("r4.user_sources.UserSourcesClient") as factory:
        code = SourceCommand()._handle_push(
            Namespace(name="sneaky", allow_paths="", yes=True, as_json=False)
        )
    assert code == 1
    assert "loopback" in capsys.readouterr().err
    factory.assert_not_called()


def test_remove_never_unpushes_an_idna_loopback_source(store, monkeypatch):
    store.add(Source(name="sneaky", kind="public", url=IDNA_LOOPBACK_URLS[5]))
    with patch("r4.user_sources.UserSourcesClient") as factory:
        assert SourceCommand()._handle_remove(Namespace(name="sneaky")) == 0
    factory.assert_not_called()


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.2:8080", "http://foo.localhost:8080", "http://ip6-localhost:8080"],
)
def test_another_loopback_listener_never_gets_the_local_key(url, store):
    """A1-A-R7: 127.0.0.2:8080 can be an SSH forward to a remote deployment."""
    from r4.config import Config
    from r4.source_context import config_for_source

    base = Config(
        gateway_url="http://127.0.0.1:8080",
        api_key="LOCAL-A-KEY",
        project_id="pa",
        umrs_username="ua",
    )
    config = config_for_source(Source(name="o", kind="platform", url=url), base)
    assert (config.api_key, config.project_id, config.umrs_username) == (None, None, None)
    assert config.bound_source == "o"


# ------------------------------------------------------------------ push ---


def test_push_refuses_a_public_row_on_a_platform_deployment(store, capsys):
    """A1-A-R9: b's LLM route registered as public is still b's login."""
    store.add(Source(name="b", kind="platform", url=B_URL))
    store.add(Source(name="b-llm", kind="public", url=f"{B_URL}/gw/llm", api_key="opaque-b"))
    with patch("r4.user_sources.UserSourcesClient") as factory:
        code = SourceCommand()._handle_push(
            Namespace(name="b-llm", allow_paths="", yes=True, as_json=False)
        )
    assert code == 1
    assert "platform source 'b'" in capsys.readouterr().err
    factory.assert_not_called()


def test_push_refuses_a_key_that_is_a_deployment_login(store, capsys):
    import base64

    claims = base64.urlsafe_b64encode(
        json.dumps({"sub": "bob", "gateway_url": "https://elsewhere.example"}).encode()
    ).decode()
    jwt = f"eyJhbGciOiJIUzI1NiJ9.{claims.rstrip('=')}.c2ln"
    store.add(Source(name="relabel", kind="public", url="https://llm.example/v1", api_key=jwt))
    with patch("r4.user_sources.UserSourcesClient") as factory:
        code = SourceCommand()._handle_push(
            Namespace(name="relabel", allow_paths="", yes=True, as_json=False)
        )
    assert code == 1
    assert "deployment login" in capsys.readouterr().err
    factory.assert_not_called()


def test_push_of_a_plain_public_provider_still_goes_ahead(store):
    store.add(Source(name="b", kind="platform", url=B_URL))
    store.add(Source(name="oai", kind="public", url="https://api.example.com/v1", api_key="sk"))
    sealed = MagicMock()
    with patch("r4.user_sources.UserSourcesClient", return_value=sealed):
        code = SourceCommand()._handle_push(
            Namespace(name="oai", allow_paths="", yes=True, as_json=False)
        )
    assert code == 0
    sealed.push.assert_called_once()


# ------------------------------------------------------------ hint texts ---


def test_the_401_hint_never_puts_a_token_in_argv():
    """A1-A-R10."""
    from r4.errors import AuthenticationError

    hint = AuthenticationError(status_code=401).details["hint"]
    assert "--api-key-stdin" in hint
    assert "--api-key <" not in hint


def test_no_recovery_hint_in_r4_puts_a_token_in_argv():
    root = Path(__import__("r4").__file__).parent
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "--api-key <" in line
    ]
    assert offenders == []
