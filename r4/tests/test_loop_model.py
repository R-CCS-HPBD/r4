"""Which model a research loop uses, and that the start says so (2026-11-03).

Maintainer: "are loop also using the service models of r4?" A local agentic
iteration and a plan loop's runs took ``config.model`` even after ``/model``
chose another (``LocalAgentIterationExecutor(model=None)``;
``plan_default_model = client_default_model(client)``), and no start reply
named the model at all.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion import research_loop_tools as rlt
from r4.runtime.loop_model import (
    LOCAL_PLACEHOLDER,
    PLANNED_ENGINE,
    SERVER_AGENT_MODEL,
    loop_model_note,
    resolve_loop_model,
)


def _client(model: str = "profile-default") -> Any:
    client = SimpleNamespace(config=SimpleNamespace(project_id="proj-1", model=model))
    client.start_loop = MagicMock(
        return_value={"loop_id": "loop-9", "status": "running", "research_goal": "g"}
    )
    client.knowledge = MagicMock()
    return client


def test_order_is_caller_then_session_then_profile() -> None:
    client = _client()
    assert resolve_loop_model("named", "session-m", client) == "named"
    assert resolve_loop_model(None, "session-m", client) == "session-m"
    assert resolve_loop_model(" ", "", client) == "profile-default"
    assert resolve_loop_model(None, None, SimpleNamespace()) == ""


@pytest.mark.parametrize(
    ("location", "kw", "expected"),
    [
        ("umrs", {"agentic": False}, PLANNED_ENGINE),
        ("umrs", {"agentic": True}, SERVER_AGENT_MODEL),
        (
            "umrs",
            {"agentic": False, "plan": "research-round", "model": "m"},
            "m for the plan's runs",
        ),
        ("local", {"agentic": False}, LOCAL_PLACEHOLDER),
        ("local", {"agentic": True, "model": "m", "executor_bound": True}, "m"),
    ],
)
def test_the_note_names_the_model_by_where_iterations_run(location, kw, expected) -> None:
    assert loop_model_note(location, **kw).startswith(expected)


def test_hybrid_names_both_venues() -> None:
    note = loop_model_note("hybrid", agentic=True, model="m", executor_bound=True)
    assert "local iterations use m" in note and "on UMRS, chosen by the server" in note


def test_no_loop_note_claims_the_service_tier() -> None:
    for location in ("umrs", "local", "hybrid"):
        assert "svc-" not in loop_model_note(location, agentic=True)


def _start(client: Any, reader: Any = None, **kw: Any) -> dict[str, Any]:
    tools = {t["name"]: t for t in rlt.build_research_loop_tools(client, reader=reader)}
    return json.loads(tools["research_loop_start"]["execute"](research_goal="g", **kw))


def test_a_local_loop_uses_the_session_model_not_the_profile(monkeypatch) -> None:
    """The bug: after /model, a local agentic loop still ran config.model."""
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    seen: list[str] = []
    monkeypatch.setattr(rlt, "_local_executor", lambda c, t, m="": seen.append(m) or MagicMock())
    reply = _start(_client(), reader=lambda: "kimi-k3@rikyu")
    assert seen == ["kimi-k3@rikyu"]
    assert reply["model"] == "kimi-k3@rikyu"


def test_the_caller_named_model_wins(monkeypatch) -> None:
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    seen: list[str] = []
    monkeypatch.setattr(rlt, "_local_executor", lambda c, t, m="": seen.append(m) or MagicMock())
    _start(_client(), reader=lambda: "session-m", model="named-m")
    assert seen == ["named-m"]


def test_the_local_executor_receives_the_model() -> None:
    executor = rlt._local_executor(_client(), "proj-1", "kimi-k3@rikyu")
    assert executor._model == "kimi-k3@rikyu"
    assert rlt._local_executor(_client(), "proj-1")._model is None  # profile default


def test_a_umrs_agentic_start_says_the_server_chooses(monkeypatch) -> None:
    monkeypatch.setenv("R4_RUNTIME_MODE", "umrs")
    client = _client()
    reply = _start(client, reader=lambda: "kimi-k3@rikyu", agentic=True)
    assert reply["model"] == SERVER_AGENT_MODEL
    assert client.start_loop.call_args.kwargs["agentic"] is True


def test_a_failing_reader_falls_back_to_the_profile(monkeypatch) -> None:
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    seen: list[str] = []
    monkeypatch.setattr(rlt, "_local_executor", lambda c, t, m="": seen.append(m) or MagicMock())

    def broken() -> str:
        raise RuntimeError("no session")

    _start(_client(), reader=broken)
    assert seen == ["profile-default"]


def test_a_server_plan_loop_sends_the_session_model() -> None:
    from r4.runtime.api import LoopSpec
    from r4.runtime.umrs import plan_loop_fields

    spec = LoopSpec(
        project_id="proj",
        research_goal="g",
        iteration_plan="research-round",
        plan_sources=("site",),
        plan_web_policy="none",
        model="qwen3-8b@site",
    )
    fields = plan_loop_fields(_client(model="profile-default@site"), spec)
    assert fields["plan_default_model"] == "qwen3-8b@site"
    unset = plan_loop_fields(
        _client(model="profile-default@site"), LoopSpec(**{**spec.__dict__, "model": ""})
    )
    assert unset["plan_default_model"] == "profile-default@site"


def test_tui_research_start_prints_the_model_line(monkeypatch) -> None:
    from r4.tui import commands

    lines: list[str] = []
    renderer = SimpleNamespace(info=lines.append, system=lines.append, error=lines.append)
    app = SimpleNamespace(renderer=renderer)
    runtime = MagicMock()
    runtime.start_loop.return_value = SimpleNamespace(loop_id="loop-1")
    runtime.capabilities.return_value = SimpleNamespace(location="umrs")
    monkeypatch.setattr(commands, "_research_runtime_for", lambda a: runtime)
    monkeypatch.setattr(commands, "_tui_project_id", lambda a: "proj")
    commands._cmd_research_start(app, ["screen", "MOF-5"])
    assert f"model: {PLANNED_ENGINE}" in lines
