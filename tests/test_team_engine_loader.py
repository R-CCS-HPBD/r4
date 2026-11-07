"""Unit tests for the SPI plugin loader, version window, and trust policy."""

from __future__ import annotations

import importlib.metadata

import pytest

from r4_contracts.team_engine.capabilities import TrustTier
from r4_teamengine_spi import (
    IncompatibleSpiVersion,
    PluginTrustError,
    TeamEngine,
    UnknownEngineError,
    apply_site_policy,
    check_spi_compat,
    discover_descriptors,
    is_compatible,
    load,
)
from r4_teamengine_spi.testing import StubEngine
from r4_teamengine_spi.version import SPI_VERSION


class TestVersionWindow:
    @pytest.mark.parametrize(
        ("candidate", "expected"),
        [
            (SPI_VERSION, True),
            ("0.1.9", True),  # patch bumps fine within 0.x minor
            ("0.2.0", False),  # 0.x minor bump = breaking
            ("1.0.0", False),  # major bump = breaking
        ],
    )
    def test_compat(self, candidate: str, expected: bool) -> None:
        assert is_compatible(candidate) is expected

    def test_check_spi_compat_raises(self) -> None:
        desc = StubEngine().descriptor()
        bad = type(desc)(
            name=desc.name,
            version=desc.version,
            spi_version="0.99",
            trust_tier=desc.trust_tier,
            capabilities=desc.capabilities,
        )
        with pytest.raises(IncompatibleSpiVersion):
            check_spi_compat(bad)

    def test_check_spi_compat_accepts_current(self) -> None:
        check_spi_compat(StubEngine().descriptor())


class TestSitePolicy:
    def setup_method(self) -> None:
        self.desc = StubEngine().descriptor()

    def test_disabled_engine_fails_closed(self) -> None:
        with pytest.raises(PluginTrustError):
            apply_site_policy(self.desc, {"enabled": {"stub": False}})

    def test_unknown_policy_shape_defaults_allow_core(self) -> None:
        assert apply_site_policy(self.desc, None) is TrustTier.CORE
        assert apply_site_policy(self.desc, {}) is TrustTier.CORE

    def test_untrusted_tier_never_loads_in_proc(self) -> None:
        with pytest.raises(PluginTrustError):
            apply_site_policy(self.desc, {"trust_overrides": {"stub": "untrusted"}})

    def test_tier_downgrade_applies(self) -> None:
        tier = apply_site_policy(self.desc, {"trust_overrides": {"stub": "community"}})
        assert tier is TrustTier.COMMUNITY


class _EntryPointShim:
    def __init__(self, name: str, cls: type) -> None:
        self.name = name
        self._cls = cls

    def load(self) -> type:
        return self._cls


class TestLoad:
    def test_load_unknown_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: ())
        with pytest.raises(UnknownEngineError, match="nope"):
            load("nope")

    def test_load_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shim = _EntryPointShim("stub", StubEngine)
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: (shim,))
        engine = load("stub")
        assert isinstance(engine, TeamEngine)
        assert engine.descriptor().name == "stub"

    def test_load_respects_disable_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shim = _EntryPointShim("stub", StubEngine)
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: (shim,))
        with pytest.raises(PluginTrustError):
            load("stub", site_policy={"enabled": {"stub": False}})

    def test_discover_descriptors_skips_broken_plugins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Broken:
            def __init__(self) -> None:
                raise RuntimeError("broken on import-time-init")

        good = _EntryPointShim("stub", StubEngine)
        bad = _EntryPointShim("broken", _Broken)
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: (good, bad))
        found = discover_descriptors()
        assert "stub" in found
        assert "broken" not in found
