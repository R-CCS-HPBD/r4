"""K1: ``r4 config show`` and ``r4 health`` describe the ACTIVE source.

Stabilization cycle 2 (2026-10-21): ``get_config()`` overlays only an active
platform source, so with an active public source both commands showed the
localhost:8080 platform default and the built-in rivault model, and
``r4 health`` probed (and could report "ready" for) whatever answered on
localhost:8080. A public source is now shown as itself (url, provider,
default model) and ``r4 health`` probes its model list; a platform source's
output is unchanged.
"""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import patch

import pytest

from r4._cli.config import ConfigCommand
from r4._cli.health import _run_health
from r4.config import Config, reload_config, set_config
from r4.source import Source, get_source_store


@pytest.fixture(autouse=True)
def _no_env_routing(monkeypatch):
    for name in ("R4_MODEL", "R4_SOURCE", "R4_GATEWAY_URL", "R4_ENDPOINT_URL", "R4_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield
    set_config(Config.from_env())


def _activate(source: Source) -> None:
    store = get_source_store()
    store.add(source)
    store.set_active(source.name)
    reload_config()


def _public(provider: str | None = None) -> Source:
    return Source(
        name="pub",
        kind="public",
        url="http://127.0.0.1:9/v1",
        api_key="sk-pub-own",
        provider=provider,
        default_model="fake-model",
    )


def _show(capsys) -> str:
    assert ConfigCommand().handle(Namespace(config_command="show")) == 0
    return capsys.readouterr().out


def test_config_show_describes_an_active_public_source(capsys) -> None:
    _activate(_public(provider="anthropic"))

    out = _show(capsys)

    assert "Endpoint: http://127.0.0.1:9/v1 (public)" in out
    assert "Provider: anthropic" in out
    assert "Model: fake-model" in out
    assert "Auth: configured" in out
    assert "localhost:8080" not in out
    assert "rivault" not in out


def test_config_show_keeps_the_platform_view_for_a_platform_source(capsys) -> None:
    _activate(
        Source(name="plat", kind="platform", url="http://gw.example:8080", default_model="m1")
    )

    out = _show(capsys)

    assert "Gateway: http://gw.example:8080" in out
    assert "Model: m1 (platform)" in out
    assert "Endpoint:" not in out


def _never_readiness(*_a, **_k):
    raise AssertionError("health must not probe the platform gateway for a public source")


def test_health_probes_the_active_public_source_not_localhost(capsys) -> None:
    _activate(_public())

    with (
        patch("r4.client.Client.readiness", _never_readiness),
        patch("r4.model_routing.probe_source_models", return_value=(["fake-model"], None)) as probe,
    ):
        rc = _run_health(Namespace())

    out = capsys.readouterr().out
    assert rc == 0
    assert probe.call_args.args[0].name == "pub"
    assert "Public source 'pub' is reachable (1 model(s))" in out
    assert "Endpoint: http://127.0.0.1:9/v1 (public)" in out
    assert "localhost:8080" not in out


def test_health_json_failure_names_the_public_source(capsys) -> None:
    _activate(_public())

    with (
        patch("r4.client.Client.readiness", _never_readiness),
        patch("r4.model_routing.probe_source_models", return_value=([], "Connection failed")),
    ):
        rc = _run_health(Namespace(as_json=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ready"] is False
    assert payload["error"] == "Connection failed"
    assert payload["context"]["source"] == "pub"
    assert payload["context"]["kind"] == "public"
    assert payload["context"]["endpoint_url"] == "http://127.0.0.1:9/v1"
    assert payload["context"]["model"] == "fake-model"
    assert "gateway_url" not in payload["context"]


def test_health_keeps_the_gateway_probe_for_a_platform_source(capsys) -> None:
    _activate(Source(name="plat", kind="platform", url="http://gw.example:8080"))

    with patch(
        "r4.client.Client.readiness", return_value={"ready": True, "checks": {"umrs": "ok"}}
    ) as readiness:
        rc = _run_health(Namespace(as_json=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert readiness.called
    assert payload["context"]["gateway_url"] == "http://gw.example:8080"
