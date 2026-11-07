"""Pins for the role-agent turn engine (2026-09-28 live-work visibility)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.teamup.role_agent import RoleActivityEvent, _clip, run_role_turn
from r4.teamup.roles import AgentRole
from r4.teamup.session import TeamupSession, TeamupSessionConfig


def _session(tmp_path, *, respond_events=None):
    cfg = TeamupSessionConfig(name="t", roles=[AgentRole.PM])
    fake_client = SimpleNamespace()
    session = TeamupSession(cfg, fake_client, session_dir=tmp_path / "t")
    return session


def _fake_events_companion():
    from r4.companion.agent import CompanionEvent, CompanionEventType

    class _FakeCompanion:
        def __init__(self, *a, **kw):
            self._listeners = []

        def on_event(self, cb):
            self._listeners.append(cb)

        def send(self, context, *, extra_system, tools, stream):
            assert tools  # safe set passed in
            sequences = [
                (CompanionEventType.THINKING, "decomposing goal…", None),
                (CompanionEventType.TOOL_CALL, "", {"name": "file_read", "arguments": "hello.py"}),
                (
                    CompanionEventType.TOOL_RESULT,
                    "",
                    {"status": "ok", "result_preview": "42 B", "duration_ms": 150},
                ),
                (CompanionEventType.RESPONSE_START, "", None),
            ]
            for et, msg, data in sequences:
                for cb in self._listeners:
                    cb(CompanionEvent(event_type=et, message=msg, data=data or {}))
            return "final answer"

    return _FakeCompanion()


def _assert_tool_call_shape(tool_call) -> None:
    assert tool_call.name == "file_read"
    assert "hello.py" in tool_call.preview


def _assert_activity_arc(events, final) -> None:
    assert final == "final answer"
    kinds = [a.kind for _, a in events]
    assert "thinking" in kinds and "tool_call" in kinds and "tool_result" in kinds
    _assert_tool_call_shape(next(a for _, a in events if a.kind == "tool_call"))


def _assert_last_is_final(activities) -> None:
    assert activities[-1].kind == "final"


def test_run_role_turn_streams_activity_events(monkeypatch, tmp_path):
    session = _session(tmp_path)
    monkeypatch.setattr(
        "r4.companion.agent.ResearchCompanion",
        lambda *a, **kw: _fake_events_companion(),
    )
    events: list = []
    session._on_agent_activity = lambda role, ev: events.append((role, ev))
    final, activities = run_role_turn(
        session,
        AgentRole.PM,
        "goal text",
        system_prompt="You plan this project.",
        model="pub-model",
    )
    _assert_activity_arc(events, final)
    _assert_last_is_final(activities)


def test_tool_call_marks_current_task_and_final_clears(monkeypatch, tmp_path):
    session = _session(tmp_path)

    class _FakeCompanion:
        def __init__(self, *a, **kw):
            self._listeners = []

        def on_event(self, cb):
            self._listeners.append(cb)

        def send(self, context, *, extra_system, tools, stream):
            from r4.companion.agent import CompanionEvent, CompanionEventType

            for cb in self._listeners:
                cb(
                    CompanionEvent(
                        event_type=CompanionEventType.TOOL_CALL,
                        message="",
                        data={"name": "rag_search", "arguments": "q"},
                    )
                )
            return "done"

    monkeypatch.setattr("r4.companion.agent.ResearchCompanion", lambda *a, **kw: _FakeCompanion())

    state = session.agent_states["pm"]
    final, _acts = run_role_turn(
        session,
        AgentRole.PM,
        "g",
        system_prompt="p",
        model=None,
    )
    assert final == "done"
    assert state.current_task is None  # cleared at final (never stale)


def test_safe_tools_only_defined_subset_in_play(monkeypatch, tmp_path):
    session = _session(tmp_path)
    captured = {}

    class _FakeCompanion:
        def on_event(self, cb):
            return None

        def send(self, context, *, extra_system, tools, stream):
            captured["tools"] = tools
            return "ok"

    monkeypatch.setattr("r4.companion.agent.ResearchCompanion", lambda *a, **kw: _FakeCompanion())
    run_role_turn(session, AgentRole.PM, "g", system_prompt="p", model=None)
    names = {t.get("name") for t in captured["tools"]}
    from r4.companion.tools import _CONCURRENCY_SAFE_TOOLS

    assert names
    assert names <= _CONCURRENCY_SAFE_TOOLS
    assert "shell_exec" not in names
    assert "hpc_submit" not in names


def test_clip_is_one_line_bounded() -> None:
    assert _clip("a\nb c", 10) == "a b c"
    assert _clip("x" * 200, 96).endswith("…")
    ev = RoleActivityEvent(role="pm", kind="tool_call", name="t")
    assert ev.kind == "tool_call"


# --- ADR-092 slice 1: one agent-turn runtime, governed roles --------------------


# ``test_chat_once_factory_forwards_the_role_model_to_client_chat`` removed in
# ADR-092 slice 9: the chat-once factory left production (the native engine's
# role turns go through the AgentBroker, which carries the role's model on its
# AgentTurnRequest; pinned in test_native_through_broker.py).


def test_default_factory_forwards_the_round_hooks(monkeypatch) -> None:
    from r4.teamup import role_agent

    captured: dict = {}
    monkeypatch.setattr(
        "r4.companion.agent.ResearchCompanion", lambda *a, **kw: captured.update(kw) or "c"
    )

    def usage(_call_type, _payload):
        return None

    def pre(_call_type):
        return None

    role_agent._default_factory(
        SimpleNamespace(),
        model="m",
        temperature=0.7,
        max_tool_rounds=3,
        permission_manager=None,
        usage_callback=usage,
        pre_llm_call=pre,
    )
    assert captured["usage_callback"] is usage and captured["pre_llm_call"] is pre
    assert captured["call_type"] == "subagent" and captured["max_tool_rounds"] == 3


class _RecordingCompanion:
    def __init__(self) -> None:
        self.sends: list[tuple] = []
        self.cancel = None

    def on_event(self, cb):
        return None

    def set_turn_cancel(self, token):
        self.cancel = token

    def send(self, context, **kwargs):
        self.sends.append((context, kwargs))
        return "done"


def test_run_role_turn_delegates_to_run_agent_turn_with_identical_send(monkeypatch, tmp_path):
    from r4.teamup import role_agent

    session = _session(tmp_path)
    companion = _RecordingCompanion()
    built: list[dict] = []

    def _factory(client, **kwargs):
        built.append(kwargs)
        return companion

    session._subcompanion_factory = _factory
    delegated: list[dict] = []
    real = role_agent.run_agent_turn

    def _spy(client, **kwargs):
        delegated.append(kwargs)
        return real(client, **kwargs)

    monkeypatch.setattr(role_agent, "run_agent_turn", _spy)
    final, activities = run_role_turn(
        session, AgentRole.PM, "goal", system_prompt="plan it", model="m1", max_tool_rounds=5
    )

    assert (final, activities[-1].kind, activities[-1].role) == ("done", "final", "pm")
    assert [call["label"] for call in delegated] == ["pm"]
    (context, kwargs) = companion.sends[0]
    tools = kwargs.pop("tools")
    assert (context, kwargs, bool(tools)) == (
        "goal",
        {"extra_system": "plan it", "stream": True},
        True,
    )
    assert built == [
        {"model": "m1", "temperature": 0.7, "max_tool_rounds": 5, "permission_manager": None}
    ]


def test_full_parity_role_toolkit_excludes_team_agent_deny(tmp_path):
    """A full-parity role was offered the whole catalog, ``teamup_start``
    included: a role turn could start a nested team (ADR-092 D-6, MD-7)."""
    from r4.companion.permissions import PermissionManager, PermissionMode

    session = _session(tmp_path)
    session._permissions = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    companion = _RecordingCompanion()
    session._subcompanion_factory = lambda client, **kw: companion
    run_role_turn(session, AgentRole.PM, "g", system_prompt="p", model=None)

    names = {tool.get("name") for tool in companion.sends[0][1]["tools"]}
    assert "shell_exec" in names  # still full parity
    assert "teamup_start" not in names and "ask_user" not in names

    from r4.companion.permissions import TEAM_AGENT_DENY

    assert not names & TEAM_AGENT_DENY


def test_role_turn_without_a_host_manager_is_never_offered_team_agent_deny(tmp_path):
    """Finding 35 (ADR-092 D-6, MD-7): with no host manager the role gets the
    concurrency-safe subset, which also holds team_plan_templates and
    team_plan_result. Those are TEAM_AGENT_DENY tools, never offered to a
    team role, whatever branch built its toolkit."""
    from r4.companion.permissions import TEAM_AGENT_DENY
    from r4.companion.tools import _CONCURRENCY_SAFE_TOOLS

    session = _session(tmp_path)
    assert getattr(session, "_permissions", None) is None
    companion = _RecordingCompanion()
    session._subcompanion_factory = lambda client, **kw: companion
    run_role_turn(session, AgentRole.PM, "g", system_prompt="p", model=None)

    names = {tool.get("name") for tool in companion.sends[0][1]["tools"]}
    assert names and names <= _CONCURRENCY_SAFE_TOOLS
    assert _CONCURRENCY_SAFE_TOOLS & TEAM_AGENT_DENY  # the overlap the branch must drop
    assert not names & TEAM_AGENT_DENY


def test_role_turn_runs_on_its_own_fork_of_the_host_manager(tmp_path):
    from r4.companion.permissions import PermissionManager, PermissionMode

    session = _session(tmp_path)
    host = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    session._permissions = host
    managers: list = []

    def _factory(client, *, permission_manager, **_kw):
        managers.append(permission_manager)
        return _RecordingCompanion()

    session._subcompanion_factory = _factory
    run_role_turn(session, AgentRole.PM, "g", system_prompt="p", model=None)
    run_role_turn(session, AgentRole.PM, "g", system_prompt="p", model=None)

    first, second = managers
    assert first is not host and second is not first  # one fork per turn
    assert first.label == "pm" and first.mode is PermissionMode.ASK_ALWAYS
    assert first.check("teamup_start", {}) is False
    host.approve_for_session("file_write")
    assert first.check("file_write", {"path": "x"}) is True  # approvals are shared


def test_run_agent_turn_wires_hooks_cancel_and_label() -> None:
    from r4.teamup.role_agent import run_agent_turn

    companion = _RecordingCompanion()
    built: list[dict] = []
    seen: list = []
    cancel = object()

    def usage(_call_type, _payload):
        return None

    def _factory(client, **kwargs):
        built.append(kwargs)
        return companion

    final, activities = run_agent_turn(
        SimpleNamespace(),
        label="scan[0]#1",
        prompt="read it",
        system="you read",
        tools=[{"name": "file_read"}],
        model="m",
        permission_manager="pm",
        max_tool_rounds=4,
        usage_callback=usage,
        cancel=cancel,
        on_activity=seen.append,
        factory=_factory,
        stream=False,
    )
    assert (final, companion.cancel) == ("done", cancel)
    assert built == [
        {
            "model": "m",
            "temperature": 0.7,
            "max_tool_rounds": 4,
            "permission_manager": "pm",
            "usage_callback": usage,  # pre_llm_call unset: not passed at all
        }
    ]
    assert companion.sends == [
        ("read it", {"extra_system": "you read", "tools": [{"name": "file_read"}], "stream": False})
    ]
    assert (seen, [event.role for event in activities]) == (activities, ["scan[0]#1"])


def test_run_agent_turn_pushes_final_when_the_turn_raises() -> None:
    from r4.teamup.role_agent import run_agent_turn

    class _Broken(_RecordingCompanion):
        def send(self, context, **kwargs):
            raise RuntimeError("model down")

    seen: list = []
    with pytest.raises(RuntimeError, match="model down"):
        run_agent_turn(
            SimpleNamespace(),
            label="a",
            prompt="p",
            system="s",
            tools=[],
            model=None,
            permission_manager=None,
            on_activity=seen.append,
            factory=lambda client, **kw: _Broken(),
        )
    assert [event.kind for event in seen] == ["final"]


# --- ADR-093 D-14 (C23): a role reads at the size of the model it runs on ------
#
# A role's catalog was built with no reader, so its knowledge_query always
# asked K3 for 10 and its search_docs sent no profile, whatever model the
# role ran on. The reader is the role's own model, else the team's (the
# lead session's), else the client's configured model, as for plan agents.

READER_PROFILE = {"llama3.2:3b": "small", "kimi-k3": "large"}


class _Knowledge:
    def __init__(self) -> None:
        self.limits: list = []

    def graphs(self, **_kwargs):
        return [
            {
                "graph_id": "g-a",
                "name": "project-a",
                "kind": "project",
                "project_id": "project-a",
                "status": "active",
            }
        ]

    def query(self, query, **kwargs):
        self.limits.append(kwargs.get("limit"))
        return {"query": {"results": [], "total_results": 0}}


class _Rag:
    def __init__(self) -> None:
        self.profiles: list = []

    def retrieve(self, query, **kwargs):
        self.profiles.append(kwargs.get("profile"))
        sufficiency = SimpleNamespace(
            status="insufficient", basis="none", value=None, threshold=None, detail=""
        )
        return SimpleNamespace(sufficiency=sufficiency, passages=[], searched=0)


class _ReadingCompanion:
    """Runs one tool from the catalog it is handed, as a role's model would."""

    def __init__(self, tool: str, **arguments) -> None:
        self.tool, self.arguments = tool, arguments

    def on_event(self, cb):
        return None

    def send(self, context, *, extra_system, tools, stream):
        chosen = next(t for t in tools if t["name"] == self.tool)
        return chosen["execute"](**self.arguments)


def _reading_session(
    tmp_path, monkeypatch, *, role_model, team_model, client_model, companion, host=True
):
    """A PM role whose turn reads through ``companion``; models set at each level."""
    import r4.companion.retrieval_router as router
    from r4.companion.permissions import PermissionManager, PermissionMode

    def reader_fields(model, *, client=None):
        profile = READER_PROFILE.get(str(model))
        return {"profile": profile, "reader_model": model} if profile else {}

    monkeypatch.setattr(router, "reader_fields", reader_fields)
    client = SimpleNamespace(
        knowledge=_Knowledge(),
        rag=_Rag(),
        config=SimpleNamespace(project_id="project-a", model=client_model),
    )
    roles = {"pm": role_model} if role_model else {}
    cfg = TeamupSessionConfig(name="t", roles=[AgentRole.PM], role_models=roles)
    session = TeamupSession(cfg, client, session_dir=tmp_path / "t")
    session._session_model = team_model
    if host:
        session._permissions = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    session._subcompanion_factory = lambda client, **kw: companion
    return session, client


@pytest.mark.parametrize(
    ("role_model", "team_model", "client_model", "profile"),
    [
        pytest.param("kimi-k3", "llama3.2:3b", "llama3.2:3b", "large", id="own-large"),
        pytest.param("llama3.2:3b", "kimi-k3", "kimi-k3", "small", id="own-small"),
        pytest.param(None, "llama3.2:3b", "kimi-k3", "small", id="team-default"),
        pytest.param(None, None, "llama3.2:3b", "small", id="client-default"),
    ],
)
def test_a_roles_knowledge_query_asks_for_its_own_models_k_max(
    tmp_path, monkeypatch, role_model, team_model, client_model, profile
):
    from r4_contracts.models import RETRIEVAL_PROFILES

    session, client = _reading_session(
        tmp_path,
        monkeypatch,
        role_model=role_model,
        team_model=team_model,
        client_model=client_model,
        companion=_ReadingCompanion("knowledge_query", query="hip fracture and vitamin D"),
    )

    session._run_agent_turn_sync(AgentRole.PM)

    assert client.knowledge.limits == [RETRIEVAL_PROFILES[profile].k_max]
    # the cases tell a large reader from a small one (large 10 until ADR-093
    # MD-14; 6 since, 2026-10-13)
    assert RETRIEVAL_PROFILES["large"].k_max > RETRIEVAL_PROFILES["small"].k_max == 4


def test_a_role_without_a_host_manager_sizes_search_docs_by_its_own_model(tmp_path, monkeypatch):
    """The concurrency-safe subset holds search_docs; its cut is the role's too."""
    session, client = _reading_session(
        tmp_path,
        monkeypatch,
        role_model="llama3.2:3b",
        team_model="kimi-k3",
        client_model="kimi-k3",
        companion=_ReadingCompanion("search_docs", query="hip fracture and vitamin D"),
        host=False,
    )

    session._run_agent_turn_sync(AgentRole.PM)

    assert client.rag.profiles == ["small"]


def test_the_role_companion_still_runs_on_the_model_it_was_given(tmp_path, monkeypatch):
    """The client default sizes the reader only; the companion keeps its own
    fallback to it (a role with no model anywhere is built with model None)."""
    built: list = []
    session, _client = _reading_session(
        tmp_path,
        monkeypatch,
        role_model=None,
        team_model=None,
        client_model="llama3.2:3b",
        companion=_ReadingCompanion("knowledge_query", query="q"),
    )
    companion = session._subcompanion_factory(None)
    session._subcompanion_factory = lambda client, **kw: built.append(kw["model"]) or companion

    session._run_agent_turn_sync(AgentRole.PM)

    assert built == [None]


def test_a_role_on_a_client_whose_config_raises_still_runs_tool_free(tmp_path, monkeypatch):
    """Sizing never costs the turn: before C23 a client whose ``config`` raises
    ran the role tool-free; resolving the reader must not turn that into an
    agent error (review of the teamup leg, 2026-10-11)."""

    class _RaisingConfig:
        knowledge = _Knowledge()
        rag = _Rag()

        @property
        def config(self):
            raise RuntimeError("config not loaded")

    class _Plain:
        def on_event(self, cb):
            return None

        def send(self, context, *, extra_system, tools, stream):
            return f"ran with {len(tools)} tools"

    session, _client = _reading_session(
        tmp_path,
        monkeypatch,
        role_model=None,
        team_model=None,
        client_model=None,
        companion=_Plain(),
    )
    session.client = _RaisingConfig()

    out = session._run_agent_turn_sync(AgentRole.PM)

    assert "ERROR" not in str(out)
    assert "config not loaded" not in str(out)


def test_a_roles_search_agentic_plans_on_the_roles_own_model(tmp_path, monkeypatch):
    """The catalog built for the role's model also binds search_agentic's
    planner and synthesis to it, as for the lead session and plan agents."""
    import r4.search as search_pkg

    seen: list = []

    class _Agentic:
        def __init__(self, client, cfg):
            seen.append(cfg.model)

        async def search(self, query):
            return SimpleNamespace(
                answer="", sources=[], to_dict=lambda: {}, confidence=0.0, iterations=0
            )

    monkeypatch.setattr(search_pkg, "AgenticSearch", _Agentic)
    session, client = _reading_session(
        tmp_path,
        monkeypatch,
        role_model="kimi-k3",
        team_model=None,
        client_model="llama3.2:3b",
        companion=_ReadingCompanion("search_agentic", query="q"),
    )
    client._config = client.config  # search_agentic reads the client's private config

    session._run_agent_turn_sync(AgentRole.PM)

    assert seen == ["kimi-k3"]
