"""Unit pins for r4.source_context (onboarding plan 2026-10-15, S1 / Phase 4-A, 4-C).

- loopback detection that resists URL spellings (D-8: ``source push`` must
  recognise this machine however the URL writes it);
- ``credential_for``: the source's own key, else the login stored under its
  own name for its own URL, never the active deployment's key;
- ``config_for_source``: a new Config with the source's URL, credential, ZMQ
  endpoint, default model and project, and nothing of another deployment.
"""

from __future__ import annotations

import json

import pytest

from r4.config import Config
from r4.source import Source
from r4.source_context import (
    config_for_source,
    credential_for,
    is_loopback_url,
    same_deployment,
    stored_login,
)

LOOPBACK_URLS = (
    "http://localhost:11434",
    "http://LOCALHOST:8080",
    "https://localhost./v1",
    "http://ollama.localhost:11434",
    "http://127.0.0.1:8080",
    "http://127.1:8080",
    "http://127.0.0.2",
    "http://127.255.255.254",
    "http://0x7f.0.0.1",
    "http://0177.0.0.1",
    "http://2130706433",
    "http://0x7f000001",
    "http://[::1]:8080",
    "http://[0:0:0:0:0:0:0:1]",
    "http://[::ffff:127.0.0.1]:8080",
    "http://[::ffff:7f00:1]",
    "http://[::127.0.0.1]",
    "http://0.0.0.0:8080",
    "http://[::]:8080",
    "localhost:8080",
    "127.0.0.1",
    "http://user:pass@127.0.0.1:8080/x",
    "http://[::1%25lo]:8080",  # an IPv6 zone is not part of the address
)

REMOTE_URLS = (
    "https://api.openai.com/v1",
    "https://api.rikyu.r-ccs.riken.jp/v1",
    "http://10.0.0.31:8080",
    "http://192.168.1.10",
    "https://localhost.example.com",
    "https://127.0.0.1.nip.io",
    "http://[2001:db8::1]",
    "http://[::ffff:8.8.8.8]",
    "http://128.0.0.1",
    "",
    "not a url at all",
    # 2026-10-16: a "%" in a DNS name is part of the name httpx dials — only
    # an IPv6 literal has a zone to drop.
    "http://localhost%2e.evil.example:4000",
    "http://127.0.0.1%2e.evil.example",
    "http://127.0.0.1%25.evil.example",
)


@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_loopback_spellings_are_this_machine(url):
    assert is_loopback_url(url)


@pytest.mark.parametrize("url", REMOTE_URLS)
def test_other_hosts_are_not_loopback(url):
    assert not is_loopback_url(url)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("https://gw.example/rccs", "https://gw.example/rccs/", True),
        ("https://GW.example:443/rccs", "https://gw.example/rccs", True),
        ("http://gw.example", "http://gw.example:80", True),
        ("https://gw.example/rccs", "https://gw.example/bdrt", False),
        ("http://gw.example", "https://gw.example", False),
        ("http://localhost:8080", "http://127.0.0.1:8080", True),
        ("http://127.1:8080", "http://127.0.0.1:8080", True),
        ("http://[::ffff:127.0.0.1]:8080", "http://127.0.0.1:8080", True),
        ("http://[::1]:8080/", "http://ip6-localhost:8080", True),
        # 2026-10-15: other loopback addresses and names are other listeners.
        ("http://localhost:8080", "http://[::1]:8080/", False),
        ("http://127.0.0.2:8080", "http://127.0.0.1:8080", False),
        ("http://foo.localhost:8080", "http://localhost:8080", False),
        ("http://localhost:8080", "http://10.0.0.5:8080", False),
        ("http://localhost:8080", "http://localhost:8081", False),
        ("", "http://gw.example", False),
        (None, None, False),
        # 2026-10-16: the name before a "%" is not the deployment httpx dials.
        ("https://gw.example%2e.evil.example", "https://gw.example", False),
        ("https://gw.example%25.evil.example/rccs", "https://gw.example/rccs", False),
        ("https://gw.example%.evil.example", "https://gw.example", False),
        ("http://[::1%25lo]:8080", "http://[::1]:8080", True),
    ],
)
def test_same_deployment(a, b, same):
    assert same_deployment(a, b) is same


def test_within_deployment_reads_the_whole_name():
    from r4.source_context import within_deployment

    assert within_deployment("https://gw.example/gw/llm", "https://gw.example")
    assert not within_deployment("https://gw.example%2e.evil.example/gw/llm", "https://gw.example")


@pytest.mark.parametrize(
    "url,host",
    [
        ("https://api.example/v1", "api.example"),
        ("https://API.Example./v1", "api.example"),
        ("http://[::1%25lo]:8080", "::1"),
        ("api.example:8443", "api.example"),
        ("https://api.example%2e.evil.example/v1", None),
        ("https://user@api.example/v1", None),
        ("https://evil.example\\@api.example/v1", None),
        ("https://api.example .evil.example/v1", None),
        ("ftp://api.example/v1", None),
        ("", None),
    ],
)
def test_dialed_host_is_one_plain_host_or_none(url, host):
    from r4.source_context import dialed_host

    assert dialed_host(url) == host


# ------------------------------------------------------------ credentials ---


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    for name in ("R4_API_KEY", "R4_MODEL", "R4_PROJECT_ID", "R4_API_KEY_STAND"):
        monkeypatch.delenv(name, raising=False)
    return root / "r4"


def _write_profiles(cfg_dir, profiles: dict) -> None:
    (cfg_dir / "config.json").write_text(json.dumps({"profiles": profiles}))


def test_credential_is_the_sources_own_key(xdg, monkeypatch):
    monkeypatch.setenv("R4_API_KEY", "ACTIVE-KEY")
    src = Source(name="stand", kind="platform", url="http://localhost:8080", api_key="OWN")
    assert credential_for(src) == "OWN"


def test_credential_never_falls_back_to_the_active_key(xdg, monkeypatch):
    monkeypatch.setenv("R4_API_KEY", "ACTIVE-KEY")
    _write_profiles(xdg, {"default": {"gateway_url": "https://a.example", "api_key": "PROFILE"}})
    src = Source(name="stand", kind="platform", url="http://localhost:8080")
    assert credential_for(src) is None


def test_credential_uses_the_login_stored_under_the_sources_name(xdg, monkeypatch):
    _write_profiles(xdg, {"stand": {"gateway_url": "http://localhost:8080", "api_key": "STORED"}})
    src = Source(name="stand", kind="platform", url="http://localhost:8080/")
    assert credential_for(src) == "STORED"
    monkeypatch.setenv("R4_API_KEY_STAND", "ENV-STAND")
    assert credential_for(src) == "ENV-STAND"


def test_a_percent_lookalike_never_gets_the_deployments_login(xdg):
    """2026-10-16: ``a.example%2e.evil.example`` is dialled as that whole
    name — the login recorded for ``a.example`` is never its credential."""
    _write_profiles(xdg, {"stand": {"gateway_url": "https://a.example", "api_key": "STORED"}})
    assert credential_for(Source(name="stand", kind="platform", url="https://a.example")) == (
        "STORED"
    )
    lookalike = Source(name="stand", kind="platform", url="https://a.example%2e.evil.example")
    assert credential_for(lookalike) is None


def test_a_login_made_for_another_url_is_not_the_sources(xdg):
    """A source re-pointed at a new URL never inherits the old deployment's login."""
    _write_profiles(xdg, {"stand": {"gateway_url": "https://old.example", "api_key": "OLD"}})
    src = Source(name="stand", kind="platform", url="http://localhost:8080")
    assert credential_for(src) is None
    assert stored_login("stand") == "OLD"


# ---------------------------------------------------------------- configs ---


def _base(**overrides) -> Config:
    values = {
        "gateway_url": "https://a.example",
        "api_key": "A-KEY",
        "project_id": "proj-a",
        "umrs_username": "alice",
        "model": "a-default-model",
    }
    values.update(overrides)
    return Config(**values)


def test_another_deployment_gets_only_its_own(xdg):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib2IifQ.c2ln"
    src = Source(
        name="b",
        kind="platform",
        url="https://b.example",
        api_key=jwt,
        zmq_host="zmq.b.example",
        zmq_control_port=6100,
    )
    base = _base()
    config = config_for_source(src, base)
    assert (config.gateway_url, config.api_key, config.bound_source) == (
        "https://b.example",
        jwt,
        "b",
    )
    assert (config.project_id, config.umrs_username) == (None, "bob")
    assert (config.umrs_server_host, config.umrs_control_port) == ("zmq.b.example", 6100)
    assert (base.gateway_url, base.api_key, base.project_id) == (
        "https://a.example",
        "A-KEY",
        "proj-a",
    )


def test_the_same_deployment_keeps_the_base_identity(xdg):
    src = Source(name="a", kind="platform", url="https://a.example/")
    config = config_for_source(src, _base())
    assert (config.api_key, config.project_id, config.umrs_username) == ("A-KEY", "proj-a", "alice")
    assert config.bound_source is None


def test_source_default_model_and_project_apply(xdg):
    src = Source(
        name="stand",
        kind="platform",
        url="http://localhost:8080",
        api_key="K",
        default_model="llama3.2:3b",
        project="default",
    )
    config = config_for_source(src, _base())
    assert (config.model, config.project_id) == ("llama3.2:3b", "default")


def test_r4_model_and_r4_project_id_win_on_the_same_deployment(xdg, monkeypatch):
    monkeypatch.setenv("R4_MODEL", "chosen")
    monkeypatch.setenv("R4_PROJECT_ID", "proj-a")
    src = Source(name="a", kind="platform", url="https://a.example", default_model="m", project="p")
    config = config_for_source(src, _base(model="chosen"))
    assert (config.model, config.project_id) == ("chosen", "proj-a")


def test_a_source_project_wins_over_the_profiles_on_its_own_deployment(xdg):
    src = Source(name="a", kind="platform", url="https://a.example", project="p-of-source")
    assert config_for_source(src, _base()).project_id == "p-of-source"


def test_a_public_source_never_gets_another_endpoints_key(xdg):
    base = _base(endpoint_url="https://api.openai.com/v1", endpoint_api_key="OPENAI-KEY")
    other = Source(name="oll", kind="public", url="http://localhost:11434", provider="ollama")
    assert config_for_source(other, base).endpoint_api_key is None
    same = Source(name="oai", kind="public", url="https://api.openai.com/v1/")
    assert config_for_source(same, base).endpoint_api_key == "OPENAI-KEY"


def test_a_public_source_default_model_applies(xdg):
    src = Source(name="oll", kind="public", url="http://localhost:11434", default_model="qwen3:8b")
    assert config_for_source(src, _base()).model == "qwen3:8b"


def test_an_explicit_zmq_env_variable_wins_over_the_source(xdg, monkeypatch):
    monkeypatch.setenv("UMRS_SERVER_HOST", "tunnel.local")
    src = Source(name="b", kind="platform", url="https://b.example", zmq_control_port=6100)
    config = config_for_source(src, _base(umrs_server_host="tunnel.local"))
    assert (config.umrs_server_host, config.umrs_control_port) == ("tunnel.local", 6100)
