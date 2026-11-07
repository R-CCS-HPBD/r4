"""Follow-ups to the ADR-092 slice-1 review that the fixers handed back.

- The lead's own model rounds and plan agents charge one session's usage
  counters from different threads: one lock now covers both (D-7).
- ``r4 team launch --project`` had the same gate split as ``r4 team plan
  run`` (finding 39): the ADR-062 gate checks the active project.
- An agent that names no model was journalled with model None, so a resume
  after the default model changed replayed the old model's results (D-8).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from r4.companion.session import CompanionSession


def test_session_usage_counters_share_one_lock_across_threads():
    session = CompanionSession.__new__(CompanionSession)
    first = session._usage_lock()
    seen: list[object] = []
    worker = threading.Thread(target=lambda: seen.append(session._usage_lock()))
    worker.start()
    worker.join()
    assert seen == [first]
    with first:  # re-entrant: a guarded call may record inside the same lock
        with session._usage_lock():
            pass


def test_team_launch_refuses_a_project_other_than_the_active_one(monkeypatch, capsys):
    import r4.project
    from r4._cli import teamup

    monkeypatch.setattr(
        r4.project, "get_project_manager", lambda: SimpleNamespace(get_current=lambda: "proj-a")
    )
    args = SimpleNamespace(
        spec=None,
        team=None,
        name="t",
        goal="g",
        roles="pm,se",
        engine=None,
        max_iterations=1,
        max_tokens=None,
        dry_run=True,
        project="proj-b",
    )
    with pytest.raises(SystemExit) as exit_info:
        teamup._cmd_team_launch(args, None)
    assert exit_info.value.code == 2
    assert "proj-b" in capsys.readouterr().err


def test_a_plan_spec_pins_the_model_its_unnamed_agents_run(tmp_path):
    from r4.teams.plan import spec_from_plan
    from r4.teams.service import TeamService

    raw = {
        "plan_version": 1,
        "name": "pin",
        "description": "an agent that names no model",
        "budget": {"max_tokens": 1000},
        "phases": [
            {"id": "p", "title": "P", "steps": [{"id": "a", "kind": "agent", "prompt": "x"}]}
        ],
    }
    spec, problems = spec_from_plan(raw, {}, project="p")
    assert not problems and not (spec.context or {}).get("default_model")
    client = SimpleNamespace(config=SimpleNamespace(model="llama3.2:3b"))
    service = TeamService(client, run_root=tmp_path)
    pinned = service._pin_default_model(spec)
    assert pinned.context["default_model"] == "llama3.2:3b"
    assert service._pin_default_model(pinned) is pinned  # an explicit model is kept
