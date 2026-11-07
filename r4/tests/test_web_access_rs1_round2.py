"""RS-1 round-2 review fixes for the web-access choice (ADR-099 D-2, D-9; M-4).

W2-1 the chat's ``team_plan_run(venue="server")`` sends the session's
     choice (in test_plan_server_venue.py, with the fake UMRS refusing a
     start without one).
W2-2 local command-line agents (``r4 team plan`` on the local venue,
     ``r4 teamup``, ``r4 recipe run``) take the user's choice: ``--web`` (the
     command's, else ``r4 --web``), the question on a terminal, else the
     non-interactive order; refusals name a way out that exists there.
W2-3 the Rich REPL keeps ``r4 --web`` in the process (never exported to the
     agent's shells) and asks instead of applying ``R4_WEB_ACCESS`` unasked.
W2-4 a server plan loop does not take a local session's ``default_off``
     for the user's choice.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from r4._cli import team_plan, teamup
from r4._cli import web_access_prompt as prompt_module
from r4.companion import web_access as wa
from r4.companion.web_access import WebAccess, make_web_access


@pytest.fixture(autouse=True)
def _no_ambient_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)
    monkeypatch.delenv("R4_LAUNCH_WEB_ACCESS", raising=False)


def _config(last: str = "") -> SimpleNamespace:
    return SimpleNamespace(profile="default", web_access_last=last, web_access_domains_last=[])


def _answers(*replies: str):
    queue = list(replies)
    return lambda _question: queue.pop(0)


# ── W2-2: the way out every refusal names ───────────────────────────────────


@pytest.mark.parametrize(
    "refusal",
    [
        wa.REFUSAL_OFF,
        wa.REFUSAL_UNCHOSEN,
        wa.check_url(make_web_access("allowlist", "a.org", source="flag"), "https://b.org/"),
        wa.check_search(make_web_access("allowlist", "a.org", source="flag")),
    ],
)
def test_every_refusal_names_the_console_and_the_command_line_way_out(refusal):
    """The CLI surfaces have no /web: the text named only /web there."""
    assert "/web" in refusal
    assert "r4 --web" in refusal


# ── W2-4: default_off is not the user's choice for a remote start ──────────


def test_a_plan_loop_does_not_send_the_local_default_off():
    from r4.companion.research_loop_tools import _plan_web_fields

    unchosen = make_web_access("none", source="default_off")
    assert _plan_web_fields(lambda: unchosen, "research-round") == {}
    chosen = make_web_access("none", source="flag")
    assert _plan_web_fields(lambda: chosen, "research-round") == {
        "plan_web_policy": "none",
        "plan_web_allow_domains": (),
    }
    assert wa.remote_start_choice(unchosen) is None
    assert wa.remote_start_choice(None) is None
    assert wa.remote_start_choice(chosen) is chosen


# ── W2-2: the command-line choice ───────────────────────────────────────────


def test_the_commands_own_web_flag_wins_over_the_root_flag():
    args = SimpleNamespace(
        venue_web_policy="public",
        venue_web_domains=None,
        root_web="allowlist",
        root_web_domains=["a.org"],
    )
    assert prompt_module.cli_web_flag(args, "venue_web_policy", "venue_web_domains") == (
        "public",
        [],
    )
    args.venue_web_policy = None
    assert prompt_module.cli_web_flag(args, "venue_web_policy", "venue_web_domains") == (
        "allowlist",
        ["a.org"],
    )
    assert prompt_module.cli_web_flag(None) == (None, [])


def test_a_command_on_a_terminal_asks_and_remembers_the_answer(monkeypatch, capsys):
    remembered: list[WebAccess] = []
    monkeypatch.setattr(
        prompt_module, "remember_web_access", lambda access, **_kw: remembered.append(access)
    )
    monkeypatch.setenv("R4_WEB_ACCESS", "public")  # preselects, never answers
    access = prompt_module.cli_agent_web_access(
        SimpleNamespace(), _config("public"), interactive=True, input_fn=_answers("1")
    )
    assert (access.mode, access.source) == ("none", "prompt")
    assert [a.mode for a in remembered] == ["none"]
    assert "Web access for the agent" in capsys.readouterr().err


def test_a_command_without_a_terminal_says_where_the_choice_came_from(capsys):
    access = prompt_module.cli_agent_web_access(SimpleNamespace(), _config(), interactive=False)
    assert (access.mode, access.source) == ("none", "default_off")
    assert "web access: off (no choice given" in capsys.readouterr().err
    access = prompt_module.cli_agent_web_access(
        SimpleNamespace(), _config("public"), interactive=False
    )
    assert (access.mode, access.source) == ("public", "remembered")


def test_an_invalid_command_line_choice_exits_2(capsys):
    args = SimpleNamespace(root_web="allowlist", root_web_domains=None)
    with pytest.raises(SystemExit) as exc:
        prompt_module.cli_agent_web_getter(args, _config(), interactive=False)
    assert exc.value.code == 2
    assert "--web: allowlist web access needs at least one domain" in capsys.readouterr().err


def _record_services(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """``team_plan._service`` calls, by their web-access accessor."""
    seen: list[Any] = []
    original = team_plan._service

    def _service(client: Any, web_access: Any = None) -> Any:
        seen.append(web_access)
        return original(client, web_access=web_access)

    monkeypatch.setattr(team_plan, "_service", _service)
    monkeypatch.setattr(team_plan, "_stream_to_exit", lambda _start: 0)
    return seen


def test_a_local_team_plan_run_follows_its_web_flag(tmp_path, monkeypatch):
    """``--web`` was read only by the server venue: the local agents refused
    every web call whatever the user passed."""
    from test_plan_server_venue import VenueUmrs, _client, _run_args

    seen = _record_services(monkeypatch)
    args = _run_args(tmp_path, venue="local", venue_web_policy="public")
    assert team_plan._cmd_run(args, _client(VenueUmrs())) == 0
    assert seen[-1] is not None and seen[-1]().mode == "public"
    root = _run_args(
        tmp_path,
        venue="local",
        venue_web_policy=None,
        root_web="allowlist",
        root_web_domains=[".arxiv.org"],
    )
    assert team_plan._cmd_run(root, _client(VenueUmrs())) == 0
    assert seen[-1]().domains == (".arxiv.org",)


def test_a_local_team_plan_resume_follows_its_web_flag(monkeypatch):
    seen = _record_services(monkeypatch)
    args = SimpleNamespace(
        venue="local",
        run_id="r1",
        from_phase=None,
        durable=None,
        venue_web_policy="none",
        venue_web_domains=None,
    )
    assert team_plan._cmd_resume(args, MagicMock()) == 0
    assert seen[-1] is not None and seen[-1]().mode == "none"


def test_teamup_run_gives_its_roles_the_users_choice(monkeypatch):
    from r4.teamup import session as session_module

    loaded = SimpleNamespace(
        status=SimpleNamespace(value="running"),
        get_status=lambda: {"status": "running", "iteration": 0},
    )
    monkeypatch.setattr(session_module.TeamupSession, "load", lambda *_a, **_k: loaded)
    monkeypatch.setattr(teamup, "_teamup_session_id", lambda _args: "s1")
    args = SimpleNamespace(continuous=False, iterations=0, root_web="public", root_web_domains=None)
    teamup._cmd_teamup_run(args, MagicMock())
    assert getattr(loaded, "_web_access", None) is not None
    assert loaded._web_access().mode == "public"


def test_a_started_crew_plan_runs_under_the_users_choice(monkeypatch):
    """``r4 teamup create --crew <plan crew> --start`` runs the plan locally."""
    seen = _record_services(monkeypatch)
    spec = SimpleNamespace()
    args = SimpleNamespace(start=True, root_web="none", root_web_domains=None)
    assert teamup._crew_plan_code(args, MagicMock(), spec) == 0
    assert seen[-1] is not None and seen[-1]().mode == "none"


def test_teamup_launch_gives_its_team_the_users_choice(monkeypatch):
    import r4.teams as teams_pkg

    built: list[dict[str, Any]] = []

    class _Service:
        def __init__(self, _client: Any, **kwargs: Any) -> None:
            built.append(kwargs)

        def resolve(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("stop here")

    monkeypatch.setattr(teams_pkg, "TeamService", _Service)
    monkeypatch.setattr(teamup, "_launch_project", lambda *_a: "")
    monkeypatch.setattr(
        teamup, "_team_launch_spec", lambda *_a: SimpleNamespace(agents=["pm"], plan=None)
    )
    args = SimpleNamespace(
        project=None, dry_run=False, engine=None, root_web="none", root_web_domains=None
    )
    with pytest.raises(RuntimeError, match="stop here"):
        teamup._cmd_team_launch(args, MagicMock())
    assert built[-1]["web_access"]().mode == "none"


def test_a_recipe_runs_its_web_tools_under_the_users_choice(monkeypatch):
    import r4.companion as companion_pkg
    import r4.companion.tools as tools_module
    from r4._cli import recipe as recipe_module

    asked: list[Any] = []

    def _tools(*_a: Any, web_access: Any = None, **_k: Any) -> list[dict[str, Any]]:
        asked.append(web_access)
        return []

    monkeypatch.setattr(tools_module, "get_builtin_tools", _tools)
    monkeypatch.setattr(companion_pkg, "ResearchCompanion", MagicMock())
    recipe = SimpleNamespace(model=None, tools=None, instructions="")
    args = SimpleNamespace(root_web="public", root_web_domains=None)
    assert recipe_module._run_local_recipe(recipe, "go", args) == 0
    assert asked[-1] is not None and asked[-1]().mode == "public"


# ── W2-3: the Rich REPL ─────────────────────────────────────────────────────


def _repl_session(monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any]) -> Any:
    from r4.companion.ui import repl as repl_module

    monkeypatch.setattr(repl_module.RichREPL, "run", lambda self: None)
    monkeypatch.setattr("r4.companion.model_resolution.resolve_initial_model", lambda _c, **_k: "m")
    session = MagicMock()
    session.metadata = metadata
    return session


def test_the_repl_keeps_r4_web_in_the_process(monkeypatch):
    """``r4 --web`` became ``R4_WEB_ACCESS`` in the REPL's environment: every
    shell the agent opened (and an ``r4 -p`` it started) inherited it."""
    from r4.companion import ui

    session = _repl_session(monkeypatch, {})
    monkeypatch.setenv("R4_LAUNCH_WEB_ACCESS", "public")
    with patch("r4.companion.session.CompanionSession", return_value=session):
        ui._launch_repl(MagicMock(), None, "m", None, None)
    assert "R4_WEB_ACCESS" not in os.environ
    assert "R4_LAUNCH_WEB_ACCESS" not in os.environ
    assert session.metadata["web_access"]["mode"] == "public"


def test_the_repl_asks_when_only_r4_web_access_is_set(monkeypatch):
    """An ambient ``R4_WEB_ACCESS`` became the REPL session's choice unasked."""
    session = _repl_session(monkeypatch, {})
    monkeypatch.setenv("R4_WEB_ACCESS", "public")
    access = prompt_module.apply_repl_web_access(
        session, _config(), flag=None, interactive=True, input_fn=_answers("")
    )
    assert (access.mode, access.source) == ("public", "prompt")  # preselected, then answered
    assert session.metadata["web_access"]["source"] == "prompt"


def test_the_repl_keeps_a_resumed_sessions_choice(monkeypatch):
    own = make_web_access("allowlist", "a.org", source="prompt").to_metadata()
    session = _repl_session(monkeypatch, {"web_access": own})
    asked = MagicMock(side_effect=AssertionError("asked"))
    access = prompt_module.apply_repl_web_access(
        session, _config(), flag=None, interactive=True, input_fn=asked
    )
    assert access.domains == ("a.org",)
    flag = make_web_access("none", source="flag")
    assert prompt_module.apply_repl_web_access(session, _config(), flag=flag).mode == "none"
    assert session.metadata["web_access"]["mode"] == "none"
