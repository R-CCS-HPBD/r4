"""CLI project context agrees with `r4 config context` and with the requests.

`_current_project_id()` read only the `r4 project switch` state, so after
`r4 auth login` (which sets the profile's project) `graph-ingest` said "No
project context" while `r4 config context` showed `default` and every request
already carried it (stand-alone stack, 2026-10-09).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4._cli import _helpers


@pytest.fixture
def selection(monkeypatch):
    state = {"switched": None, "profile": None}
    monkeypatch.setattr(
        "r4.project.get_project_manager",
        lambda: SimpleNamespace(get_current=lambda: state["switched"]),
    )
    monkeypatch.setattr(
        "r4.config.get_config", lambda: SimpleNamespace(project_id=state["profile"])
    )
    return state


def test_profile_project_is_the_fallback(selection):
    selection["profile"] = "default"
    assert _helpers._current_project_id() == "default"


def test_project_switch_still_wins(selection):
    selection["switched"] = "proj-b"
    selection["profile"] = "default"
    assert _helpers._current_project_id() == "proj-b"


def test_no_project_anywhere_is_none(selection):
    assert _helpers._current_project_id() is None


def test_config_failure_is_none(monkeypatch, selection):
    def boom():
        raise RuntimeError("no config")

    monkeypatch.setattr("r4.config.get_config", boom)
    assert _helpers._current_project_id() is None
