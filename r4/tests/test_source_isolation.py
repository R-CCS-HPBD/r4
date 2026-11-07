"""Per-source credential and identity isolation (onboarding plan 2026-10-15, S1).

One r4 process talks to several deployments. Deployment A is the profile's
own login (its bearer token, project and user); deployment B is another
registered platform source. Every call-site class that sends a request to B
— ``--source``, the per-command source overrides, the per-source probes and
listings, ``model@B`` chat on every wire path, the companion's ``source=``
tools, the model-scale probe — must send B only what is B's own: its own
key (``bk``) or no Authorization at all (``b``), never A's bearer token,
A's X-Project-ID or A's X-User-ID.

Before 2026-10-15 these sites built ``api_key=source.api_key or cfg.api_key``
(and the per-route HTTP core reused the active identity headers), so a
key-less source received the active deployment's token.

The pins use only names that existed before the fix, so on the old code they
fail on the leaked headers, not on an import.
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest

A_URL = "https://a.example"
A_KEY = "A-PROFILE-SECRET-TOKEN"
A_PROJECT = "proj-of-a"
A_USER = "alice-at-a"
B_URLS = {"b": "https://b.example", "bk": "https://bk.example"}
B_OWN_KEY = "B-OWN-KEY"

_REPLY = {
    "ok": True,
    "ready": True,
    "models": [],
    "data": [],
    "endpoints": [],
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
}

_ENV = (
    "R4_API_KEY",
    "R4_GATEWAY_URL",
    "R4_PROJECT_ID",
    "R4_PROFILE",
    "R4_SOURCE",
    "R4_MODEL",
    "R4_ENDPOINT_URL",
    "R4_ENDPOINT_PROVIDER",
    "UMRS_USERNAME",
    "UMRS_SERVER_HOST",
    "UMRS_CONTROL_PORT",
    "UMRS_TUNNEL_MODE",
    "UMRS_BASE_URL",
)


class _Stop(Exception):
    """Raised by a patched preflight: the command has applied its source."""


@pytest.fixture
def deployments(tmp_path, monkeypatch):
    """Profile login for A (active source ``site-a``); B key-less, BK keyed."""
    from r4.source import Source, SourceStore

    xdg = tmp_path / "xdg"
    (xdg / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    profile = {
        "gateway_url": A_URL,
        "api_key": A_KEY,
        "project_id": A_PROJECT,
        "umrs_username": A_USER,
    }
    (xdg / "r4" / "config.json").write_text(json.dumps({"profiles": {"default": profile}}))
    store = SourceStore(path=xdg / "r4" / "sources.json")
    store.add(Source(name="site-a", kind="platform", url=A_URL))
    store.add(Source(name="b", kind="platform", url=B_URLS["b"]))
    store.add(Source(name="bk", kind="platform", url=B_URLS["bk"], api_key=B_OWN_KEY))
    store.set_active("site-a")

    import r4.auth
    import r4.config
    from r4.client import Client

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    # The HTTP path is the one under test; no provider transport (urllib).
    monkeypatch.setattr(Client, "_provider_for_route", lambda self, route: None)
    monkeypatch.setattr("r4.providers.factory._detect_local_ollama", lambda: None)
    return store


@pytest.fixture
def wire(monkeypatch) -> list[httpx.Request]:
    """Every httpx request in the process, answered locally (no network)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_REPLY)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    class _Captured(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _Captured)
    return seen


def _source(name: str):
    from r4.source import get_source_store

    return get_source_store().get(name)


def _get_on_process_config() -> None:
    from r4.client import Client

    Client().get("/gw/models")


# ------------------------------------------------------- call-site drivers ---


def _cli_source_flag(name: str) -> None:
    from r4 import cli

    cli._apply_tui_source_override(name)
    _get_on_process_config()


def _chat_command(name: str, monkeypatch) -> None:
    from r4._cli.chat import ChatCommand

    monkeypatch.setattr("r4._cli._helpers._preflight_check", _raise_stop)
    with pytest.raises(_Stop):
        ChatCommand().handle(Namespace(source=name))
    _get_on_process_config()


def _model_command(name: str, monkeypatch) -> None:
    from r4._cli.model import ModelCommand

    monkeypatch.setattr("r4._cli._helpers._preflight_check", _raise_stop)
    with pytest.raises(_Stop):
        ModelCommand().handle(Namespace(endpoint_source=name, model_command="list"))
    _get_on_process_config()


def _agent_override(name: str) -> None:
    from r4._cli.agent import _apply_source_override

    _apply_source_override(name)
    _get_on_process_config()


def _up_source(name: str) -> None:
    from r4._cli.up import _resolve_up_source

    _resolve_up_source(Namespace(source=name))
    _get_on_process_config()


def _up_picker(name: str) -> None:
    from r4._cli.up import _apply_picker_source

    _apply_picker_source(SimpleNamespace(_picker_source=name))
    _get_on_process_config()


def _routing_probe_config(name: str) -> None:
    from r4._http import HTTPCore
    from r4.model_routing import _source_probe_config

    HTTPCore(_source_probe_config(_source(name))).get(f"{B_URLS[name]}/gw/models")


def _models_all_sources(name: str) -> None:
    from r4._cli._helpers import _available_models_all_sources

    _available_models_all_sources([_source(name)], None)


def _endpoint_source_config(name: str) -> None:
    from r4._cli.endpoint import _apply_endpoint_source_config
    from r4.client import Client
    from r4.config import get_config

    Client(config=_apply_endpoint_source_config(get_config(), _source(name))).get("/gw/models")


def _endpoint_list(name: str) -> None:
    from r4._cli.endpoint import _list_endpoints_for_source
    from r4.config import get_config

    _list_endpoints_for_source(_source(name), Namespace(all=False), get_config())


def _endpoint_down_scan(_name: str) -> None:
    from r4._cli.endpoint import _resolve_down_client_for_instance
    from r4.client import Client

    _resolve_down_client_for_instance(Namespace(instance_id="i-1", source=None), Client())


def _health_quick(name: str) -> None:
    from r4._cli.health import _probe_source_quick

    _probe_source_quick(_source(name))


def _health_full(name: str, monkeypatch) -> None:
    from r4._cli import health
    from r4.client import Client

    def fake_diagnostics(config):
        Client(config=config).get("/gw/models")
        return {}

    monkeypatch.setattr(health, "run_full_diagnostics", fake_diagnostics)
    health._collect_full_diagnostics([_source(name)], "site-a")


def _pool_entry(name: str) -> None:
    from r4._cli.pool import _resolve_entry_client
    from r4.client import Client

    _resolve_entry_client(Client(), {"source": name}).get("/gw/models")


def _pool_source_client(name: str) -> None:
    from r4._cli.pool import _get_source_client

    _get_source_client(name).get("/gw/models")


def _companion_tool(name: str) -> None:
    from r4.client import Client
    from r4.companion.site_routing import resolve_source_client

    resolve_source_client(Client(), name).client.get("/gw/models")


def _hpc_sites_probe(name: str) -> None:
    """Multi-HPC S2 (2026-10-24 review): the real per-source probe client
    (``r4.sites._client_for``), every source probed at once."""
    from r4 import sites

    sites.clear_cache()
    sites.inventory(refresh=True)


def _hpc_jobs_listing(name: str) -> None:
    from r4 import hpc_jobs, sites

    sites.clear_cache()
    hpc_jobs.list_across_sites()


def _companion_site_read(name: str) -> None:
    """A read tool with ``site=`` through the real routed client."""
    from r4 import sites
    from r4.client import Client
    from r4.companion.tools import _client_tools

    sites.clear_cache()
    tools = {t["name"]: t["execute"] for t in _client_tools(Client())}
    tools["job_list"](site=name)


def _companion_cli(name: str) -> None:
    from r4._cli import companion

    companion._companion_client(_source(name)).get("/gw/models")


def _route_blocking(name: str) -> None:
    from r4.client import Client

    Client().chat("hi", model=f"m@{name}")


def _route_stream(name: str) -> None:
    from r4.client import Client

    list(Client().chat("hi", model=f"m@{name}", stream=True))


def _route_agentic(name: str) -> None:
    from r4.client import Client

    payload = {"model": f"m@{name}", "messages": [{"role": "user", "content": "hi"}]}
    stream = Client()._stream_request_collect(payload, [])
    list(stream or [])


def _route_text_completion(name: str) -> None:
    from r4.client import Client

    Client().text_completion({"model": f"m@{name}", "prompt": "x"})


def _model_scale_probe(name: str) -> None:
    from r4.client import Client
    from r4.model_scale import detect_model_scale

    detect_model_scale("m", source=_source(name), client=Client(), refresh=True)


def _raise_stop(*_args: Any, **_kwargs: Any) -> None:
    raise _Stop()


_NEEDS_MONKEYPATCH = {_chat_command, _model_command, _health_full}

CALL_SITES: dict[str, Callable[..., None]] = {
    "cli --source": _cli_source_flag,
    "r4 chat --source": _chat_command,
    "r4 model --source": _model_command,
    "r4 agent --source": _agent_override,
    "r4 up --source": _up_source,
    "r4 up picker source": _up_picker,
    "model routing probe": _routing_probe_config,
    "model list all sources": _models_all_sources,
    "endpoint --source config": _endpoint_source_config,
    "endpoint list per source": _endpoint_list,
    "endpoint down scan": _endpoint_down_scan,
    "health quick probe": _health_quick,
    "health full diagnostics": _health_full,
    "pool entry source": _pool_entry,
    "pool source client": _pool_source_client,
    "companion source= tool": _companion_tool,
    "companion site= read tool": _companion_site_read,
    "hpc sites probe": _hpc_sites_probe,
    "hpc jobs across sites": _hpc_jobs_listing,
    "companion --source": _companion_cli,
    "model@source blocking": _route_blocking,
    "model@source streaming": _route_stream,
    "model@source agentic collect": _route_agentic,
    "model@source text completion": _route_text_completion,
    "model scale probe": _model_scale_probe,
}


def _drive(site: str, target: str, monkeypatch) -> None:
    driver = CALL_SITES[site]
    try:
        if driver in _NEEDS_MONKEYPATCH:
            driver(target, monkeypatch)
        else:
            driver(target)
    except (Exception, SystemExit):  # noqa: BLE001 — the wire, not the reply, is under test
        pass


def _requests_to(seen: list[httpx.Request], target: str) -> list[httpx.Request]:
    host = httpx.URL(B_URLS[target]).host
    return [r for r in seen if r.url.host == host]


def _assert_nothing_of_a(requests: list[httpx.Request]) -> None:
    for request in requests:
        for header, value in request.headers.items():
            assert A_KEY not in value, f"A's bearer token reached {request.url} in {header}"
        assert request.headers.get("x-project-id") != A_PROJECT, (
            f"A's project reached {request.url}"
        )
        assert request.headers.get("x-user-id") != A_USER, f"A's user reached {request.url}"


@pytest.mark.parametrize("site", sorted(CALL_SITES))
def test_a_keyless_source_never_receives_the_active_identity(site, deployments, wire, monkeypatch):
    _drive(site, "b", monkeypatch)
    to_b = _requests_to(wire, "b")
    assert to_b, f"{site}: no request reached source b"
    _assert_nothing_of_a(to_b)
    assert all("authorization" not in r.headers for r in to_b), (
        f"{site}: a key-less source must be sent no credential at all"
    )


@pytest.mark.parametrize("site", sorted(CALL_SITES))
def test_a_keyed_source_receives_only_its_own_key(site, deployments, wire, monkeypatch):
    _drive(site, "bk", monkeypatch)
    to_b = _requests_to(wire, "bk")
    assert to_b, f"{site}: no request reached source bk"
    _assert_nothing_of_a(to_b)
    # Unauthenticated readiness probes (/ready, /health) carry none.
    sent = {r.headers["authorization"] for r in to_b if "authorization" in r.headers}
    assert sent == {f"Bearer {B_OWN_KEY}"}


# ------------------------------------------------- the single-source path ---


def test_the_active_source_on_the_profiles_deployment_keeps_the_login(deployments, wire):
    """Single-source happy path: A's own login, project and user go to A."""
    _get_on_process_config()
    to_a = [r for r in wire if r.url.host == "a.example"]
    assert to_a
    assert to_a[0].headers["authorization"] == f"Bearer {A_KEY}"
    assert to_a[0].headers["x-project-id"] == A_PROJECT
    assert to_a[0].headers["x-user-id"] == A_USER


def test_model_at_the_active_source_keeps_the_login(deployments, wire):
    from r4.client import Client

    Client().chat("hi", model="m@site-a")
    to_a = [r for r in wire if r.url.host == "a.example"]
    assert to_a and to_a[0].headers["authorization"] == f"Bearer {A_KEY}"
    assert to_a[0].headers["x-project-id"] == A_PROJECT


# ------------------------------------------------- the active-source overlay ---


def test_a_keyless_active_source_elsewhere_gets_no_profile_login(deployments, monkeypatch):
    """config.py overlay: the profile's login belongs to A, so B gets none."""
    monkeypatch.setenv("R4_SOURCE", "b")
    from r4.config import Config

    config = Config.from_env()
    assert config.gateway_url == B_URLS["b"]
    assert config.api_key is None
    assert config.project_id is None
    assert config.umrs_username is None


def test_the_overlay_keeps_the_login_when_it_belongs_to_the_active_source(deployments):
    from r4.config import Config

    config = Config.from_env()
    assert (config.gateway_url, config.api_key) == (A_URL, A_KEY)
    assert (config.project_id, config.umrs_username) == (A_PROJECT, A_USER)


def test_no_auth_manager_fallback_for_a_keyless_active_source_elsewhere(deployments, monkeypatch):
    """HTTPCore must not fall back to the profile's stored login either."""
    monkeypatch.setenv("R4_SOURCE", "b")
    from r4._http import HTTPCore
    from r4.config import Config

    headers = HTTPCore(Config.from_env()).build_headers()
    assert "Authorization" not in headers


# --------------------------------------------------------- Client(...) copy ---


def test_client_overrides_never_mutate_the_global_config(monkeypatch):
    import r4.config
    from r4.client import Client
    from r4.config import Config, get_config

    shared = Config(gateway_url=A_URL, api_key=A_KEY, project_id=A_PROJECT)
    monkeypatch.setattr(r4.config, "_config", shared)
    client = Client(gateway_url=B_URLS["bk"], api_key=B_OWN_KEY, project_id="p-b")
    assert (client.config.gateway_url, client.config.api_key) == (B_URLS["bk"], B_OWN_KEY)
    assert get_config() is shared
    assert (shared.gateway_url, shared.api_key, shared.project_id) == (A_URL, A_KEY, A_PROJECT)


def test_client_overrides_never_mutate_a_passed_config():
    from r4.client import Client
    from r4.config import Config

    passed = Config(gateway_url=A_URL, api_key=A_KEY)
    Client(config=passed, gateway_url=B_URLS["b"])
    assert (passed.gateway_url, passed.api_key) == (A_URL, A_KEY)


def test_companion_tool_source_client_leaves_the_bridge_config_alone(deployments):
    from r4.client import Client
    from r4.companion.site_routing import resolve_source_client
    from r4.config import get_config

    bridge = Client()
    before = (get_config().gateway_url, get_config().api_key)
    routed = resolve_source_client(bridge, "bk").client
    assert routed is not bridge
    assert routed.config.gateway_url == B_URLS["bk"]
    assert (get_config().gateway_url, get_config().api_key) == before == (A_URL, A_KEY)
    assert (bridge.config.gateway_url, bridge.config.api_key) == (A_URL, A_KEY)


def test_companion_tool_unknown_source_fails_closed(deployments, wire):
    """X-a (multi-HPC plan S3, 2026-10-24): an unknown or public source name
    is refused naming the valid sites. Before, ``_resolve_source_client``
    returned the active deployment's client, so ``endpoint_provision(source=
    "typo")`` provisioned at home and reported "Provisioned on source 'typo'"."""
    from r4.client import Client
    from r4.companion.site_routing import resolve_source_client

    bridge = Client()
    with pytest.raises(Exception, match="unknown_site") as refused:
        resolve_source_client(bridge, "typo")
    assert "site-a" in str(refused.value) and "bk" in str(refused.value)


# ------------------------------------------------------ --source moves ZMQ ---


def _add_zmq_source(store, *, api_key: str | None = None) -> None:
    from r4.source import Source

    store.add(
        Source(
            name="bz",
            kind="platform",
            url="https://bz.example",
            api_key=api_key,
            zmq_host="zmq.bz.example",
            zmq_control_port=6100,
        )
    )


def test_source_flag_moves_the_zmq_data_plane(deployments):
    from r4 import cli
    from r4.config import get_config

    _add_zmq_source(deployments)
    cli._apply_tui_source_override("bz")
    config = get_config()
    assert (config.umrs_server_host, config.umrs_control_port) == ("zmq.bz.example", 6100)
    assert config.gateway_url == "https://bz.example"


def test_source_flag_zmq_session_registers_at_the_source_with_its_own_jwt(deployments, monkeypatch):
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "1")
    from r4 import cli
    from r4.client import Client

    own_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib2IifQ.c2ln"
    _add_zmq_source(deployments, api_key=own_jwt)
    cli._apply_tui_source_override("bz")
    transport = Client().data._transport
    if transport is None:
        pytest.skip("pyzmq not installed")
    assert transport._config.server_host == "zmq.bz.example"
    assert transport._resolve_session_base_url() == "https://bz.example"
    assert transport._get_jwt_for_session() == own_jwt


def test_a_bound_transport_keeps_its_curve_session_in_its_own_file(deployments, monkeypatch):
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "1")
    from r4 import cli
    from r4.client import Client

    _add_zmq_source(deployments, api_key="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib2IifQ.c2ln")
    cli._apply_tui_source_override("bz")
    transport = Client().data._transport
    if transport is None:
        pytest.skip("pyzmq not installed")
    manager = transport._new_session_manager()
    assert manager._session_file.name != "curve-session.json"


def test_a_401_from_another_deployment_says_how_to_log_in_to_it(deployments, monkeypatch):
    """The generic hint (`r4 auth login`) would replace the active login."""
    from r4 import cli
    from r4.client import Client
    from r4.errors import AuthenticationError

    real_client = httpx.Client
    refused = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "no"}))

    class _Refusing(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = refused
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _Refusing)
    cli._apply_tui_source_override("b")
    with pytest.raises(AuthenticationError) as caught:
        Client().get("/gw/models")
    assert "r4 source set b --api-key-stdin" in (caught.value.hint or "")


def test_the_active_source_applies_its_own_default_model_and_project(deployments, monkeypatch):
    from r4.config import Config
    from r4.source import Source

    deployments.add(
        Source(
            name="stand",
            kind="platform",
            url="http://localhost:8080",
            api_key="STAND-KEY",
            default_model="llama3.2:3b",
            project="default",
        )
    )
    monkeypatch.setenv("R4_SOURCE", "stand")
    config = Config.from_env()
    assert (config.api_key, config.model, config.project_id) == (
        "STAND-KEY",
        "llama3.2:3b",
        "default",
    )
    assert config.umrs_username != A_USER
    monkeypatch.setenv("R4_PROJECT_ID", "explicit")
    monkeypatch.setenv("R4_MODEL", "chosen")
    config = Config.from_env()
    assert (config.model, config.project_id) == ("chosen", "explicit")


def test_a_keyed_active_source_elsewhere_gets_no_profile_identity(deployments, monkeypatch, wire):
    """Its own key, and neither the profile's project nor its user."""
    monkeypatch.setenv("R4_SOURCE", "bk")
    from r4.config import Config

    config = Config.from_env()
    assert (config.gateway_url, config.api_key) == (B_URLS["bk"], B_OWN_KEY)
    assert (config.project_id, config.umrs_username) == (None, None)
    _get_on_process_config()
    to_b = _requests_to(wire, "bk")
    assert to_b
    _assert_nothing_of_a(to_b)
