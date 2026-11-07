"""``r4 team plan`` / ``r4 teamup plan`` (ADR-092 D-9, D-10).

list and show the templates, validate (every problem, exit 2), a dry run
that names the engine, project, bound, tools, models and budget, a real run
with its exit codes, resume and result. LLM-free: a scripted
``MemoryAgentBroker`` is injected through ``TeamService._agent_broker``.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_teamengine_spi.testing import MemoryAgentBroker

from r4._cli import teamup as teamup_cli
from r4.teams import service as team_service


def _plan() -> dict[str, Any]:
    """A schema'd gatherer, then two checker replicas over its output."""
    return {
        "plan_version": 1,
        "name": "mini",
        "description": "Gather facts, then check them. Second sentence.",
        "params": {"topic": {"type": "string", "required": True}},
        "agents": {"worker": {"instructions": "Work.", "tools": ["file_read", "grep_search"]}},
        "budget": {"max_tokens": 100_000},
        "phases": [
            {
                "id": "gather",
                "title": "Gather",
                "steps": [
                    {
                        "id": "gather",
                        "kind": "agent",
                        "use": "worker",
                        "label": "gatherer",
                        "prompt": "Gather facts on {{params.topic}}",
                        "schema": {
                            "type": "object",
                            "properties": {"facts": {"type": "array"}},
                            "required": ["facts"],
                        },
                    }
                ],
            },
            {
                "id": "check",
                "title": "Check",
                "steps": [
                    {
                        "id": "check",
                        "kind": "agent",
                        "label": "checker",
                        "prompt": "Check {{steps.gather.output|json}}",
                        "fanout": {"count": 2},
                    }
                ],
            },
        ],
        "output": {"facts": "steps.gather.output.facts", "checks": "steps.check.output"},
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Runs under ``tmp_path/runs``, the active project ``proj-cli``, a fake client."""
    import r4.client
    import r4.project
    from r4.teams import service

    current = SimpleNamespace(get_current=lambda: "proj-cli")
    monkeypatch.setattr(r4.project, "get_project_manager", lambda: current)
    monkeypatch.setattr(service, "TEAM_RUNS_DIR", tmp_path / "runs")
    client = SimpleNamespace(config=SimpleNamespace(project_id=""))
    monkeypatch.setattr(r4.client, "Client", lambda *a, **k: client)
    plan_file = tmp_path / "mini.json"
    plan_file.write_text(json.dumps(_plan()))
    return SimpleNamespace(root=tmp_path / "runs", plan=plan_file, tmp=tmp_path)


@pytest.fixture
def broker(monkeypatch):
    holder: dict[str, MemoryAgentBroker] = {}

    def _use(script: dict[str, Any]) -> MemoryAgentBroker:
        holder["broker"] = MemoryAgentBroker(script)
        monkeypatch.setattr(
            team_service.TeamService,
            "_agent_broker",
            lambda self, spec, run_id, meter: holder["broker"],
        )
        return holder["broker"]

    return _use


def _r4(capsys, *argv: str, name: str = "team") -> tuple[int, str, str]:
    """``r4 <name> plan <argv…>`` through the real teamup dispatch."""
    parser = ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    teamup_cli.build_teamup_parser(sub, name=name)
    args = parser.parse_args([name, "plan", *argv])
    try:
        teamup_cli.cmd_teamup(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out, err = capsys.readouterr()
    return code, out, err


# ── list / show ─────────────────────────────────────────────────────────────


def test_both_spellings_list_the_templates_with_params(env, capsys) -> None:
    for name in ("team", "teamup"):
        code, out, _err = _r4(capsys, "list", name=name)
        assert code == 0
        assert "adversarial-review" in out
        assert "target: string (required)" in out
        assert "skeptics: integer = 3" in out
    code, out, _err = _r4(capsys, "list", "--json")
    assert code == 0
    assert any(row["name"] == "adversarial-review" for row in json.loads(out))


def test_show_prints_the_json_the_params_and_the_expanded_bound(env, capsys) -> None:
    code, out, _err = _r4(capsys, "show", "adversarial-review")
    assert code == 0
    assert not _missing(
        out,
        '"name": "adversarial-review"',
        "params:",
        "max_findings: integer = 5",
        "verify: Adversarially verify [verify__votes, verify]",
        "agent bound: 16",  # 1 finder + 5 findings x 3 skeptics
        "placeholders for target",
    )


def test_show_binds_given_args_and_names_an_unknown_template(env, capsys) -> None:
    code, out, _err = _r4(
        capsys, "show", "adversarial-review", "--arg", "max_findings=2", "--arg", "skeptics=1"
    )
    assert code == 0 and "agent bound: 3" in out
    code, _out, err = _r4(capsys, "show", "no-such-template")
    assert code == 2 and "no plan template" in err


# ── validate ────────────────────────────────────────────────────────────────


def test_validate_prints_every_problem_and_exits_2(env, capsys) -> None:
    bad = _plan()
    bad["budget"] = {}
    bad["concurrency"] = 99
    bad["phases"][1]["steps"][0]["kind"] = "agnet"
    path = env.tmp / "bad.json"
    path.write_text(json.dumps(bad))
    code, out, _err = _r4(capsys, "validate", str(path), "--arg", "topic=x")
    assert code == 2
    assert "invalid: 3 problem(s)" in out
    for needle in ("budget.max_tokens", "concurrency", "phases[1].steps[0].kind"):
        assert needle in out


def test_validate_accepts_args_json_and_a_valid_plan_exits_0(env, capsys) -> None:
    args_file = env.tmp / "args.json"
    args_file.write_text(json.dumps({"topic": "x"}))
    code, out, _err = _r4(capsys, "validate", str(env.plan), "--args-json", f"@{args_file}")
    assert code == 0
    assert "valid: mini: 2 phase(s), 2 step(s), agent bound 3" in out
    code, out, _err = _r4(capsys, "validate", str(env.plan))
    assert code == 2 and "params.topic" in out
    code, _out, err = _r4(capsys, "validate", str(env.plan), "--arg", "novalue")
    assert code == 2 and "K=V" in err
    code, _out, err = _r4(capsys, "validate", str(env.tmp / "missing.json"))
    assert code == 2 and "no plan file" in err


# ── run --dry-run ───────────────────────────────────────────────────────────


def test_dry_run_names_engine_project_bound_tools_models_and_budget(env, broker, capsys) -> None:
    used = broker({})
    code, out, _err = _r4(
        capsys, "run", str(env.plan), "--arg", "topic=x", "--dry-run", "--model", "small-1"
    )
    assert code == 0
    assert "project:  proj-cli" in out.splitlines()
    assert not _missing(
        out,
        "engine:   plan (",
        "gather: Gather [gather]",
        "check: Check [check]",
        "agent bound: 3, concurrency 4",
        "models: small-1",
        "agent gatherer (step gather): profile worker; model small-1; "
        "tools file_read, grep_search; 1 agent(s)",
        "agent checker (step check): profile (default); model small-1; "
        "tools file_read, grep_search, glob_search, list_dir; 2 agent(s)",
        "budget:   max_tokens=100000",
        "fail-closed",
        "nothing executed",
    )
    assert used.calls == []
    assert not env.root.exists() or not any(env.root.iterdir())


def test_dry_run_names_a_step_model_over_its_profiles(env, capsys) -> None:
    """Findings 0, 29, 42 (ADR-092 D-9): the dry run listed profile models
    only, so a step's own model (the one that runs) never appeared and the
    profile's (which never runs) did."""
    plan = _plan()
    plan["agents"]["worker"]["model"] = "local-small"
    plan["phases"][0]["steps"][0]["model"] = "gpt-5@openai"
    plan["phases"][1]["steps"][0]["model"] = "claude-x@anthropic"
    path = env.tmp / "override.json"
    path.write_text(json.dumps(plan))
    code, out, _err = _r4(capsys, "run", str(path), "--arg", "topic=x", "--dry-run")
    assert code == 0
    assert "models: gpt-5@openai, claude-x@anthropic" in out.splitlines()
    assert "local-small" not in out
    assert "agent checker (step check): profile (default); model claude-x@anthropic" in out


@pytest.mark.parametrize("extra", [["--dry-run"], []], ids=["dry-run", "run"])
def test_project_flag_must_name_the_active_project(env, broker, capsys, extra) -> None:
    """Finding 39 (ADR-092 D-6): the ADR-062 gate and the model preflight
    check the ACTIVE project, so ``--project`` naming another one labelled
    the run with a project its gate never checked (a public route was not
    refused for a sensitive ``--project``). Refused as invalid input."""
    used = broker({"gatherer": {"facts": ["a"]}, "checker": {"ok": True}})
    code, _out, err = _r4(
        capsys, "run", str(env.plan), "--arg", "topic=x", "--project", "proj-x", *extra
    )
    assert code == 2 and "not the active project (proj-cli)" in err
    assert "r4 project switch proj-x" in err
    assert used.calls == []
    assert not env.root.exists() or not any(env.root.iterdir())


def test_project_flag_naming_the_active_project_runs(env, capsys) -> None:
    code, out, _err = _r4(
        capsys, "run", str(env.plan), "--arg", "topic=x", "--dry-run", "--project", "proj-cli"
    )
    assert code == 0 and "project:  proj-cli" in out.splitlines()


def test_project_flag_without_an_active_project_is_refused(env, monkeypatch, capsys) -> None:
    import r4.project

    nothing = SimpleNamespace(get_current=lambda: None)
    monkeypatch.setattr(r4.project, "get_project_manager", lambda: nothing)
    code, _out, err = _r4(
        capsys, "run", str(env.plan), "--arg", "topic=x", "--dry-run", "--project", "proj-x"
    )
    assert code == 2 and "not the active project (none)" in err


def test_dry_run_without_a_project_says_none_never_cli(env, monkeypatch, capsys) -> None:
    import r4.project

    nothing = SimpleNamespace(get_current=lambda: None)
    monkeypatch.setattr(r4.project, "get_project_manager", lambda: nothing)
    code, out, err = _r4(capsys, "run", str(env.plan), "--arg", "topic=x", "--dry-run")
    assert code == 0
    assert "project:  (none)" in out.splitlines()
    assert "no active project" in err


def test_run_refuses_an_invalid_plan_with_exit_2(env, broker, capsys) -> None:
    used = broker({})
    code, out, _err = _r4(capsys, "run", str(env.plan), "--max-tokens", "0", "--arg", "topic=x")
    assert code == 2
    assert "budget.max_tokens" in out
    assert used.calls == []


# ── a real run ──────────────────────────────────────────────────────────────


def test_run_streams_progress_and_exits_0_on_success(env, broker, capsys) -> None:
    used = broker({"gatherer": {"facts": ["a", "b"]}, "checker": {"ok": True}})
    code, out, _err = _r4(capsys, "run", str(env.plan), "--arg", "topic=x")
    assert code == 0
    assert not _missing(
        out,
        "[phase] gather: Gather",
        "[phase] check: Check",
        "[agent] gatherer ok 100 tokens",
        "[agent] checker#1 ok 100 tokens",
        "[budget] spent 300 tokens, 3 agent(s) started",
        "status:  success",
    )
    assert len(used.calls) == 3


def test_a_run_writes_its_result_under_the_run_root_with_the_project(env, broker, capsys) -> None:
    broker({"gatherer": {"facts": ["a", "b"]}, "checker": {"ok": True}})
    _code, out, _err = _r4(capsys, "run", str(env.plan), "--arg", "topic=x")
    run_dir = Path(out.split("run_dir: ", 1)[1].splitlines()[0])
    assert run_dir.parent == env.root
    result = json.loads((run_dir / "result.json").read_text())
    assert result["output"]["facts"] == ["a", "b"]
    assert json.loads((run_dir / "resolution.json").read_text())["project"] == "proj-cli"


def test_a_partial_run_exits_0_and_a_failed_run_exits_1(env, broker, capsys) -> None:
    broker({"gatherer": {"facts": ["a"]}, "checker": None})
    code, out, _err = _r4(capsys, "run", str(env.plan), "--arg", "topic=x")
    assert code == 0
    assert "[agent] checker#0 null(scripted_null)" in out
    assert "status:  partial" in out
    broker({})  # every agent unscripted: no call succeeds
    code, out, err = _r4(capsys, "run", str(env.plan), "--arg", "topic=x")
    assert code == 1
    assert "status:  failed" in out
    assert "no agent call succeeded" in err


def _run_once(capsys, env) -> str:
    _code, out, _err = _r4(capsys, "run", str(env.plan), "--arg", "topic=x")
    return out.split("run_id: ", 1)[1].splitlines()[0].strip()


def test_resume_replays_every_unchanged_call(env, broker, capsys) -> None:
    used = broker({"gatherer": {"facts": ["a", "b"]}, "checker": {"ok": True}})
    run_id = _run_once(capsys, env)
    calls = len(used.calls)
    code, out, _err = _r4(capsys, "resume", run_id)
    assert code == 0
    assert not _missing(out, "[agent] gatherer ok (replayed from the journal)", "attempt-2")
    assert len(used.calls) == calls
    code, _out, _err = _r4(capsys, "resume", "no-such-run")
    assert code == 2


def test_resume_from_an_unknown_phase_is_invalid_input(env, broker, capsys) -> None:
    """Finding 46: the journal refuses an unknown ``--from-phase`` with a
    KeyError before any attempt starts; it exited 1 (a failed run) where an
    invalid argument exits 2, as an unknown run does."""
    used = broker({"gatherer": {"facts": ["a", "b"]}, "checker": {"ok": True}})
    run_id = _run_once(capsys, env)
    calls = len(used.calls)
    code, _out, err = _r4(capsys, "resume", run_id, "--from-phase", "bogus")
    assert code == 2
    assert "no phase 'bogus'" in err and "KeyError" not in err
    assert len(used.calls) == calls


def test_result_prints_the_result_or_a_slice(env, broker, capsys) -> None:
    broker({"gatherer": {"facts": ["a", "b"]}, "checker": {"ok": True}})
    run_id = _run_once(capsys, env)
    code, out, _err = _r4(capsys, "result", run_id, "--path", "output.facts")
    assert code == 0 and json.loads(out) == ["a", "b"]
    _code, out, _err = _r4(capsys, "result", run_id)
    assert json.loads(out)["status"] == "success"
    code, _out, err = _r4(capsys, "result", "no-such-run")
    assert code == 1 and "no readable result" in err
    code, _out, err = _r4(capsys, "result", "../escape")
    assert code == 2 and "invalid team run id" in err


_HOSTILE = "done\x1b]52;c;Y3VybCBldmlsfHNo\x07\x1b[2K\rall checks passed\x9b31m"


def test_agent_output_never_reaches_the_terminal_raw(env, broker, capsys) -> None:
    """Finding 14 (ADR-092 D-6): a log line or a label may splice agent
    output in (``{{steps.<id>.output…}}``). An agent that read hostile
    content returned OSC 52 (write the clipboard) and erase-line sequences,
    and ``r4 team plan run`` printed them verbatim; a result printed C1."""
    plan = _plan()
    gather = plan["phases"][0]["steps"][0]
    gather["schema"] = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    check = plan["phases"][1]["steps"][0]
    check["label"] = "fix {{steps.gather.output.title}}"
    check["log"] = "found: {{steps.gather.output.title}}"
    check["prompt"] = "Fix {{steps.gather.output.title}}"
    plan["output"] = {"title": "steps.gather.output.title"}
    path = env.tmp / "hostile.json"
    path.write_text(json.dumps(plan))
    broker({"gatherer": {"title": _HOSTILE}})

    _code, out, err = _r4(capsys, "run", str(path), "--arg", "topic=x")
    for stream in (out, err):
        assert not {"\x1b", "\x07", "\r", "\x9b"} & set(stream)
    assert "[log] check: found: done\\x1b]52;c;" in out  # shown, inert
    assert "[agent] fix done" in out  # the label, whatever the engine folded out of it

    run_id = out.split("run_id: ", 1)[1].splitlines()[0].strip()
    code, out, _err = _r4(capsys, "result", run_id, "--path", "output.title")
    assert code == 0 and "\x9b" not in out and "\x1b" not in out
    assert json.loads(out) == _HOSTILE  # still the exact value, as JSON


def _missing(text: str, *needles: str) -> list[str]:
    """The needles ``text`` lacks (one assert per test keeps each test simple)."""
    return [needle for needle in needles if needle not in text]
