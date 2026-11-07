"""RS-1 review fixes for the web-access choice (ADR-099 D-2, D-9; M-4).

W1  a remote worker restoring a copy without a choice follows the choice
    the server started the sandbox with (its web tools refused before).
W2  ``r4 session start`` keeps the session's own choice (it sent the
    profile's remembered one, which could widen it silently).
W3  agents built without the session's getter (team plan agents, teamup
    roles, recipes) are guarded: the lead's choice, else ``R4_WEB_ACCESS``,
    else refuse (they ran the web unguarded).
W4  the bridge's remote start writes the choice into the copy it mirrors,
    the adopted journal copy too (the worker restored none).
W5  an ambient ``R4_WEB_ACCESS`` preselects the console's question; ``r4
    --web`` reaches the bridge as a launch-only value.
W6  ``load_session`` applies the launch preset as ``initialize`` does.
P1  a server plan loop sends the user's choice (``plan_web_policy``) and
    is refused by name without one.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bridge_rpc_harness import VENUE_ROW, Harness, make_harness

from r4.companion import session_store as store_module
from r4.companion.session_store import CompanionSessionStore
from r4.companion.web_access import WebAccess, make_web_access


@pytest.fixture(autouse=True)
def _no_ambient_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)
    monkeypatch.delenv("R4_LAUNCH_WEB_ACCESS", raising=False)


class _Client:
    """The r4 client surface a session and a remote start touch."""

    def __init__(self, *, last: str = "") -> None:
        self.config = SimpleNamespace(
            profile="default",
            gateway_url="http://gw.test",
            project_id="proj-x",
            model="m",
            auto_retry=False,
            web_access_last=last,
            web_access_domains_last=[],
        )
        self.posts: list[tuple[str, Any]] = []

    def post(self, path: str, json: Any = None, **_kw: Any) -> dict[str, Any]:
        self.posts.append((path, json))
        if path.endswith("/venue/start"):
            return {"session_id": "s", "state": "provisioning", "web_policy": json["web_policy"]}
        return {"status": "ok", "applied": True}

    def get(self, path: str, **_kw: Any) -> Any:
        return {}


# ── W1: the worker restores the started choice ──────────────────────────────


class _Rail:
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail

    def get_snapshot(self) -> dict[str, Any]:
        return self.detail

    def get_settings(self) -> dict[str, Any]:
        return {"rev": 0, "settings": {}}

    def put_snapshot(self, **_body: Any) -> dict[str, Any]:
        return {"status": "ok", "applied": True}


def _mirrored_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **metadata: Any):
    """The owner's mirrored snapshot of a remote session (``metadata`` set)."""
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "on")
    store_module.set_snapshot_enabled(None)
    client = _Client()
    owner = CompanionSessionStore(db_path=tmp_path / "owner.db")
    session = owner.create_session(client, name="remote")
    session.metadata.update({"venue": "frontend", **metadata})
    posted: list[dict[str, Any]] = []
    owner.set_mirror_poster(
        lambda _url, json=None, **_kw: posted.append(json) or {"status": "ok"}, client
    )
    owner.save(session)
    return client, {"session_id": session.session_id, **posted[-1]}


def _restore(tmp_path: Path, client: Any, detail: dict[str, Any], spec: str) -> Any:
    from r4.venue_worker import VenueWorker, WorkerEnv

    env = WorkerEnv(
        session_id=detail["session_id"],
        rail_url="http://egress:8081",
        token_file="/run/secrets/session_token",
        skip_selftest=True,
        web_access=spec,
    )
    store = CompanionSessionStore(db_path=tmp_path / "worker.db")
    worker = VenueWorker(env, _Rail(detail), token="tok", store=store)  # type: ignore[arg-type]
    worker._build_client = lambda _model: client  # type: ignore[method-assign]
    return worker.bootstrap()


def _web_fetch(session: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("r4.companion.tools._execute_agent_tool", lambda _n, _a: "RAN")
    tool = next(t for t in session.tools if t["name"] == "web_fetch")
    return str(tool["execute"](url="https://arxiv.org/abs/1"))


def test_worker_restore_without_a_choice_follows_the_server_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, detail = _mirrored_copy(tmp_path, monkeypatch)
    session = _restore(tmp_path, client, detail, "public")
    assert WebAccess.from_metadata(session.metadata).mode == "public"
    assert session.metadata["web_access"]["source"] == "server"
    assert _web_fetch(session, monkeypatch) == "RAN"


def test_worker_restore_falls_back_to_the_recorded_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server before RS-1 passes no R4_WEB_ACCESS: the runtime row the
    owner recorded at start names the policy."""
    client, detail = _mirrored_copy(
        tmp_path, monkeypatch, venue_runtime={"state": "running", "web_policy": "public"}
    )
    session = _restore(tmp_path, client, detail, "")
    assert session.web_access().mode == "public"
    assert _web_fetch(session, monkeypatch) == "RAN"


def test_worker_restore_keeps_the_copys_own_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = make_web_access("none", source="prompt").to_metadata()
    client, detail = _mirrored_copy(tmp_path, monkeypatch, web_access=own)
    session = _restore(tmp_path, client, detail, "public")
    assert session.web_access().mode == "none"
    assert _web_fetch(session, monkeypatch).startswith("Error: web access is off")


# ── W2: r4 session start keeps the session's choice ─────────────────────────


def test_session_start_keeps_the_sessions_choice_over_the_remembered_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli import sessions as sessions_cli

    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
    store_module.set_snapshot_enabled(None)
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    client = _Client(last="public")
    monkeypatch.setattr(sessions_cli, "_venue_client_and_store", lambda: (client, store))
    session = store.create_session(client, name="chose-off")
    session.metadata["web_access"] = make_web_access("none", source="prompt").to_metadata()
    store.save(session)
    args = Namespace(
        session_subcommand="start",
        session_id=session.session_id,
        venue_sources="openai-main",
        venue_default_source="",
        venue_web_policy=None,
        venue_web_domains=None,
        as_json=True,
    )
    assert sessions_cli.SessionsCommand().handle(args) == 0
    starts = [body for path, body in client.posts if path.endswith("/venue/start")]
    assert starts[0]["web_policy"] == "none"
    assert "this session's choice" in capsys.readouterr().err


# ── W3: agents without the session's getter are guarded ────────────────────


def test_an_agent_built_without_a_getter_refuses_without_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from r4.companion import tools

    monkeypatch.setattr(tools, "_execute_agent_tool", lambda _n, _a: "RAN")
    fetch = next(t for t in tools._agent_builtin_tools() if t["name"] == "web_fetch")
    assert "has not been chosen" in fetch["execute"](url="https://arxiv.org/")
    monkeypatch.setenv("R4_WEB_ACCESS", "public")
    assert fetch["execute"](url="https://arxiv.org/") == "RAN"


def test_plan_agents_get_the_leads_getter(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.companion import tools as companion_tools
    from r4.teams.agent_broker import RoleTurnAgentBroker

    seen: list[Any] = []
    monkeypatch.setattr(
        companion_tools, "get_builtin_tools", lambda *_a, **kw: seen.append(kw) or []
    )
    lead = lambda: make_web_access("none", source="prompt")  # noqa: E731
    broker = RoleTurnAgentBroker(
        _Client(),
        meter=SimpleNamespace(),  # type: ignore[arg-type]
        run_id="r1",
        web_access=lead,
    )
    turn = SimpleNamespace(fork=None, retrieval=[])
    broker._catalog(turn, None)  # type: ignore[arg-type]
    assert seen and seen[0]["web_access"] is lead


def test_service_hands_the_getter_to_brokers_and_team_sessions() -> None:
    from r4.teams.service import TeamService

    lead = lambda: make_web_access("public", source="prompt")  # noqa: E731
    service = TeamService(_Client(), web_access=lead, records=None)
    spec = SimpleNamespace(budgets=None, project=None, context={})
    broker = service._agent_broker(spec, "r1", SimpleNamespace())  # type: ignore[arg-type]
    assert broker is not None and broker._web_access is lead  # type: ignore[attr-defined]


def test_teamup_roles_get_the_leads_getter(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.companion import tools as companion_tools
    from r4.teamup import role_agent

    seen: list[Any] = []
    monkeypatch.setattr(
        companion_tools, "get_builtin_tools", lambda *_a, **kw: seen.append(kw) or []
    )
    lead = lambda: make_web_access("none", source="prompt")  # noqa: E731
    role_agent._default_toolkit(_Client(), web_access=lead)
    assert seen[0]["web_access"] is lead


def test_the_session_catalog_threads_its_getter_to_team_tools() -> None:
    from r4.companion import team_plan_tools
    from r4.companion.tools import _project_and_teamup_tools

    lead = lambda: make_web_access("none", source="prompt")  # noqa: E731
    built = _project_and_teamup_tools(_Client(), web_access=lead)  # type: ignore[arg-type]
    executor = team_plan_tools.executor_of(built)
    assert executor is not None and executor._service()._web_access is lead


# ── W4: the bridge's remote start pins the choice into the adopted copy ─────


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def test_venue_start_writes_the_choice_into_the_adopted_copy(harness: Harness) -> None:
    sent: list[dict[str, Any]] = []
    original = harness.client.post

    def post(path: str, json: Any = None, **kw: Any) -> Any:
        if path.endswith("/venue/start"):
            sent.append(json)
            return dict(VENUE_ROW)
        return original(path, json=json, **kw)

    harness.client.post = post  # type: ignore[method-assign]
    # The journal's newer copy (a remote turn nobody adopted) has no choice.
    adopted = harness.store.load(harness.sid, harness.client)
    adopted.metadata.pop("web_access", None)

    def save_adopting(session: Any) -> Any:
        harness.store.save(session)
        harness.server._session = adopted
        return adopted

    harness.server._save_adopting_newer = save_adopting  # type: ignore[method-assign]
    harness.server.session_venue_start(
        {"source_refs": ["openai-main"], "web_policy": "allowlist", "web_allow_domains": ["a.org"]}
    )
    assert (sent[0]["web_policy"], sent[0]["web_allow_domains"]) == ("allowlist", ["a.org"])
    for copy in (adopted, harness.store.load(harness.sid, harness.client)):
        chosen = WebAccess.from_metadata(copy.metadata)
        assert (chosen.mode, chosen.domains) == ("allowlist", ("a.org",))


def test_venue_start_refuses_an_invalid_choice_before_sending(harness: Harness) -> None:
    with pytest.raises(ValueError, match=r"^web_access\.invalid: "):
        harness.server.session_venue_start({"source_refs": ["s"], "web_policy": "allowlist"})


# ── W5: the console's env choice preselects; r4 --web is launch-only ───────


def test_an_ambient_env_choice_only_preselects_in_the_console(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    monkeypatch.setenv("R4_WEB_ACCESS", "off")
    server = CompanionBridgeServer(client=harness.client, store=harness.store, model="gpt-x")
    assert server._web_access_preset is None
    assert "R4_WEB_ACCESS" not in os.environ  # the agent's shells do not inherit it
    state = server.initialize({"venue": "local"})
    assert state["session"]["web_access"]["mode"] == ""  # the console asks
    got = server.session_web_access_get({})
    assert got["remembered"]["mode"] == "none" and got["preset"] is False


def test_the_launch_flag_is_a_preset_taken_out_of_the_environment(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    monkeypatch.setenv("R4_LAUNCH_WEB_ACCESS", "allowlist:pypi.org")
    server = CompanionBridgeServer(client=harness.client, store=harness.store, model="gpt-x")
    assert server._web_access_preset is not None
    assert server._web_access_preset.domains == ("pypi.org",)
    assert "R4_LAUNCH_WEB_ACCESS" not in os.environ


def test_root_web_flag_is_handed_over_launch_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4 import cli

    args = cli._build_parser().parse_args(["--web", "public"])
    try:
        cli._apply_web_flag(args)
        assert os.environ["R4_LAUNCH_WEB_ACCESS"] == "public"
        assert "R4_WEB_ACCESS" not in os.environ
    finally:
        os.environ.pop("R4_LAUNCH_WEB_ACCESS", None)


# ── W6: load_session applies the launch preset ──────────────────────────────


def test_load_session_applies_the_launch_preset(harness: Harness) -> None:
    other = harness.store.create_session(harness.client, name="no-choice-yet")
    harness.server._web_access_preset = make_web_access("allowlist", ["pypi.org"], source="flag")
    state = harness.server.load_session({"session_id": other.session_id})
    web = state["session"]["web_access"]
    assert (web["mode"], web["domains"], web["source"]) == ("allowlist", ["pypi.org"], "flag")


def test_load_session_keeps_a_sessions_own_choice(harness: Harness) -> None:
    other = harness.store.create_session(harness.client, name="chose-off")
    other.metadata["web_access"] = make_web_access("none", source="prompt").to_metadata()
    harness.store.save(other)
    harness.server._web_access_preset = make_web_access("public", source="flag")
    state = harness.server.load_session({"session_id": other.session_id})
    assert state["session"]["web_access"]["mode"] == "none"


# ── P1: a server plan loop carries the user's choice ───────────────────────


def _plan_spec(**over: Any) -> Any:
    from r4.runtime import LoopSpec

    base: dict[str, Any] = {
        "project_id": "p",
        "research_goal": "g",
        "iteration_plan": "research-round",
        "plan_sources": ("site",),
    }
    base.update(over)
    return LoopSpec(**base)


def test_plan_loop_fields_send_the_sessions_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.runtime import umrs

    monkeypatch.setattr("r4.teams.agent_broker.client_default_model", lambda _c: "m")
    spec = _plan_spec(plan_web_policy="allowlist", plan_web_allow_domains=("a.org",))
    fields = umrs.plan_loop_fields(_Client(), spec)
    assert fields["plan_web_policy"] == "allowlist"
    assert fields["plan_web_allow_domains"] == ["a.org"]
    monkeypatch.setenv("R4_WEB_ACCESS", "off")
    assert umrs.plan_loop_fields(_Client(), _plan_spec())["plan_web_policy"] == "none"


def test_plan_loop_without_a_choice_is_refused_before_sending() -> None:
    from r4.runtime.umrs import PlanWebChoiceRequired, UmrsRuntime

    class _Loops(_Client):
        def start_loop(self, *_a: Any, **_kw: Any) -> Any:
            raise AssertionError("nothing is sent without a choice")

    with pytest.raises(PlanWebChoiceRequired, match="sessionpolicy.web_choice_required"):
        UmrsRuntime(_Loops()).start_loop(_plan_spec())  # type: ignore[arg-type]


def test_the_servers_typed_refusal_is_raised_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.errors import APIError
    from r4.runtime.umrs import PlanWebChoiceRequired, UmrsRuntime

    body = json.dumps(
        {
            "detail": {
                "code": "sessionpolicy.web_choice_required",
                "message": "choose the agent's web access for this session",
            }
        }
    )

    class _Loops(_Client):
        def start_loop(self, *_a: Any, **_kw: Any) -> Any:
            raise APIError("bad request", status_code=400, response_body=body)

    monkeypatch.setattr("r4.teams.agent_broker.client_default_model", lambda _c: "m")
    with pytest.raises(PlanWebChoiceRequired) as refused:
        UmrsRuntime(_Loops()).start_loop(_plan_spec(plan_web_policy="public"))  # type: ignore[arg-type]
    assert str(refused.value).startswith("sessionpolicy.web_choice_required: choose the agent")


def test_research_loop_start_takes_the_sessions_choice() -> None:
    from r4.companion.research_loop_tools import _plan_web_fields

    lead = lambda: make_web_access("allowlist", ["a.org"], source="prompt")  # noqa: E731
    assert _plan_web_fields(lead, "research-round") == {
        "plan_web_policy": "allowlist",
        "plan_web_allow_domains": ("a.org",),
    }
    assert _plan_web_fields(lambda: WebAccess(mode=""), "research-round") == {}
    assert _plan_web_fields(lead, "") == {}
