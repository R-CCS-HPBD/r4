"""Bare-model public-source fallback in resolve_model_route (2026-10-02).

``r4 chat`` covers bare models via ``_maybe_discover_source``; the companion
bridge does not, so a bare model with a platform active source died with
gateway 400/503 even when a configured public source served the model. The
fallback in :func:`resolve_model_route` probes only ``kind=public`` sources;
platform sources stay governed, and the ADR-062 sensitive-project refusal
still fires on the resolved public route in ``r4.client``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import pytest

from r4 import model_routing
from r4.model_routing import resolve_model_route

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeConfig:
    gateway_url: str = "http://gw:8000"
    endpoint_url: Optional[str] = None
    endpoint_provider: Optional[str] = None
    endpoint_api_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_profile: Optional[str] = None
    model: str = "default-model"
    model_routes: dict = field(default_factory=dict)


@pytest.fixture()
def _env(tmp_path, monkeypatch):
    """Temp source store (platform + public) and an isolated TTL cache."""
    sources_file = tmp_path / "sources.json"
    data = {
        "sources": [
            {"name": "rccs-cloud", "kind": "platform", "url": "http://rccs:8000"},
            {
                "name": "rikyu",
                "kind": "public",
                "url": "https://api.rikyu.example",
                "api_key": "sk-x",
            },
        ],
        "active": "rccs-cloud",
    }
    sources_file.write_text(json.dumps(data))
    monkeypatch.setattr("r4.source._resolve_sources_path", lambda: sources_file)
    model_routing._PUBLIC_FALLBACK_CACHE.clear()
    yield
    model_routing._PUBLIC_FALLBACK_CACHE.clear()


def _patch_probe(monkeypatch, served: dict[str, list[str]], counter: dict | None = None):
    def _probe(source, *, timeout: float = 5):
        if counter is not None:
            counter["calls"] += 1
        return served.get(source.name, []), None

    monkeypatch.setattr(model_routing, "probe_source_models", _probe)


# ---------------------------------------------------------------------------
# resolve_model_route — bare-model public fallback
# ---------------------------------------------------------------------------


class TestPublicFallback:
    def test_bare_model_falls_back_to_public_source(self, _env, monkeypatch):
        _patch_probe(monkeypatch, {"rikyu": ["kimi-k3"]})
        route = resolve_model_route(_FakeConfig(), "kimi-k3")
        assert route.kind == "public"
        assert route.source_name == "rikyu"
        assert "api.rikyu.example" in route.base_url

    def test_platform_binding_wins_over_public_fallback(self, _env, monkeypatch):
        _patch_probe(monkeypatch, {"rikyu": ["kimi-k3"]})
        cfg = _FakeConfig(model_routes={"kimi-k3": "rccs-cloud"})
        route = resolve_model_route(cfg, "kimi-k3")
        assert route.kind == "platform"
        assert route.source_name == "rccs-cloud"
        assert route.base_url == "http://rccs:8000/gw/llm"

    def test_legacy_public_endpoint_wins_over_fallback(self, _env, monkeypatch):
        _patch_probe(monkeypatch, {"rikyu": ["kimi-k3"]})
        cfg = _FakeConfig(endpoint_url="https://api.legacy.example")
        route = resolve_model_route(cfg, "kimi-k3")
        assert route.kind == "public"
        assert route.source_name is None  # legacy endpoint, not the source
        assert route.base_url == "https://api.legacy.example"

    def test_no_public_match_returns_platform_route(self, _env, monkeypatch):
        _patch_probe(monkeypatch, {"rikyu": []})
        route = resolve_model_route(_FakeConfig(), "Bespoke-Nimble-9B")
        assert route.kind == "platform"
        assert route.source_name is None
        assert route.base_url == "http://gw:8000/gw/llm"

    def test_platform_sources_never_fallback_targets(self, _env, monkeypatch):
        # Only the platform source serves the model — no auto-fallback to it.
        _patch_probe(monkeypatch, {"rccs-cloud": ["Bespoke-Nimble-9B"]})
        route = resolve_model_route(_FakeConfig(), "Bespoke-Nimble-9B")
        assert route.kind == "platform"
        assert route.source_name is None  # default gateway, not rccs-cloud

    def test_fallback_result_cached(self, _env, monkeypatch):
        counter = {"calls": 0}
        _patch_probe(monkeypatch, {"rikyu": ["kimi-k3"]}, counter)
        first = resolve_model_route(_FakeConfig(), "kimi-k3")
        second = resolve_model_route(_FakeConfig(), "kimi-k3")
        assert first == second
        # one public source → one probe for the first resolve; second is cached
        assert counter["calls"] == 1

    def test_miss_also_cached(self, _env, monkeypatch):
        counter = {"calls": 0}
        _patch_probe(monkeypatch, {"rikyu": []}, counter)
        resolve_model_route(_FakeConfig(), "unknown-model")
        resolve_model_route(_FakeConfig(), "unknown-model")
        assert counter["calls"] == 1  # second resolve served by the miss cache
