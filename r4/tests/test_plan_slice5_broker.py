"""ADR-092 slice 5 at the broker and the service: every tool call of a plan
agent runs in its workspace.

The broker wraps each offered tool so the call itself runs inside
``agent_workspace``: the engine's ``workspace`` (an isolated agent's worktree
or copy), else the run's project root. The tools are the real companion
catalog; the companion is a fake that calls them as a model would. No model
is called.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

import pytest
from r4_contracts.team_engine.services import AgentTurnRequest
from r4_contracts.team_engine.spec import BudgetSpec

from r4.chat_types import ChatResponse, Usage
from r4.companion.agent_workspace import current_workspace
from r4.companion.taint import TaintRegistry
from r4.teams.agent_broker import RoleTurnAgentBroker
from r4.teams.brokers import TokenBudgetMeter
from r4.teams.plan import spec_from_plan
from r4.teams.service import TeamService


class _Companion:
    def __init__(self, client: Any, *, script: Callable[..., Any], **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.script = script
        self.permissions = kwargs.get("permission_manager")
        if self.permissions is not None:
            self.permissions.set_taint_registry(TaintRegistry())

    def on_event(self, _callback: Any) -> None:
        return None

    def set_turn_cancel(self, _token: Any) -> None:
        return None

    def send(self, prompt: str, *, extra_system: str, tools: list, stream: bool) -> Any:
        return self.script({t["name"]: t["execute"] for t in tools})


class _Client:
    def __init__(self) -> None:
        self.preflights: list[Any] = []

    def list_models(self) -> list[str]:
        return []

    def preflight_model_route(self, model: Any) -> None:
        self.preflights.append(model)

    def chat(self, **_kwargs: Any) -> ChatResponse:
        return ChatResponse(content="{}", model="m1", usage=Usage(prompt_tokens=1))


def _broker(script: Callable[..., Any], **kw: Any) -> RoleTurnAgentBroker:
    def factory(client: Any, **kwargs: Any) -> _Companion:
        return _Companion(client, script=script, **kwargs)

    meter = TokenBudgetMeter(BudgetSpec(max_tokens=10_000))
    return RoleTurnAgentBroker(_Client(), meter=meter, run_id="run-1", factory=factory, **kw)


def _request(**metadata: Any) -> AgentTurnRequest:
    return AgentTurnRequest(
        call_key="k",
        label="impl#0",
        prompt="change it",
        tools=("file_read", "file_write", "shell_exec", "python_exec"),
        model="m1",
        metadata=metadata,
    )


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    out = {name: tmp_path / name for name in ("project", "ws", "outside")}
    for path in out.values():
        path.mkdir()
    (out["project"] / "orig.txt").write_text("project\n")
    return out


async def test_an_isolated_agents_tools_run_in_its_workspace(tree) -> None:
    seen: dict[str, Any] = {}

    def script(tools: dict[str, Any]) -> str:
        seen["write"] = tools["file_write"](path="made.txt", content="agent\n")
        seen["escape"] = tools["file_write"](path=str(tree["project"] / "orig.txt"), content="x")
        seen["pwd"] = tools["shell_exec"](command="pwd")
        seen["cwd"] = tools["python_exec"](code="import os; print(os.getcwd())")
        seen["read_root"] = tools["file_read"](path=str(tree["project"] / "orig.txt"))
        seen["after"] = current_workspace()
        return "done"

    broker = _broker(script)
    result = await broker.run_turn(
        _request(workspace=str(tree["ws"]), project_root=str(tree["project"]))
    )

    assert result.ok
    assert (tree["ws"] / "made.txt").read_text() == "agent\n"
    assert seen["escape"].startswith("Error: refused:")
    assert (tree["project"] / "orig.txt").read_text() == "project\n"
    assert seen["pwd"].strip() == os.path.realpath(tree["ws"])
    assert seen["cwd"].strip() == os.path.realpath(tree["ws"])
    assert seen["read_root"] == "project\n"  # reads may reach the project root
    assert seen["after"] is None  # set around each call, not leaked to the turn


async def test_a_non_isolated_agent_is_contained_to_the_project_root(tree) -> None:
    seen: dict[str, Any] = {}

    def script(tools: dict[str, Any]) -> str:
        seen["write"] = tools["file_write"](path="new.txt", content="agent\n")
        seen["escape"] = tools["file_write"](path=str(tree["outside"] / "x.txt"), content="x")
        return "done"

    broker = _broker(script, project_root=str(tree["project"]))
    assert (await broker.run_turn(_request())).ok
    assert (tree["project"] / "new.txt").read_text() == "agent\n"
    assert seen["escape"].startswith("Error: refused:")
    assert not (tree["outside"] / "x.txt").exists()


async def test_the_workspace_holds_on_a_pool_thread(tree) -> None:
    """The companion runs concurrency-safe tools on a pool thread, which does
    not inherit a context variable of the turn's thread."""
    seen: dict[str, Any] = {}

    def script(tools: dict[str, Any]) -> str:
        def other_thread() -> None:
            seen["pwd"] = tools["shell_exec"](command="pwd")
            seen["escape"] = tools["file_read"](path=str(tree["outside"] / "nope"))

        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
        return "done"

    broker = _broker(script)
    meta = {"workspace": str(tree["ws"]), "project_root": str(tree["project"])}
    assert (await broker.run_turn(_request(**meta))).ok
    assert seen["pwd"].strip() == os.path.realpath(tree["ws"])
    assert seen["escape"].startswith("Error: refused:")


async def test_a_named_workspace_that_is_gone_fails_the_turn_by_name(tree) -> None:
    called: list[str] = []
    broker = _broker(lambda tools: called.append("ran") or "done")
    result = await broker.run_turn(
        _request(workspace=str(tree["ws"] / "gone"), project_root=str(tree["project"]))
    )
    assert not result.ok
    assert result.reason.startswith("workspace_unavailable:")
    assert called == []  # never falls back to the process's own directory


async def test_without_any_root_tools_run_uncontained_as_before(tree) -> None:
    seen: dict[str, Any] = {}

    def script(tools: dict[str, Any]) -> str:
        seen["write"] = tools["file_write"](path=str(tree["outside"] / "x.txt"), content="x\n")
        return "done"

    assert (await _broker(script).run_turn(_request())).ok
    assert (tree["outside"] / "x.txt").read_text() == "x\n"


# ── the service pins the project root and a resume keeps it ─────────────────


def _plan() -> dict[str, Any]:
    return {
        "plan_version": 1,
        "name": "p",
        "description": "d",
        "budget": {"max_tokens": 1000},
        "phases": [
            {"id": "a", "title": "A", "steps": [{"id": "s", "kind": "agent", "prompt": "hi"}]}
        ],
    }


async def test_the_service_pins_the_project_root_and_resume_reuses_it(
    tree, tmp_path, monkeypatch
) -> None:
    from r4.teamup import role_agent

    monkeypatch.setattr(
        role_agent,
        "_default_factory",
        lambda client, **kw: _Companion(client, script=lambda tools: "ok", **kw),
    )
    runs = tmp_path / "runs"
    service = TeamService(_Client(), run_root=runs, project_root=tree["project"])
    spec, problems = spec_from_plan(_plan(), {}, project="p", default_model="m1")
    assert problems == []
    handle = await service.submit(spec, engine="plan")
    _ = [e async for e in handle.events()]

    root = os.path.realpath(tree["project"])
    manifest = json.loads((handle.run_dir / "resolution.json").read_text())
    assert manifest["project_root"] == root
    assert handle.services.agents._project_root == root

    monkeypatch.chdir(tree["outside"])  # a resume from elsewhere keeps the run's tree
    again = await TeamService(_Client(), run_root=runs).resume(handle.run_id)
    _ = [e async for e in again.events()]
    assert again.services.agents._project_root == root


async def test_the_service_defaults_the_project_root_to_the_process_directory(
    tree, tmp_path, monkeypatch
) -> None:
    from r4.teamup import role_agent

    monkeypatch.setattr(
        role_agent,
        "_default_factory",
        lambda client, **kw: _Companion(client, script=lambda tools: "ok", **kw),
    )
    monkeypatch.chdir(tree["project"])
    spec, _ = spec_from_plan(_plan(), {}, project="p", default_model="m1")
    handle = await TeamService(_Client(), run_root=tmp_path / "r").submit(spec, engine="plan")
    _ = [e async for e in handle.events()]
    assert handle.services.agents._project_root == os.path.realpath(tree["project"])
