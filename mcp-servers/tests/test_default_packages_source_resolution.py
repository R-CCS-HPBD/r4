"""Default Hatch packages must call the deployment the scientist selected.

platform-tools, rag-search and knowledge-tools resolved their gateway from
``~/.config/r4/sources.json`` by reading ``"current"`` — a key r4 never writes
(r4 uses ``"active"``) — so they silently took the FIRST keyed source, and they
ignored XDG_CONFIG_HOME. A scientist who switched to source B had their tool
calls sent to source A. The three packages share one helper; every test runs
against all three so they cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PKGS = Path(__file__).resolve().parents[1] / "hatch-registry" / "packages"
_NAMES = ["platform-tools", "rag-search", "knowledge-tools"]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"pkg_{name.replace('-', '_')}", _PKGS / name / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("R4_GATEWAY_URL", "R4_GATEWAY_TOKEN", "R4_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _write(tmp_path, data):
    d = tmp_path / "r4"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sources.json").write_text(json.dumps(data))


_TWO = {
    "sources": [
        {"name": "first", "url": "https://first.example/", "api_key": "k1"},
        {"name": "second", "url": "https://second.example", "api_key": "k2"},
    ],
}


@pytest.mark.parametrize("name", _NAMES)
def test_uses_the_active_source_not_the_first(name, tmp_path):
    _write(tmp_path, {**_TWO, "active": "second"})
    assert _load(name)._gateway_from_sources() == ("https://second.example", "k2")


@pytest.mark.parametrize("name", _NAMES)
def test_r4_source_env_wins(name, tmp_path, monkeypatch):
    _write(tmp_path, {**_TWO, "active": "second"})
    monkeypatch.setenv("R4_SOURCE", "first")
    assert _load(name)._gateway_from_sources() == ("https://first.example", "k1")


@pytest.mark.parametrize("name", _NAMES)
def test_several_sources_and_none_active_is_not_guessed(name, tmp_path):
    _write(tmp_path, _TWO)
    assert _load(name)._gateway_from_sources() == ("", "")


@pytest.mark.parametrize("name", _NAMES)
def test_honours_xdg_config_home(name, tmp_path):
    _write(tmp_path, {"sources": [{"name": "only", "url": "https://x", "api_key": "k"}]})
    assert _load(name)._gateway_from_sources() == ("https://x", "k")


@pytest.mark.parametrize("name", _NAMES)
def test_active_source_without_key_does_not_fall_back(name, tmp_path):
    _write(
        tmp_path,
        {
            "active": "public",
            "sources": [
                {"name": "first", "url": "https://first.example", "api_key": "k1"},
                {"name": "public", "url": "http://localhost:11434"},
            ],
        },
    )
    assert _load(name)._gateway_from_sources() == ("", "")
