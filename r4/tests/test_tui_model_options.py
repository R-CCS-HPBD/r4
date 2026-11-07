"""Tests for cross-source companion model options (bridge pickers)."""

from __future__ import annotations

import r4.model_routing as model_routing
import r4.tui.model_options as model_options
from r4.model_routing import SourceModel, SourceProbe


class _FakeStore:
    def __init__(self, active: str | None):
        self._active = active

    def get_active(self) -> str | None:
        return self._active


def _probes_from_models(models: list[SourceModel]) -> list[SourceProbe]:
    """Group per-model rows into successful per-source probes (errors none)."""
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[str]] = {}
    for m in models:
        key = (m.source_name, m.kind)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(m.model)
    return [
        SourceProbe(source_name=name, kind=kind, models=tuple(grouped[(name, kind)]))
        for name, kind in order
    ]


def _patch_probes(monkeypatch, *, active: str | None, models: list[SourceModel]) -> None:
    """Patch the probe pass (2026-10-20 seam: the collector reads
    ``probe_sources_detailed`` so probe failures stay named for pickers)."""
    monkeypatch.setattr("r4.source.get_source_store", lambda: _FakeStore(active))
    monkeypatch.setattr(
        model_routing,
        "probe_sources_detailed",
        lambda *, timeout=5: _probes_from_models(list(models)),
    )
    model_options.reset_extra_source_model_cache()


class TestExtraSourceOptions:
    def test_public_and_peer_sources_qualified(self, monkeypatch):
        _patch_probes(
            monkeypatch,
            active="rccs",
            models=[
                SourceModel(model="a", source_name="rccs", kind="platform"),
                SourceModel(model="llama3.2:latest", source_name="ollama", kind="public"),
                SourceModel(model="b", source_name="ai4s", kind="platform"),
            ],
        )
        options = model_options.list_extra_source_model_options()
        names = [option["name"] for option in options]
        # Active platform source skipped (covered by bare endpoint options);
        # public and peer platform sources appear qualified.
        assert names == ["llama3.2:latest@ollama", "b@ai4s"]
        assert options[0]["label"] == "llama3.2:latest@ollama (public)"
        assert options[1]["label"] == "b@ai4s (platform)"

    def test_no_sources_returns_empty(self, monkeypatch):
        _patch_probes(monkeypatch, active=None, models=[])
        assert model_options.list_extra_source_model_options() == []

    def test_probe_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr("r4.source.get_source_store", lambda: _FakeStore(None))

        def _boom(*, timeout=5):
            raise RuntimeError("probes unavailable")

        monkeypatch.setattr(model_routing, "probe_sources_detailed", _boom)
        model_options.reset_extra_source_model_cache()
        assert model_options.list_extra_source_model_options() == []
        assert model_options.list_extra_source_probe_problems() == []

    def test_cache_reused_within_ttl(self, monkeypatch):
        calls = []

        def _probe(*, timeout=5):
            calls.append(timeout)
            return [SourceProbe(source_name="ollama", kind="public", models=("m",))]

        monkeypatch.setattr("r4.source.get_source_store", lambda: _FakeStore("rccs"))
        monkeypatch.setattr(model_routing, "probe_sources_detailed", _probe)
        model_options.reset_extra_source_model_cache()
        assert len(model_options.list_extra_source_model_options()) == 1
        assert len(model_options.list_extra_source_model_options()) == 1
        assert len(calls) == 1


class TestBridgeOptions:
    def test_merges_platform_and_public(self, monkeypatch):
        _patch_probes(
            monkeypatch,
            active="rccs",
            models=[SourceModel(model="llama3.2:latest", source_name="ollama", kind="public")],
        )
        monkeypatch.setattr(
            "r4.tui.commands._collect_endpoint_model_options",
            lambda client: [{"name": "a", "source": "endpoint", "status": "ready"}],
        )
        options = model_options.collect_bridge_model_options(client=object())
        names = [option["name"] for option in options]
        assert names == ["a", "llama3.2:latest@ollama"]

    def test_dedupes_against_platform_options(self, monkeypatch):
        _patch_probes(
            monkeypatch,
            active="rccs",
            models=[SourceModel(model="x", source_name="peer", kind="platform")],
        )
        monkeypatch.setattr(
            "r4.tui.commands._collect_endpoint_model_options",
            lambda client: [{"name": "x@peer", "source": "endpoint", "status": "ready"}],
        )
        options = model_options.collect_bridge_model_options(client=object())
        assert [option["name"] for option in options] == ["x@peer"]


class TestUnifiedCollectModelOptions:
    """`r4.tui.commands.collect_model_options` (TUI /models picker) aggregates."""

    def test_includes_public_source_models(self, monkeypatch):
        import r4.tui.commands as tui_commands

        _patch_probes(
            monkeypatch,
            active="rccs",
            models=[SourceModel(model="llama3.2:latest", source_name="ollama", kind="public")],
        )
        monkeypatch.setattr(
            tui_commands,
            "_collect_endpoint_model_options",
            lambda client: [{"name": "a", "source": "endpoint", "status": "ready"}],
        )
        options = tui_commands.collect_model_options(client=object())
        assert [option["name"] for option in options] == ["a", "llama3.2:latest@ollama"]


class TestProbeProblems:
    """A failed source probe stays NAMED for pickers (field report
    2026-10-20: its silent drop read as "no provisioned models" — a rejected
    public key looked exactly like an empty endpoint list, and the console
    even sent the user to /endpoint up)."""

    def _patch(self, monkeypatch, probes):
        monkeypatch.setattr("r4.source.get_source_store", lambda: _FakeStore(None))
        monkeypatch.setattr(
            model_routing, "probe_sources_detailed", lambda *, timeout=5: list(probes)
        )
        model_options.reset_extra_source_model_cache()

    def test_failed_probe_is_a_named_problem_not_a_silent_empty(self, monkeypatch):
        self._patch(
            monkeypatch,
            [
                SourceProbe(
                    source_name="rikyu",
                    kind="public",
                    models=(),
                    error="API key rejected — store a valid key",
                ),
                SourceProbe(source_name="ollama", kind="public", models=("m",)),
            ],
        )
        problems = model_options.list_extra_source_probe_problems()
        assert problems == [{"source": "rikyu", "error": "API key rejected — store a valid key"}]
        # The healthy source still contributes its options on the same pass.
        assert [o["name"] for o in model_options.list_extra_source_model_options()] == ["m@ollama"]

    def test_active_platform_probe_failure_is_not_reported_here(self, monkeypatch):
        # The active platform gateway is the endpoint options' lane (the
        # "Backend unreachable" poller), not the extra-source picker's.
        monkeypatch.setattr("r4.source.get_source_store", lambda: _FakeStore("rccs"))
        monkeypatch.setattr(
            model_routing,
            "probe_sources_detailed",
            lambda *, timeout=5: [
                SourceProbe(source_name="rccs", kind="platform", error="connection refused"),
            ],
        )
        model_options.reset_extra_source_model_cache()
        assert model_options.list_extra_source_probe_problems() == []

    def test_options_and_problems_share_one_probe_pass(self, monkeypatch):
        calls = []

        def _probe(*, timeout=5):
            calls.append(timeout)
            return [SourceProbe(source_name="rikyu", kind="public", error="boom")]

        monkeypatch.setattr("r4.source.get_source_store", lambda: _FakeStore(None))
        monkeypatch.setattr(model_routing, "probe_sources_detailed", _probe)
        model_options.reset_extra_source_model_cache()
        assert model_options.list_extra_source_model_options() == []
        assert len(model_options.list_extra_source_probe_problems()) == 1
        assert model_options.list_extra_source_model_options() == []
        assert len(calls) == 1
