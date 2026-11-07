"""Where the profile's login belongs (onboarding plan 2026-10-15, S1 fix round 1).

``Config.from_env`` decides whether the active source may receive the
profile's login, project and user by comparing the active source with the
login's *home* deployment. These pins cover the cases the first round got
wrong:

- a login that records no gateway (a piped ``--api-key-stdin`` login, an
  opaque key, an OIDC token) must not reach another deployment when several
  are registered — and ``r4 auth login`` now records where it was made;
- the single-source happy path behaves exactly as before, whatever stale
  ``gateway_url`` the profile or a repo TOML file carries;
- a repo TOML file never decides where the login goes while a source is
  active; ``R4_GATEWAY_URL`` never receives a login recorded for another
  deployment; an explicit ``R4_SOURCE`` wins over ``R4_GATEWAY_URL``;
- the profile's own deployment keeps its login while the process targets
  another source (``--source a``, ``model@a``, ``health --all``);
- ``r4 project switch`` reaches an active source on another deployment when
  the project is recorded for it, and says so when it does not;
- ``--source`` / an active source on another deployment never makes
  project-scoped commands use the active deployment's switched project;
- ``r4 --source b`` with no subcommand opens Console v3 on ``b``.

Fix round 2 (2026-10-15): a login that names no gateway records nothing
when only the built-in ``http://localhost:8080`` is in play (NEW-A-1), keeps
the home the login it replaces recorded (NEW-A-2), and a legacy login's
profile ``gateway_url`` stays home under ``R4_GATEWAY_URL`` when both are
registered platform deployments (A1-A-R6(c)).
"""

from __future__ import annotations

import io
import json
from argparse import Namespace
from typing import Any

import httpx
import pytest

A_URL = "https://a.example"
B_URL = "https://b.example"
A_KEY = "A-PROFILE-SECRET-TOKEN"
A_PROJECT = "proj-of-a"
A_USER = "alice-at-a"

_ENV = (
    "R4_API_KEY",
    "R4_API_KEY_B",
    "R4_GATEWAY_URL",
    "R4_PROJECT_ID",
    "R4_PROFILE",
    "R4_SOURCE",
    "R4_MODEL",
    "R4_ENDPOINT_URL",
    "UMRS_USERNAME",
    "UMRS_SERVER_HOST",
    "UMRS_CONTROL_PORT",
    "UMRS_TUNNEL_MODE",
    "UMRS_BASE_URL",
)


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Isolated config and HOME, a neutral cwd (no repo TOML), no r4 env."""
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(work)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    import r4.auth
    import r4.config

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    return root / "r4"


def _profile(cfg_dir, **values: Any) -> None:
    (cfg_dir / "config.json").write_text(json.dumps({"profiles": {"default": values}}))


def _sources(*rows: tuple[str, str, str | None], active: str | None = None):
    from r4.source import Source, SourceStore

    store = SourceStore()
    for name, url, key in rows:
        store.add(Source(name=name, kind="platform", url=url, api_key=key))
    if active:
        store.set_active(active)
    return store


def _headers(config=None) -> dict[str, str]:
    from r4._http import HTTPCore
    from r4.config import Config

    return HTTPCore(config or Config.from_env()).build_headers()


def _assert_nothing_of_a(headers: Any) -> None:
    headers = httpx.Headers(headers)  # case-insensitive: dict(request.headers) is lower-case
    assert A_KEY not in headers.get("Authorization", "")
    assert headers.get("X-Project-ID") != A_PROJECT
    assert headers.get("X-User-ID") != A_USER


# ------------------------------------- a login that records no gateway ---


def test_an_unrecorded_login_never_reaches_another_deployment(xdg):
    """A0-A-R1 / A1-A-R6(b) / A2-A-R2-4: no gateway_url, no claim, two deployments."""
    _profile(xdg, api_key=A_KEY, project_id=A_PROJECT, umrs_username=A_USER)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    headers = _headers()
    _assert_nothing_of_a(headers)
    assert "Authorization" not in headers


def test_an_unrecorded_oidc_login_never_reaches_another_deployment(xdg):
    from r4.auth import TokenInfo, TokenStore

    _profile(xdg, project_id=A_PROJECT, umrs_username=A_USER)
    TokenStore().save(TokenInfo(access_token=A_KEY), "default")
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    headers = _headers()
    _assert_nothing_of_a(headers)
    assert "Authorization" not in headers


def _quiet_login(monkeypatch) -> None:
    from r4._cli import login

    for name in (
        "_verify_identity_via_whoami",
        "_sync_umrs_client_profile",
        "_ensure_umrs_user_config",
        "_try_zmq_session_bootstrap_with_profile",
        "_maybe_enroll_hpc_after_login",
    ):
        monkeypatch.setattr(login, name, lambda *a, **k: None)


def _piped_auth_login(monkeypatch, *argv: str) -> int:
    import r4.config
    from r4._cli.auth import AuthCommand
    from r4._cli.login import LoginCommand

    parser = __import__("argparse").ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(A_KEY + "\n"))  # not a TTY: no prompt
    monkeypatch.setattr(r4.config, "_config", None)
    return LoginCommand().handle(parser.parse_args(["auth", "login", "--api-key-stdin", *argv]))


def test_a_piped_login_records_the_deployment_it_was_made_for(xdg, monkeypatch):
    """A0-A-R1: the login made while source a is active records a as its home.

    Then a is removed and b becomes the only (active) source: the recorded
    home — not the single-deployment fallback — keeps a's login away from b.
    """
    from r4.config import ProfileConfigStore
    from r4.source import get_source_store

    _quiet_login(monkeypatch)
    _sources(("a", A_URL, None), active="a")
    assert _piped_auth_login(monkeypatch, "--project", A_PROJECT) == 0
    assert ProfileConfigStore().load("default")["login_gateway_url"] == A_URL
    store = get_source_store()
    store.remove("a")
    _sources(("b", B_URL, None), active="b")
    headers = _headers()
    _assert_nothing_of_a(headers)
    assert "Authorization" not in headers


def test_a_login_records_the_gateway_it_names(xdg, monkeypatch):
    from r4.config import ProfileConfigStore

    _quiet_login(monkeypatch)
    assert _piped_auth_login(monkeypatch, "--gateway-url", "https://named.example/") == 0
    assert ProfileConfigStore().load("default")["login_gateway_url"] == "https://named.example"


# ------------------------------- fix round 2: what a login records (2026-10-15) ---

RCCS_URL = "https://rccs.example"
OPAQUE_KEY = "OPAQUE-RCCS-KEY"


def _jwt(**claims: Any) -> str:
    import base64

    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{body}.c2ln"


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _login(monkeypatch, *argv: str, stdin: str = "", answers: tuple[str, ...] | None = None):
    """`r4 auth login ARGV`; ``answers`` makes stdin a terminal answering the prompts.

    Returns (exit code, the prompts shown)."""
    import r4.config
    from r4._cli.auth import AuthCommand
    from r4._cli.login import LoginCommand

    parser = __import__("argparse").ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    shown: list[str] = []
    replies = iter(answers or ())
    monkeypatch.setattr("builtins.input", lambda prompt="": shown.append(prompt) or next(replies))
    monkeypatch.setattr("sys.stdin", (_Terminal if answers is not None else io.StringIO)(stdin))
    monkeypatch.setattr(r4.config, "_config", None)
    code = LoginCommand().handle(parser.parse_args(["auth", "login", *argv]))
    return code, shown


def _recorded_home() -> str | None:
    from r4.config import ProfileConfigStore

    return ProfileConfigStore().load("default").get("login_gateway_url")


_FRESH_LOGINS = {
    "stdin": (("--api-key-stdin",), OPAQUE_KEY),
    "flag": (("--api-key", OPAQUE_KEY), ""),
    "jwt-without-gateway": (("--api-key-stdin",), _jwt(sub=A_USER)),
}


@pytest.mark.parametrize("stale", [None, "http://localhost:8080"], ids=["fresh", "stale-default"])
@pytest.mark.parametrize("how", sorted(_FRESH_LOGINS))
def test_a_login_made_before_any_gateway_exists_records_no_home(xdg, monkeypatch, how, stale):
    """NEW-A-1: the built-in http://localhost:8080 (or the old prompt default
    the profile kept) is not where the login belongs. Nothing is recorded,
    so the user's one remote source gets the login exactly as on HEAD."""
    _quiet_login(monkeypatch)
    if stale:
        _profile(xdg, gateway_url=stale)
    argv, piped = _FRESH_LOGINS[how]
    code, _ = _login(monkeypatch, *argv, "--project", "projA", stdin=piped)
    assert code == 0
    assert _recorded_home() is None
    _sources(("rccs", RCCS_URL, None), active="rccs")
    headers = _headers()
    key = piped or OPAQUE_KEY
    assert (headers.get("Authorization"), headers.get("X-Project-ID")) == (f"Bearer {key}", "projA")


def test_enter_at_the_gateway_prompt_records_no_home_when_nothing_names_one(xdg, monkeypatch):
    """NEW-A-1: Enter on `Gateway URL [http://localhost:8080]` names nothing."""
    _quiet_login(monkeypatch)
    code, shown = _login(monkeypatch, "--api-key", OPAQUE_KEY, answers=("", ""))
    assert code == 0
    assert shown[0] == "Gateway URL [http://localhost:8080]: "
    assert _recorded_home() is None


def test_a_login_while_a_source_at_the_default_address_is_active_records_it(xdg, monkeypatch):
    """The stand-alone stack registered at localhost:8080 and active: that
    source names the address, so the login made now belongs to it."""
    _quiet_login(monkeypatch)
    _sources(("standalone", "http://localhost:8080", None), active="standalone")
    code, _ = _login(monkeypatch, "--api-key-stdin", stdin=OPAQUE_KEY)
    assert code == 0
    assert _recorded_home() == "http://localhost:8080"


A_LOOP = "http://127.0.0.1:9"
B_LOOP = "http://127.0.0.2:9"


def test_a_relogin_naming_no_gateway_keeps_the_recorded_home(xdg, monkeypatch):
    """NEW-A-2: refreshing A's token while b is active (as the 401 and
    `r4 refresh` hints say) keeps A's record; b gets none of the login."""
    from r4.config import Config
    from r4.source import get_source_store
    from r4.source_context import config_for_source

    _quiet_login(monkeypatch)
    _sources(("a", A_LOOP, None), active="a")
    assert _login(monkeypatch, "--api-key-stdin", "--project", "projA", stdin="A-KEY-1")[0] == 0
    assert _recorded_home() == A_LOOP
    _sources(("b", B_LOOP, None), active="b")
    assert _login(monkeypatch, "--api-key-stdin", stdin="A-KEY-2")[0] == 0
    assert _recorded_home() == A_LOOP
    to_b = _headers()
    assert (to_b.get("Authorization"), to_b.get("X-Project-ID")) == (None, None)
    a = config_for_source(get_source_store().get("a"), Config.from_env())
    to_a = _headers(a)
    assert (to_a["Authorization"], to_a["X-Project-ID"]) == ("Bearer A-KEY-2", "projA")


def test_a_relogin_keeps_the_home_the_previous_tokens_claim_names(xdg, monkeypatch):
    """NEW-A-2: a pre-2026-10-15 login records its home only in its JWT claim;
    an opaque refresh while b is active keeps that home."""
    _quiet_login(monkeypatch)
    _profile(xdg, api_key=_jwt(sub=A_USER, gateway_url=A_URL), project_id=A_PROJECT)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    assert _login(monkeypatch, "--api-key-stdin", stdin=A_KEY)[0] == 0
    assert _recorded_home() == A_URL
    headers = _headers()
    _assert_nothing_of_a(headers)
    assert "Authorization" not in headers


def test_a_relogin_over_an_oidc_login_keeps_its_recorded_home(xdg, monkeypatch):
    from r4.auth import TokenInfo, TokenStore

    _quiet_login(monkeypatch)
    _profile(xdg, login_gateway_url=A_URL, project_id=A_PROJECT)
    TokenStore().save(TokenInfo(access_token="A-OIDC"), "default")
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    assert _login(monkeypatch, "--api-key-stdin", stdin=A_KEY)[0] == 0
    assert _recorded_home() == A_URL
    _assert_nothing_of_a(_headers())


def test_a_login_after_logout_is_a_new_login_not_a_refresh(xdg, monkeypatch):
    """With no stored login the old record is not kept: the login made while
    b is active belongs to b."""
    _quiet_login(monkeypatch)
    _profile(xdg, login_gateway_url=A_URL)  # `r4 auth logout` left the record, no key
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    assert _login(monkeypatch, "--api-key-stdin", stdin="B-LOGIN")[0] == 0
    assert _recorded_home() == B_URL
    assert _headers()["Authorization"] == "Bearer B-LOGIN"


def test_a_relogin_for_a_source_named_profile_follows_the_sources_url(xdg, monkeypatch):
    """`r4 auth login --profile b` is a login made for source b, wherever the
    profile recorded b before (`r4 source set b --url` moved it)."""
    from r4.config import ProfileConfigStore
    from r4.source import get_source_store
    from r4.source_context import credential_for

    _quiet_login(monkeypatch)
    ProfileConfigStore().save("b", login_gateway_url="https://old-b.example", api_key="B-OLD")
    _sources(("b", B_URL, None))
    assert _login(monkeypatch, "--profile", "b", "--api-key-stdin", stdin="B-NEW")[0] == 0
    assert ProfileConfigStore().load("b")["login_gateway_url"] == B_URL
    assert credential_for(get_source_store().get("b")) == "B-NEW"


def test_a_relogin_naming_a_gateway_moves_the_home(xdg, monkeypatch):
    _quiet_login(monkeypatch)
    _profile(xdg, login_gateway_url=A_URL, api_key=A_KEY)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    assert _login(monkeypatch, "--api-key-stdin", "--gateway-url", B_URL, stdin="B-LOGIN")[0] == 0
    assert _recorded_home() == B_URL
    assert _headers()["Authorization"] == "Bearer B-LOGIN"


def test_the_gateway_prompt_defaults_to_the_recorded_home(xdg, monkeypatch, capsys):
    """NEW-A-2: on a terminal the prompt offers the recorded home, not the
    active source; Enter keeps it."""
    _quiet_login(monkeypatch)
    _profile(xdg, login_gateway_url=A_URL, api_key=A_KEY)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    code, shown = _login(monkeypatch, "--api-key", "A-KEY-NEW", answers=("", ""))
    assert code == 0
    assert shown[0] == f"Gateway URL [{A_URL}]: "
    assert _recorded_home() == A_URL
    assert "Authorization" not in _headers()


def test_a_kept_home_says_how_to_move_the_login(xdg, monkeypatch, capsys):
    _quiet_login(monkeypatch)
    _profile(xdg, gateway_url=A_URL, login_gateway_url=A_URL, api_key=A_KEY)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    assert _login(monkeypatch, "--api-key-stdin", stdin="A-KEY-NEW")[0] == 0
    err = capsys.readouterr().err
    assert f"kept for {A_URL}" in err
    assert f"--gateway-url {B_URL}" in err
    assert "A-KEY-NEW" not in err


# ------------------------------------------- the single-source happy path ---


@pytest.mark.parametrize("keyed", [False, True], ids=["keyless-row", "keyed-row"])
@pytest.mark.parametrize("stale", ["profile", "local-toml"])
def test_a_stale_gateway_never_changes_the_single_source_path(xdg, keyed, stale):
    """A0-A-R2: one active source; a stale profile or TOML gateway_url (the
    old `Gateway URL [http://localhost:8080]` prompt default) changes nothing."""
    profile = {"api_key": A_KEY, "project_id": A_PROJECT, "umrs_username": A_USER}
    if stale == "profile":
        profile["gateway_url"] = "http://localhost:8080"
    else:
        profile["gateway_url"] = A_URL
        (xdg.parent.parent / "work" / ".r4").mkdir()
        (xdg.parent.parent / "work" / ".r4" / "local.toml").write_text(
            'gateway_url = "http://localhost:8080"\n'
        )
    _profile(xdg, **profile)
    _sources(("a", A_URL, "A-SRC-KEY" if keyed else None), active="a")
    headers = _headers()
    assert headers["Authorization"] == f"Bearer {'A-SRC-KEY' if keyed else A_KEY}"
    assert (headers.get("X-Project-ID"), headers.get("X-User-ID")) == (A_PROJECT, A_USER)


def test_a_repo_toml_gateway_never_sends_the_login_to_the_active_source(xdg):
    """A1-A-R6(a): the login records a; a repo TOML names b; b is active."""
    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT, umrs_username=A_USER)
    (xdg.parent.parent / "work" / ".r4").mkdir()
    (xdg.parent.parent / "work" / ".r4" / "config.toml").write_text(f'gateway_url = "{B_URL}"\n')
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    headers = _headers()
    _assert_nothing_of_a(headers)
    assert "Authorization" not in headers


# ------------------------------------------------------ R4_GATEWAY_URL ---


@pytest.fixture
def wire(monkeypatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"models": [], "ok": True, "project": {}})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    class _Captured(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _Captured)
    return seen


@pytest.mark.parametrize(("target", "own"), [("b", None), ("bk", "BK-OWN-KEY")])
def test_r4_gateway_url_never_receives_a_login_recorded_elsewhere(
    xdg, wire, monkeypatch, target, own
):
    """A1-A-R6(c): R4_GATEWAY_URL names another deployment than the login's."""
    from r4.client import Client

    url = {"b": B_URL, "bk": "https://bk.example"}[target]
    _profile(
        xdg, login_gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT, umrs_username=A_USER
    )
    _sources(("site-a", A_URL, None), ("bk", "https://bk.example", "BK-OWN-KEY"))
    monkeypatch.setenv("R4_GATEWAY_URL", url)
    Client().get("/gw/models")
    (request,) = wire
    assert request.url.host == httpx.URL(url).host
    _assert_nothing_of_a(dict(request.headers))
    assert request.headers.get("authorization") == (f"Bearer {own}" if own else None)


@pytest.fixture
def legacy_login_of_a(xdg):
    """A login made before 2026-10-15 for a: no record, no claim; only the
    profile's gateway_url names a, and a and b are both registered."""
    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT, umrs_username=A_USER)
    return _sources(("site-a", A_URL, None), ("b", B_URL, None), active="site-a")


def test_r4_gateway_url_never_receives_a_legacy_login_of_another_registered_deployment(
    legacy_login_of_a, wire, monkeypatch
):
    """A1-A-R6(c): R4_GATEWAY_URL is registered source b; the profile names a."""
    from r4.client import Client

    monkeypatch.setenv("R4_GATEWAY_URL", B_URL)
    Client().get("/gw/models")
    (request,) = wire
    assert request.url.host == "b.example"
    _assert_nothing_of_a(dict(request.headers))
    assert "authorization" not in request.headers


def test_under_r4_gateway_url_the_legacy_logins_own_deployment_keeps_it(
    legacy_login_of_a, monkeypatch
):
    from r4.config import Config
    from r4.source_context import config_for_source

    monkeypatch.setenv("R4_GATEWAY_URL", B_URL)
    config = Config.from_env()
    assert config.login_home == A_URL
    headers = _headers(config_for_source(legacy_login_of_a.get("site-a"), config))
    assert (headers["Authorization"], headers["X-Project-ID"]) == (f"Bearer {A_KEY}", A_PROJECT)
    assert "Authorization" not in _headers(config_for_source(legacy_login_of_a.get("b"), config))


@pytest.mark.parametrize(
    "gateway", ["http://127.0.0.1:18080", "https://unregistered.example"], ids=["tunnel", "other"]
)
def test_r4_gateway_url_not_registered_keeps_a_legacy_login(
    legacy_login_of_a, monkeypatch, gateway
):
    """An unregistered R4_GATEWAY_URL may be the login's own deployment by
    another address (an SSH forward): a legacy login goes to it, as before."""
    monkeypatch.setenv("R4_GATEWAY_URL", gateway)
    headers = _headers()
    assert (headers["Authorization"], headers["X-Project-ID"]) == (f"Bearer {A_KEY}", A_PROJECT)


def test_r4_gateway_url_keeps_a_stale_default_login_next_to_a_registered_standalone(
    xdg, monkeypatch
):
    """The rccs.env user: the profile keeps the old prompt default
    localhost:8080, the stand-alone stack is registered there, rccs too.
    The old default is no evidence of where the login belongs."""
    _profile(xdg, gateway_url="http://localhost:8080", api_key=A_KEY, project_id=A_PROJECT)
    _sources(("standalone", "http://localhost:8080", "SA-KEY"), ("rccs", RCCS_URL, None))
    monkeypatch.setenv("R4_GATEWAY_URL", RCCS_URL)
    headers = _headers()
    assert (headers["Authorization"], headers["X-Project-ID"]) == (f"Bearer {A_KEY}", A_PROJECT)


def test_r4_gateway_url_with_an_unrecorded_login_keeps_working(xdg, monkeypatch):
    """rccs.env exports R4_GATEWAY_URL; an older login that records nothing
    (often with a stale profile gateway_url) still goes to it, as before."""
    _profile(xdg, gateway_url="http://localhost:8080", api_key=A_KEY, project_id=A_PROJECT)
    _sources(("default", "http://localhost:8080", A_KEY), active="default")
    monkeypatch.setenv("R4_GATEWAY_URL", "https://rccs.example/rccs")
    headers = _headers()
    assert headers["Authorization"] == f"Bearer {A_KEY}"
    assert headers["X-Project-ID"] == A_PROJECT


def test_an_explicit_r4_source_wins_over_r4_gateway_url(xdg, monkeypatch):
    """A1-A-R5: `export R4_SOURCE=standalone` works next to rccs.env."""
    from r4.config import Config

    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT)
    _sources(("site-a", A_URL, None), ("b", B_URL, "B-OWN"))
    monkeypatch.setenv("R4_GATEWAY_URL", A_URL)
    monkeypatch.setenv("R4_SOURCE", "b")
    config = Config.from_env()
    assert (config.gateway_url, config.api_key, config.project_id) == (B_URL, "B-OWN", None)


def test_source_flag_with_no_subcommand_opens_the_console_on_that_source(xdg, monkeypatch):
    """A1-A-R5: `r4 --source b` starts Console v3 in a child process."""
    from r4 import cli
    from r4._cli import console
    from r4.config import Config

    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT)
    _sources(("site-a", A_URL, None), ("b", B_URL, "B-OWN"), active="site-a")
    monkeypatch.setenv("R4_SOURCE", "")  # restored (removed) at teardown
    seen: dict[str, dict] = {}
    monkeypatch.setattr(console, "console_available", lambda: True)
    monkeypatch.setattr(console, "plan_console_launch", lambda: ("dev", None, ""))
    monkeypatch.setattr(console, "launch_notes", lambda *_a: [])

    def record(**kwargs: Any) -> int:
        seen["env"] = dict(kwargs["env"])
        return 0

    monkeypatch.setattr(console, "_launch_dev_source", record)
    cli._launch_tui(
        Namespace(
            source="b",
            tui_model=None,
            prompt=None,
            secure=False,
            fs_root=None,
            allow_all_tools=False,
        )
    )
    child = seen["env"]
    assert child.get("R4_SOURCE") == "b"
    monkeypatch.setenv("R4_SOURCE", child["R4_SOURCE"])
    assert Config.from_env().gateway_url == B_URL


def test_console_subcommand_names_the_global_source_flag(xdg, monkeypatch):
    from r4._cli import console

    seen: dict[str, dict] = {}
    monkeypatch.setattr(console, "plan_console_launch", lambda: ("dev", None, ""))
    monkeypatch.setattr(console, "launch_notes", lambda *_a: [])
    monkeypatch.setattr(
        console, "_launch_dev_source", lambda **kw: seen.setdefault("env", kw["env"]) and 0
    )
    console.cmd_console(Namespace(demo=False, offline=False, backend=None, source="b"))
    assert seen["env"]["R4_SOURCE"] == "b"


# ------------------------- the profile's own deployment, while bound elsewhere ---


@pytest.fixture
def bound_to_b(xdg):
    """Profile login for A; sources a and b key-less; b active (bound)."""
    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT, umrs_username=A_USER)
    return _sources(("a", A_URL, None), ("b", B_URL, None), active="b")


def test_source_flag_to_the_login_home_keeps_the_login(bound_to_b, capsys):
    """A0-A-R3: `--source a` while b is active sends a its own profile login."""
    from r4 import cli
    from r4.config import get_config

    cli._apply_tui_source_override("a")
    headers = _headers(get_config())
    assert headers["Authorization"] == f"Bearer {A_KEY}"
    assert (headers["X-Project-ID"], headers["X-User-ID"]) == (A_PROJECT, A_USER)
    assert "never reused" not in capsys.readouterr().err


def test_model_at_the_login_home_keeps_the_login(bound_to_b):
    from r4.client import Client
    from r4.config import get_config
    from r4.model_routing import ModelRoute

    route = ModelRoute(kind="platform", base_url=A_URL, source_name="a")
    headers = Client(config=get_config())._http_for_route(route).build_headers()
    assert headers["Authorization"] == f"Bearer {A_KEY}"
    assert headers["X-Project-ID"] == A_PROJECT


def test_health_across_sources_reaches_the_login_home_with_the_login(bound_to_b):
    from r4.config import get_config
    from r4.source import get_source_store
    from r4.source_context import config_for_source

    a = get_source_store().get("a")
    assert _headers(config_for_source(a, get_config()))["Authorization"] == f"Bearer {A_KEY}"
    b = get_source_store().get("b")
    assert "Authorization" not in _headers(config_for_source(b, get_config()))


# ------------------------------------------------------------- projects ---


def _project(project_id: str, gateway: str | None) -> None:
    from r4.project import ProjectConfig, get_project_manager

    get_project_manager().create(ProjectConfig(project_id=project_id, gateway_url=gateway))


def test_project_switch_reaches_an_active_source_on_another_deployment(xdg, monkeypatch, capsys):
    """A2-A-R2-2: the switched project is recorded for b's deployment."""
    from r4._cli.project import _project_switch
    from r4.config import reload_config
    from r4.project import get_project_manager

    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT)
    _sources(("site-a", A_URL, None), ("b", B_URL, "B-KEY"), active="b")
    _project("proj-b", B_URL)
    pm = get_project_manager()
    _project_switch(Namespace(project_id="proj-b"), pm, "default", reload_config, None)
    headers = _headers()
    assert (headers["Authorization"], headers["X-Project-ID"]) == ("Bearer B-KEY", "proj-b")


def test_project_switch_says_when_the_active_source_is_not_sent_it(xdg, capsys):
    from r4._cli.project import _project_switch
    from r4.config import reload_config
    from r4.project import get_project_manager

    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT)
    _sources(("site-a", A_URL, None), ("b", B_URL, "B-KEY"), active="b")
    _project("proj-x", None)
    _project_switch(
        Namespace(project_id="proj-x"), get_project_manager(), "default", reload_config, None
    )
    assert "r4 source set b --project proj-x" in capsys.readouterr().err
    assert _headers().get("X-Project-ID") is None


@pytest.fixture
def a_project_switched(xdg):
    """`r4 project switch proj-of-a` on A; b registered on another deployment."""
    from r4.project import get_project_manager

    _profile(xdg, gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT, umrs_username=A_USER)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="site-a")
    _project(A_PROJECT, None)
    get_project_manager().set_current(A_PROJECT)


def test_source_flag_never_uses_the_active_switched_project(a_project_switched):
    """A1-A-R4: knowledge / rag / artifact / agent_exec resolve the project here."""
    from r4 import cli
    from r4._cli._helpers import _current_project_id

    assert _current_project_id() == A_PROJECT
    cli._apply_tui_source_override("b")
    assert _current_project_id() is None


def _project_traffic(wire, host: str) -> list:
    """Wire entries to *host* minus the H-10.2 mode probe: /ready|/health are
    deployment discovery, legitimately carrying no x-project-id."""
    return [r for r in wire if r.url.host == host and r.url.path not in ("/ready", "/health")]


def test_project_storage_under_source_flag_never_sends_the_active_project(
    a_project_switched, wire, monkeypatch
):
    from r4 import cli
    from r4._cli.project_storage import project_storage
    from r4.project import get_project_manager
    from r4.source import get_source_store

    store = get_source_store()
    b = store.get("b")
    b.project = "proj-of-b"
    store.add(b)
    cli._apply_tui_source_override("b")
    args = Namespace(project_id=None, storage_action="show", as_json=True)
    try:
        project_storage(args, get_project_manager())
    except SystemExit:  # the reply shape is not under test, the wire is
        pass
    to_b = _project_traffic(wire, "b.example")
    assert to_b and all(A_PROJECT not in str(r.url) for r in to_b)
    assert {r.headers.get("x-project-id") for r in to_b} == {"proj-of-b"}


def test_a_bound_client_asks_only_about_its_own_project(a_project_switched, wire):
    """ADR-062 gate probe: a client bound to b never names the active project."""
    from r4.client import Client
    from r4.config import get_config
    from r4.source import get_source_store
    from r4.source_context import config_for_source

    client = Client(config=config_for_source(get_source_store().get("b"), get_config()))
    client._project_data_class()
    assert all(A_PROJECT not in str(r.url) for r in wire)


def test_the_login_homes_zmq_session_registers_at_home_with_the_login(bound_to_b):
    """While the process is bound to b, a transport for a registers at a."""
    from r4.config import get_config
    from r4.source import get_source_store
    from r4.source_context import config_for_source, transport_session_fields

    home = config_for_source(get_source_store().get("a"), get_config())
    assert transport_session_fields(home) == {
        "session_bound": True,
        "session_gateway_url": A_URL,
        "session_credential": A_KEY,
    }


def test_an_unknown_home_says_how_to_record_it(xdg, monkeypatch):
    """The active source bound only because r4 cannot tell where the login
    belongs: the 401 hint also says how to record that it is this one's."""
    from r4.client import Client
    from r4.errors import AuthenticationError

    _profile(xdg, api_key=A_KEY, project_id=A_PROJECT)
    _sources(("site-a", A_URL, None), ("b", B_URL, None), active="b")
    refused = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "no"}))
    real_client = httpx.Client

    class _Refusing(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = refused
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _Refusing)
    with pytest.raises(AuthenticationError) as caught:
        Client().get("/gw/models")
    hint = caught.value.hint or ""
    assert "r4 source set b --api-key-stdin" in hint
    assert f"r4 auth login --gateway-url {B_URL} --api-key-stdin" in hint
