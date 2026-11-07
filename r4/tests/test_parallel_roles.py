"""Pins for the parallel_roles fan-out (2026-09-29 wave B).

Default stays the sequential per-iteration pipeline; ``parallel_roles > 1``
runs role turns concurrently (bounded), with lock-serialized hook dispatch
and a guarded sync changelog. Overlap is proven by interval intersection,
never by wall-clock thresholds (CI-load honest).
"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from role_fixtures import chat_once_factory

import r4.teamup.session as session_module
from r4.teamup.comms import AgentCommunication
from r4.teamup.roles import AgentRole
from r4.teamup.session import TeamupSession, TeamupSessionConfig

ROLES = [AgentRole.PM, AgentRole.SE, AgentRole.PG]


def _session(tmp_path, *, parallel: int, monkeypatch) -> TeamupSession:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    config = TeamupSessionConfig(name="parallel-test", roles=list(ROLES), parallel_roles=parallel)
    return TeamupSession.create(config, SimpleNamespace())


def _intervals(records: dict) -> dict:
    """Role name -> (start, end) as recorded inside the fake chat."""
    return {role: (marks[0], marks[1]) for role, marks in records.items() if len(marks) == 2}


def _has_overlap(intervals: dict) -> bool:
    ordered = sorted(intervals.values())
    return any(
        a_start < b_end - 0.04 and b_start < a_end - 0.04
        for (a_start, a_end), (b_start, b_end) in zip(ordered, ordered[1:], strict=False)
    )


def _timed_recorder(records: dict, *, responder=None):
    """Thread-safe per-call (start, end) interval recorder keyed by active role."""

    def chat(**_kwargs):
        role = responder() if responder else next(iter(ROLES)).value
        records.setdefault(role, []).append(time.monotonic())
        time.sleep(0.12)
        records[role].append(time.monotonic())
        return SimpleNamespace(content=f"{role} done")

    return chat


class _RoleTaggingClient:
    """Maps each concurrent send() back to its role via the extra_system prompt."""

    PROMPT_TAGS = {
        "Project Manager (PM)": "pm",
        "System Engineer (SE)": "se",
        "Programmer (PG)": "pg",
    }

    def __init__(self, records: dict, bomb_role: str | None = None):
        self._records = records
        self._bomb = bomb_role

    def _role_of(self, extra_system: str) -> str:
        for tag, role in self.PROMPT_TAGS.items():
            if tag in (extra_system or ""):
                return role
        return "?"

    def chat(self, messages=None, **kwargs) -> SimpleNamespace:
        role = self._role_of(str(kwargs.get("system") or ""))
        if role == self._bomb:
            raise RuntimeError("bomb-role exploded")
        self._records.setdefault(role, []).append(time.monotonic())
        time.sleep(0.12)
        self._records[role].append(time.monotonic())
        return SimpleNamespace(content=f"{role} reply")


class _SendingCompanion:
    """chat-once shape that reports the role via extra_system at send time."""

    def __init__(self, client: _RoleTaggingClient):
        self._client = client

    def on_event(self, _callback) -> None:  # run_role_turn attaches its listener
        return None

    def send(self, context, *, extra_system, tools, stream):
        return self._client.chat(system=extra_system).content


def _role_tagging_factory(client: _RoleTaggingClient):
    def _factory(*_args, **_kwargs):
        return _SendingCompanion(client)

    return _factory


def test_config_parallel_roles_roundtrips() -> None:
    config = TeamupSessionConfig(name="x", parallel_roles=3)
    assert TeamupSessionConfig.from_dict(config.to_dict()).parallel_roles == 3
    default = TeamupSessionConfig(name="y")
    assert TeamupSessionConfig.from_dict(default.to_dict()).parallel_roles == 1


def test_bridge_knob_clamps_parallel_roles() -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    bridge = object.__new__(CompanionBridgeServer)
    assert bridge._teamup_knobs({"parallel_roles": 99})["parallel_roles"] == 8
    assert bridge._teamup_knobs({})["parallel_roles"] == 1


def test_parallel_async_turns_overlap_and_sequential_does_not(tmp_path, monkeypatch) -> None:
    parallel_records: dict = {}
    client = _RoleTaggingClient(parallel_records)
    team = _session(tmp_path / "par", parallel=3, monkeypatch=monkeypatch)
    team._subcompanion_factory = _role_tagging_factory(client)
    asyncio.run(team._run_agent_turns())
    assert _has_overlap(_intervals(parallel_records)), (
        f"parallel=3 must interleave role turns: {parallel_records}"
    )

    sequential_records: dict = {}
    client2 = _RoleTaggingClient(sequential_records)
    team2 = _session(tmp_path / "seq", parallel=1, monkeypatch=monkeypatch)
    team2._subcompanion_factory = _role_tagging_factory(client2)
    asyncio.run(team2._run_agent_turns())
    assert not _has_overlap(_intervals(sequential_records)), (
        f"parallel=1 must keep the pipeline order: {sequential_records}"
    )


def test_parallel_sync_turns_overlap(tmp_path, monkeypatch) -> None:
    records: dict = {}
    client = _RoleTaggingClient(records)
    team = _session(tmp_path / "sync", parallel=3, monkeypatch=monkeypatch)
    team._subcompanion_factory = _role_tagging_factory(client)
    outputs = team._run_turns_sync()
    assert set(outputs) == set(ROLES)
    assert all(reply.endswith("reply") for reply in outputs.values())
    assert _has_overlap(_intervals(records)), f"sync fan-out must interleave: {records}"


def test_parallel_error_isolation(tmp_path, monkeypatch) -> None:
    records: dict = {}
    client = _RoleTaggingClient(records, bomb_role="se")
    team = _session(tmp_path / "bomb", parallel=3, monkeypatch=monkeypatch)
    team._subcompanion_factory = _role_tagging_factory(client)
    outputs = team._run_turns_sync()
    failed = [role for role in ROLES if outputs[role].startswith("[ERROR:")]
    survived = [role for role in ROLES if role not in failed]
    assert len(failed) == 1
    assert team.agent_states["se"].status.value == "error"
    assert all(team.agent_states[role.value].status.value != "error" for role in survived)


def test_sequential_output_keeps_config_order(tmp_path, monkeypatch) -> None:
    records: dict = {}
    client = _RoleTaggingClient(records)
    team = _session(tmp_path / "order", parallel=1, monkeypatch=monkeypatch)
    team._subcompanion_factory = _role_tagging_factory(client)
    outputs = team._run_turns_sync()
    assert list(outputs) == ROLES


def test_a_one_call_factory_fans_out_unchanged(tmp_path, monkeypatch) -> None:
    """An injected one-call companion factory (a test double since slice 9,
    when the native engine moved to the AgentBroker) fans out unchanged."""
    calls: list = []

    def chat(**_kwargs):
        calls.append(1)
        return SimpleNamespace(content="chat-once reply")

    client = SimpleNamespace(chat=chat)
    team = _session(tmp_path / "legacy", parallel=2, monkeypatch=monkeypatch)
    team._subcompanion_factory = chat_once_factory(client)
    outputs = team._run_turns_sync()
    assert set(outputs) == set(ROLES) and len(calls) == 3


@pytest.mark.parametrize("writers", [8])
def test_sync_changelog_survives_concurrent_appends(tmp_path, writers: int) -> None:
    comms = AgentCommunication(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda i: comms.add_entry_sync(agent="SE", action="RESPONSE", description=f"n{i}"),
                range(writers),
            )
        )
    entries = json.loads(comms.entries_path.read_text())
    assert len(entries) == writers, "parallel appends must not lose changelog entries"
