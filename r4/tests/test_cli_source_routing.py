"""Pins for the non-TUI --source routing fix (2026-09-18 finding).

Before this wave, only the TUI launch path repointed the config to an
explicit --source (via `_apply_tui_source_override`); every non-TUI
subcommand silently kept the GLOBALLY active source's gateway — so
`r4 endpoint up --source rccs` POSTed to the bdrt gateway even when the
user passed --source rccs and chose the rccs profile (R4_PROFILE flips the
profile NAME but the active-source overlay kept /bdrt). The fix: the
shared override runs in `_dispatch` too.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from r4 import cli


@pytest.fixture
def registered_sources(tmp_path, monkeypatch):
    """Isolated source registry backed by a tmp file; active = bdrt."""
    from r4.source import Source, SourceStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Fresh store per test (the module holds a process-global store).
    store = SourceStore(path=tmp_path / "r4" / "sources.json")
    for name, url, kind in (
        ("bdrt-ax", "https://gw/bdrt", "platform"),
        ("rccs-ax", "https://gw/rccs", "platform"),
    ):
        src = Source(
            name=name,
            kind=kind,
            url=url,
            api_key="k",
        )
        store.add(src)
    store.set_active("bdrt-ax")
    monkeypatch.setattr("r4.source.get_source_store", lambda: store)
    monkeypatch.setattr(cli, "_source", None, raising=False)
    return store


@pytest.fixture
def _fresh_config_service(monkeypatch):
    """Break the module-global config singleton for test isolation."""
    import r4.config as cfgmod

    monkeypatch.setattr(cfgmod, "_config", None)


class TestDispatchSourceOverride:
    def test_dispatch_repoints_gateway_to_explicit_platform_source(
        self, registered_sources, _fresh_config_service, monkeypatch
    ):
        called = {}

        def fake_handle(args):
            from r4.config import get_config

            called["gateway"] = get_config().gateway_url
            return 0

        monkeypatch.setattr(
            cli, "_load_dispatch_registry", lambda: {"ep": MagicMock(handle=fake_handle)}
        )
        monkeypatch.setattr(cli, "_resolve_dispatch_command", lambda args: "ep")
        monkeypatch.setattr(cli.sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

        with pytest.raises(SystemExit):
            cli._dispatch(Namespace(command="ep", source="rccs-ax"), MagicMock())
        assert called["gateway"] == "https://gw/rccs"

    def test_dispatch_keeps_active_source_when_no_flag(
        self, registered_sources, _fresh_config_service, monkeypatch
    ):
        called = {}

        def fake_handle(args):
            from r4.config import get_config

            called["gateway"] = get_config().gateway_url
            return 0

        monkeypatch.setattr(
            cli, "_load_dispatch_registry", lambda: {"ep": MagicMock(handle=fake_handle)}
        )
        monkeypatch.setattr(cli, "_resolve_dispatch_command", lambda args: "ep")
        monkeypatch.setattr(cli.sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

        with pytest.raises(SystemExit):
            cli._dispatch(Namespace(command="ep", source=None), MagicMock())
        assert called["gateway"] == "https://gw/bdrt"

    def test_dispatch_unknown_source_exits_2(
        self, registered_sources, _fresh_config_service, monkeypatch
    ):
        monkeypatch.setattr(
            cli,
            "_load_dispatch_registry",
            lambda: {"ep": MagicMock(handle=lambda args: 0)},
        )
        monkeypatch.setattr(cli, "_resolve_dispatch_command", lambda args: "ep")

        with pytest.raises(SystemExit) as exc:
            cli._dispatch(Namespace(command="ep", source="nope-ax"), MagicMock())
        assert exc.value.code == 2


class TestSharedOverrideHelper:
    def test_platform_source_repoints_gateway_and_api_key(
        self, registered_sources, _fresh_config_service
    ):
        from r4.config import get_config

        cli._apply_tui_source_override("rccs-ax")
        cfg = get_config()
        assert cfg.gateway_url == "https://gw/rccs"
        assert cfg.api_key == "k"

    def test_public_source_sets_endpoint_temperatures(
        self, registered_sources, _fresh_config_service
    ):
        from r4.source import Source

        store = registered_sources
        store.add(Source(name="pub-ax", kind="public", url="https://api.pub/v1", api_key="pk"))
        cli._apply_tui_source_override("pub-ax")
        from r4.config import get_config

        cfg = get_config()
        assert cfg.endpoint_url == "https://api.pub/v1"
