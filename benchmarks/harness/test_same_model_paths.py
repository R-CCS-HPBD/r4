"""same_model/paths.py: the bench root is required, never a private default."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PATHS = Path(__file__).resolve().parent / "same_model" / "paths.py"
ENV = "R4_SAME_MODEL_BENCH"


def _load():
    spec = importlib.util.spec_from_file_location("same_model_paths_under_test", PATHS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_import_without_bench_root(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    mod = _load()
    assert PATHS.parents[3] == mod.REPO
    assert PATHS.parent / "smoke" == mod.SMOKE


def test_unset_bench_root_stops_with_message(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    mod = _load()
    with pytest.raises(SystemExit, match=ENV):
        _ = mod.BENCH


def test_bench_root_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV, str(tmp_path))
    mod = _load()
    root = tmp_path.resolve()
    assert root == mod.BENCH
    assert root / "r4glm" == mod.OUT
    assert root / "r4glm" / "harness" == mod.LANE
    assert root / "r4glm" / "harness" / "smoke" == mod.SMOKE_OUT


def test_bench_root_inside_repo_refused(monkeypatch):
    monkeypatch.setenv(ENV, str(PATHS.parent))
    mod = _load()
    with pytest.raises(SystemExit, match="inside the repository"):
        _ = mod.LANE


def test_unknown_attribute_is_attribute_error(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(AttributeError):
        _ = _load().NOPE
