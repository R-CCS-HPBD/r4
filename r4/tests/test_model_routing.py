"""Tests for cross-source model routing (parse_model_ref, resolve_model_route, etc.)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import pytest

from r4.model_routing import (
    ModelRef,
    ModelRoute,
    clear_model_route,
    parse_model_ref,
    resolve_model_route,
    update_model_route_map,
)

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
def sources_dir(tmp_path):
    """Create a temporary source store with two platform sources."""
    sources_file = tmp_path / "sources.json"
    data = {
        "sources": [
            {
                "name": "rccs-cloud",
                "kind": "platform",
                "url": "http://rccs:8000",
                "description": "RCCS Cloud",
            },
            {
                "name": "ai4s",
                "kind": "platform",
                "url": "http://ai4s:8000",
                "description": "AI4S Platform",
            },
            {
                "name": "openai",
                "kind": "public",
                "url": "https://api.openai.com",
                "description": "OpenAI",
                "api_key": "sk-test",
            },
        ],
        "active": "rccs-cloud",
    }
    sources_file.write_text(json.dumps(data))
    return sources_file


@pytest.fixture()
def _patch_source_store(sources_dir, monkeypatch):
    """Patch the source store to use the temp directory."""
    monkeypatch.setattr(
        "r4.source._resolve_sources_path",
        lambda: sources_dir,
    )
    monkeypatch.setattr(
        "r4.model_routing._resolve_sources_path_for_store",
        lambda: sources_dir,
        raising=False,
    )


# ---------------------------------------------------------------------------
# parse_model_ref
# ---------------------------------------------------------------------------


class TestParseModelRef:
    def test_simple_model(self):
        ref = parse_model_ref("qwen")
        assert ref == ModelRef(model="qwen", source=None)

    def test_model_with_slash(self):
        ref = parse_model_ref("Qwen/Qwen2.5-7B-Instruct")
        assert ref == ModelRef(model="Qwen/Qwen2.5-7B-Instruct", source=None)

    def test_model_at_source(self):
        ref = parse_model_ref("qwen@rccs-cloud")
        assert ref == ModelRef(model="qwen", source="rccs-cloud")

    def test_model_slash_at_source(self):
        ref = parse_model_ref("Qwen/Qwen2.5-7B@rccs-cloud")
        assert ref == ModelRef(model="Qwen/Qwen2.5-7B", source="rccs-cloud")

    def test_multiple_at_uses_last(self):
        ref = parse_model_ref("user@domain@source")
        assert ref == ModelRef(model="user@domain", source="source")

    def test_trailing_at_ignored(self):
        ref = parse_model_ref("model@")
        assert ref == ModelRef(model="model@", source=None)

    def test_leading_at_ignored(self):
        ref = parse_model_ref("@source")
        assert ref == ModelRef(model="@source", source=None)

    def test_empty_string(self):
        ref = parse_model_ref("")
        assert ref == ModelRef(model="", source=None)


# ---------------------------------------------------------------------------
# resolve_model_route — legacy behavior (no source store needed)
# ---------------------------------------------------------------------------


class TestResolveModelRouteLegacy:
    def test_default_to_platform(self):
        cfg = _FakeConfig()
        route = resolve_model_route(cfg, "some-model")
        assert route.kind == "platform"
        assert route.base_url == "http://gw:8000/gw/llm"
        assert route.source_name is None

    def test_public_endpoint_default(self):
        cfg = _FakeConfig(endpoint_url="https://api.openai.com")
        route = resolve_model_route(cfg, "gpt-4")
        assert route.kind == "public"
        assert route.base_url == "https://api.openai.com"

    def test_explicit_platform_route(self):
        cfg = _FakeConfig(
            endpoint_url="https://api.openai.com",
            model_routes={"gpt-4": "platform"},
        )
        route = resolve_model_route(cfg, "gpt-4")
        assert route.kind == "platform"

    def test_explicit_public_route(self):
        cfg = _FakeConfig(model_routes={"gpt-4": "public"})
        # Should raise because no public endpoint configured
        with pytest.raises(Exception):
            resolve_model_route(cfg, "gpt-4")


# ---------------------------------------------------------------------------
# resolve_model_route — source-name routes
# ---------------------------------------------------------------------------


class TestResolveModelRouteSourceName:
    def test_source_name_in_route_map(self, _patch_source_store):
        cfg = _FakeConfig(model_routes={"qwen": "ai4s"})
        route = resolve_model_route(cfg, "qwen")
        assert route.kind == "platform"
        assert route.base_url == "http://ai4s:8000/gw/llm"
        assert route.source_name == "ai4s"

    def test_at_source_notation(self, _patch_source_store):
        cfg = _FakeConfig()
        route = resolve_model_route(cfg, "qwen@ai4s")
        assert route.kind == "platform"
        assert route.base_url == "http://ai4s:8000/gw/llm"
        assert route.source_name == "ai4s"

    def test_at_source_overrides_route_map(self, _patch_source_store):
        cfg = _FakeConfig(model_routes={"qwen": "rccs-cloud"})
        route = resolve_model_route(cfg, "qwen@ai4s")
        assert route.source_name == "ai4s"

    def test_unknown_source_raises(self, _patch_source_store):
        cfg = _FakeConfig()
        with pytest.raises(Exception, match="not found"):
            resolve_model_route(cfg, "qwen@nonexistent")

    def test_public_source_route(self, _patch_source_store):
        cfg = _FakeConfig(model_routes={"gpt-4": "openai"})
        route = resolve_model_route(cfg, "gpt-4")
        assert route.kind == "public"
        assert "api.openai.com" in route.base_url
        assert route.source_name == "openai"

    def test_deleted_source_falls_through(self, _patch_source_store):
        """Route map has a source name that no longer exists — falls through."""
        cfg = _FakeConfig(model_routes={"qwen": "deleted-source"})
        route = resolve_model_route(cfg, "qwen")
        assert route.kind == "platform"
        assert route.source_name is None  # fell through to default


# ---------------------------------------------------------------------------
# update_model_route_map / clear_model_route
# ---------------------------------------------------------------------------


class TestRouteMapMutation:
    def test_update_with_source_name(self):
        cfg = _FakeConfig(model_routes={"old": "platform"})
        routes = update_model_route_map(cfg, "qwen", "rccs-cloud")
        assert routes["qwen"] == "rccs-cloud"
        assert routes["old"] == "platform"

    def test_clear_route(self):
        cfg = _FakeConfig(model_routes={"qwen": "rccs-cloud", "other": "ai4s"})
        routes = clear_model_route(cfg, "qwen")
        assert "qwen" not in routes
        assert routes["other"] == "ai4s"

    def test_clear_nonexistent(self):
        cfg = _FakeConfig(model_routes={"other": "ai4s"})
        routes = clear_model_route(cfg, "qwen")
        assert routes == {"other": "ai4s"}


# ---------------------------------------------------------------------------
# ModelRoute source_name field
# ---------------------------------------------------------------------------


class TestModelRouteSourceName:
    def test_default_none(self):
        route = ModelRoute(kind="platform", base_url="http://x")
        assert route.source_name is None

    def test_with_source(self):
        route = ModelRoute(kind="platform", base_url="http://x", source_name="foo")
        assert route.source_name == "foo"


# ---------------------------------------------------------------------------
# Cross-source aggregation (SourceModel, list_models_across_sources)
# ---------------------------------------------------------------------------


def _patch_served(monkeypatch, model_routing, served):
    """Patch the probe pass with a served-model map (kind from the store).

    The picker-facing seam moved to ``probe_sources_detailed`` (2026-10-20:
    failures stay named); ``served`` maps source name → model ids served.
    """
    from r4.model_routing import SourceProbe
    from r4.source import get_source_store

    def _probes(*, timeout=5):
        return [
            SourceProbe(
                source_name=src.name,
                kind=src.kind,
                models=tuple(served.get(src.name, ())),
            )
            for src in get_source_store().list()
        ]

    monkeypatch.setattr(model_routing, "probe_sources_detailed", _probes)


class TestCrossSourceAggregation:
    def test_source_model_qualified(self):
        from r4.model_routing import SourceModel

        sm = SourceModel(model="Qwen/Qwen2.5-7B", source_name="ai4s", kind="platform")
        assert sm.qualified == "Qwen/Qwen2.5-7B@ai4s"

    def test_count_registered_sources(self, _patch_source_store):
        from r4.model_routing import count_registered_sources

        assert count_registered_sources() == 3

    def test_list_models_across_sources(self, _patch_source_store, monkeypatch):
        from r4 import model_routing

        served = {
            "rccs-cloud": ["Qwen/Qwen2.5-7B", "shared-model"],
            "ai4s": ["Llama-3", "shared-model"],
            "openai": ["gpt-4o"],
        }
        _patch_served(monkeypatch, model_routing, served)

        result = model_routing.list_models_across_sources()
        qualified = {sm.qualified for sm in result}

        # Same bare model on two sources is kept as two distinct entries.
        assert "shared-model@rccs-cloud" in qualified
        assert "shared-model@ai4s" in qualified
        assert "Qwen/Qwen2.5-7B@rccs-cloud" in qualified
        assert "Llama-3@ai4s" in qualified
        assert "gpt-4o@openai" in qualified
        assert len(result) == 5

    def test_public_source_kind_preserved(self, _patch_source_store, monkeypatch):
        from r4 import model_routing

        _patch_served(monkeypatch, model_routing, {"openai": ["m"]})
        result = model_routing.list_models_across_sources()
        openai_models = [sm for sm in result if sm.source_name == "openai"]
        assert openai_models and openai_models[0].kind == "public"

    def test_probe_failures_stay_named(self, _patch_source_store, monkeypatch):
        """A source whose probe fails keeps its error (2026-10-20: the silent
        drop read as "no provisioned models" — a rejected key looked exactly
        like an empty endpoint). list_models_across_sources still drops it."""
        from r4 import model_routing

        monkeypatch.setattr(
            model_routing,
            "probe_source_models",
            lambda src, *, timeout=5: (
                (["m"], None) if src.name == "openai" else ([], "API key rejected")
            ),
        )
        probes = {p.source_name: p for p in model_routing.probe_sources_detailed()}
        assert probes["rccs-cloud"].error == "API key rejected"
        assert probes["ai4s"].error == "API key rejected"
        assert probes["openai"].error is None
        assert probes["openai"].models == ("m",)
        across = model_routing.list_models_across_sources()
        assert [sm.qualified for sm in across] == ["m@openai"]
