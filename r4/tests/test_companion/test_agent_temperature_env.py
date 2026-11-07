"""H14 (TB 4.0 harness plan 2026-10-24): sampling settings from the environment.

Temperature was the constructor default 0.4 with no knob, while the public TB 4.0
rows run the provider default (Z.ai: 1.0 / top_p 0.95). ``R4_COMPANION_TEMPERATURE``
and ``R4_COMPANION_TOP_P`` set them; unset keeps 0.4 and sends no ``top_p``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.agent import ResearchCompanion

_MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]


def _agent(**kwargs) -> ResearchCompanion:
    model = "glm-5.3@rikyu"
    return ResearchCompanion(
        SimpleNamespace(_config=SimpleNamespace(model=model)), model=model, **kwargs
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("R4_COMPANION_TEMPERATURE", raising=False)
    monkeypatch.delenv("R4_COMPANION_TOP_P", raising=False)


def test_env_sets_temperature_and_top_p_on_both_request_paths(monkeypatch) -> None:
    monkeypatch.setenv("R4_COMPANION_TEMPERATURE", "1.0")
    monkeypatch.setenv("R4_COMPANION_TOP_P", "0.95")
    agent = _agent()
    for payload in (
        agent._chat_payload(list(_MESSAGES), None),
        agent._build_stream_payload(list(_MESSAGES), None),
    ):
        assert payload["temperature"] == 1.0
        assert payload["top_p"] == 0.95


def test_unset_keeps_the_default_and_sends_no_top_p() -> None:
    agent = _agent()
    for payload in (
        agent._chat_payload(list(_MESSAGES), None),
        agent._build_stream_payload(list(_MESSAGES), None),
    ):
        assert payload["temperature"] == 0.4
        assert "top_p" not in payload


def test_bad_values_fall_back_to_the_defaults(monkeypatch) -> None:
    monkeypatch.setenv("R4_COMPANION_TEMPERATURE", "warm")
    monkeypatch.setenv("R4_COMPANION_TOP_P", "-1")
    agent = _agent()
    payload = agent._chat_payload(list(_MESSAGES), None)
    assert payload["temperature"] == 0.4
    assert "top_p" not in payload


def test_explicit_constructor_temperature_wins_over_env(monkeypatch) -> None:
    """Role agents pass their own temperature; the env knob is the session default."""
    monkeypatch.setenv("R4_COMPANION_TEMPERATURE", "1.0")
    assert _agent(temperature=0.2).temperature == 0.2


def test_subagent_inherits_the_sampling_settings(monkeypatch) -> None:
    monkeypatch.setenv("R4_COMPANION_TEMPERATURE", "0.9")
    monkeypatch.setenv("R4_COMPANION_TOP_P", "0.8")
    parent = _agent()
    seen: dict = {}

    def fake_send(self, task, **_kw):
        seen["temperature"], seen["top_p"] = self.temperature, self.top_p
        return "ok"

    monkeypatch.setattr(ResearchCompanion, "send", fake_send)
    parent.spawn_subagent("child task")
    assert seen == {"temperature": 0.9, "top_p": 0.8}
