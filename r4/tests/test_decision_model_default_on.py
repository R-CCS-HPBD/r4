"""The decision model is on by default for every user (2026-10-03, ADR-076).

On where the platform serves it, silent where it does not: a site without
the model must not pay a failing call at every harness gate, and an
explicit `/decision-model off` still wins.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.decision_tools import build_decision_tools
from r4.config import DEFAULT_DECISION_MODEL as CONFIG_DEFAULT
from r4.config import Config
from r4.decisions import settings
from r4.decisions.harness import lesson_is_real, plan_reply_hint, tool_call_escalation
from r4.decisions.settings import DEFAULT_DECISION_MODEL, decision_model_available

EXECUTE = {"A": 0.0, "B": -6.0, "C": -6.0, "D": -6.0}


class _Client:
    def __init__(self, served=(DEFAULT_DECISION_MODEL,), decision_model=DEFAULT_DECISION_MODEL):
        self.config = SimpleNamespace(decision_model=decision_model)
        self.served = served
        self.listings = 0
        self.completions = 0

    def list_gateway_models(self, *, timeout=30):
        self.listings += 1
        if isinstance(self.served, Exception):
            raise self.served
        return list(self.served)

    def text_completion(self, payload, *, timeout=None):
        self.completions += 1
        return {"choices": [{"text": "A", "logprobs": {"top_logprobs": [EXECUTE]}}], "usage": {}}


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch, tmp_path):
    monkeypatch.delenv("R4_DECISION_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.chdir(tmp_path)


def test_a_fresh_profile_has_the_decision_model_on():
    assert CONFIG_DEFAULT == DEFAULT_DECISION_MODEL
    assert Config().decision_model == DEFAULT_DECISION_MODEL
    assert Config.from_env(profile="fresh").decision_model == DEFAULT_DECISION_MODEL


def test_off_in_the_environment_still_turns_it_off(monkeypatch):
    monkeypatch.setenv("R4_DECISION_MODEL", "off")
    assert Config.from_env(profile="fresh").decision_model == ""


def test_a_served_model_is_used():
    client = _Client()
    hint = plan_reply_hint(client, "plan", "はい")
    assert hint is not None and hint.intent == "execute"
    assert client.completions == 1
    assert [t["name"] for t in build_decision_tools(client)] == ["decision_evaluate"]


def test_a_site_that_does_not_serve_it_pays_no_call():
    client, log = _Client(served=("some-chat-model",)), []
    assert plan_reply_hint(client, "plan", "はい", log=log) is None
    assert tool_call_escalation(client, "bash", {"command": "rm -rf x"}, "clean", log=log) is None
    assert lesson_is_real(client, "No, that's wrong", "done", log=log) is True
    assert client.completions == 0 and log == []
    assert build_decision_tools(client) == []
    assert client.listings == 1  # one listing, cached for the next gates


def test_could_not_tell_counts_as_available():
    client = _Client(served=ConnectionError("gateway down"))
    assert decision_model_available(client, DEFAULT_DECISION_MODEL) is True


def test_the_availability_answer_expires(monkeypatch):
    client = _Client(served=())
    assert decision_model_available(client, DEFAULT_DECISION_MODEL) is False
    client.served = (DEFAULT_DECISION_MODEL,)
    assert decision_model_available(client, DEFAULT_DECISION_MODEL) is False  # cached
    monkeypatch.setattr(settings, "AVAILABILITY_TTL_SEC", 0.0)
    assert decision_model_available(client, DEFAULT_DECISION_MODEL) is True
