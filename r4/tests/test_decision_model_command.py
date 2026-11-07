"""`/decision-model on [model] | off | status` (ADR-076) — the user's switch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.session_turns import active_tools
from r4.config import ProfileConfigStore
from r4.decisions.settings import (
    DEFAULT_DECISION_MODEL,
    normalize_decision_model,
    refresh_session_tools,
)
from r4.tui.commands import get_slash_registry


class _Renderer:
    def __init__(self):
        self.lines: list[str] = []

    def info(self, text):
        self.lines.append(text)

    system = info
    error = info


class _Client:
    def __init__(self, decision_model="", served=("Bespoke-Nimble-9B",)):
        self.config = SimpleNamespace(profile="default", decision_model=decision_model)
        self._served = list(served)

    def list_gateway_models(self, *, timeout=30):
        return self._served


def _app(client, tools=None):
    session = SimpleNamespace(tools=list(tools or [{"name": "file_read"}]))
    return SimpleNamespace(client=client, renderer=_Renderer(), _companion_session=session)


@pytest.fixture(autouse=True)
def _isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("R4_DECISION_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no .r4/*.toml layers


def _run(app, line):
    get_slash_registry().dispatch(app, line)
    return app.renderer.lines[-1]


def _names(app):
    return [t["name"] for t in app._companion_session.tools]


def test_status_defaults_to_off():
    app = _app(_Client())
    assert _run(app, "/decision-model") == "decision model: off"
    assert "decision_evaluate" not in _names(app)


def test_on_uses_nimble_saves_the_profile_and_adds_the_tool_now():
    app = _app(_Client())
    out = _run(app, "/decision-model on")
    assert out == f"decision model: on — {DEFAULT_DECISION_MODEL} (served by the platform)"
    assert app.client.config.decision_model == DEFAULT_DECISION_MODEL
    assert ProfileConfigStore().load("default")["decision_model"] == DEFAULT_DECISION_MODEL
    assert _names(app).count("decision_evaluate") == 1


def test_on_with_a_named_model_and_an_unserved_warning():
    app = _app(_Client(served=()))
    out = _run(app, "/decision-model on Other-Judge-1B")
    assert "Other-Judge-1B" in out and "NOT listed by the platform" in out
    assert app.client.config.decision_model == "Other-Judge-1B"


def test_on_twice_keeps_one_tool_and_the_current_model():
    app = _app(_Client(decision_model="Custom-Judge", served=("Custom-Judge",)))
    _run(app, "/decision-model on")
    _run(app, "/decision-model on")
    assert app.client.config.decision_model == "Custom-Judge"
    assert _names(app).count("decision_evaluate") == 1


def test_off_removes_the_tool_and_persists_off():
    app = _app(_Client())
    _run(app, "/decision-model on")
    assert _run(app, "/decision-model off") == "decision model: off"
    assert "decision_evaluate" not in _names(app)
    assert ProfileConfigStore().load("default")["decision_model"] == "off"


def test_stored_off_reads_back_as_disabled(monkeypatch):
    from r4.config import Config

    ProfileConfigStore().save("default", decision_model="off")
    assert Config.from_env().decision_model == ""
    ProfileConfigStore().save("default", decision_model="Bespoke-Nimble-9B")
    assert Config.from_env().decision_model == "Bespoke-Nimble-9B"
    monkeypatch.setenv("R4_DECISION_MODEL", "none")
    assert Config.from_env().decision_model == ""


def test_env_override_is_named_in_status(monkeypatch):
    monkeypatch.setenv("R4_DECISION_MODEL", "Bespoke-Nimble-9B")
    app = _app(_Client(decision_model="Bespoke-Nimble-9B"))
    out = _run(app, "/decision-model status")
    assert "R4_DECISION_MODEL environment variable takes precedence" in out


def test_unknown_verb_prints_usage():
    app = _app(_Client())
    assert _run(app, "/decision-model maybe").startswith("usage: /decision-model")


@pytest.mark.parametrize("raw", ["", " off ", "None", "FALSE", "0", "disabled"])
def test_off_words(raw):
    assert normalize_decision_model(raw) == ""


def test_enabled_tool_survives_any_active_skill():
    client = _Client(decision_model=DEFAULT_DECISION_MODEL)
    session = SimpleNamespace(tools=[{"name": "vision_submit"}, {"name": "hpc_submit"}])
    refresh_session_tools(session, client)
    shown = [t["name"] for t in active_tools(tools=session.tools, active_tool_names={"hpc_submit"})]
    assert "decision_evaluate" in shown and "vision_submit" not in shown
