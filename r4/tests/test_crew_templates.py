"""ADR-092 slice 4: crews become plan templates (MD-2, MD-8).

- research, code, paper-writing, kakenhi-report and peer-review are plan
  templates (package data, D-10) with their prompts referenced from one
  source; each validates with a static bound and runs end to end on scripted
  turns (``MemoryAgentBroker``) and on fake companions (``RoleTurnAgentBroker``).
- ``crew`` is an alias for its template on the chat tool, the bridge and the
  CLI; ``hpc`` stays the teamup preset.
- ``AgentRole`` is frozen to the HPC preset; ``AgentKey`` reads every
  persisted spelling, so old sessions load.
- HPC scaffolding is the HPC preset's only; no HPC reference reaches a crew.
- The dead compute budget and SOTA-target stop rules are retired; the
  ``generate_report`` crash is fixed.

LLM-free: no model is ever called.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_contracts.team_engine.plan import plan_summary
from r4_contracts.team_engine.spec import BudgetSpec
from r4_teamengine_spi.testing import MemoryAgentBroker, make_engine_context, make_services

import r4.teamup.session as session_module
from r4.companion import team_plan_tools
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.research_loop.team import RESEARCH_ROLES
from r4.teams import crews
from r4.teams import service as team_service
from r4.teams.plan import PlanEngine, spec_from_plan
from r4.teams.plan.templates import load_template as raw_template
from r4.teams.plan_sources import load_template
from r4.teamup.roles import ROLE_SPECS, AgentKey, AgentRole, get_role_spec
from r4.teamup.session import SessionStatus, TeamupSession, TeamupSessionConfig

SRC = Path(__file__).resolve().parents[1] / "src" / "r4"
TEMPLATE_CREWS = ("research", "code", "paper-writing", "kakenhi-report", "peer-review")
GOALS = {
    "research": {"objective": "does X hold?"},
    "code": {"goal": "add a retry to the fetcher"},
    "paper-writing": {"subject": "spin qubit coherence"},
    "kakenhi-report": {"subject": "grant 23K00001"},
    "peer-review": {"submission": "paper.pdf"},
}
BOUNDS = {"research": 16, "code": 7, "paper-writing": 14, "kakenhi-report": 6, "peer-review": 5}
# What the maintainer called "too much referencing HPC code mode": the HPC
# preset's vocabulary, scaffolding and tools. None of it may reach a crew.
HPC_MARKERS = re.compile(
    r"\bHPC\b|SOTA|sota_|ChangeLog|CLAUDE\.md|hpc_submit|GFLOPS|VibeCodeHPC|Code Doctor|"
    r"Project Manager|System Engineer|\bOpenMP\b|\bMPI\b|hardware target|optimization team",
)


def _compile(crew: str, args: dict[str, Any] | None = None):
    template = crews.crew_template(crew)
    spec, problems = spec_from_plan(
        load_template(template),
        {**GOALS[crew], **(args or {})},
        project="p",
        templates=load_template,
    )
    assert not problems, problems
    return spec


async def _run(crew: str, broker: Any, tmp_path: Path, args: dict[str, Any] | None = None):
    spec = _compile(crew, args)
    services = make_services(agents=broker)
    services.run = replace(services.run, working_dir=str(tmp_path))
    run = await PlanEngine().start(PlanEngine().compile(spec, make_engine_context()), services)
    _events = [event async for event in run.events()]
    return await run.result(), json.loads((tmp_path / "result.json").read_text())


# ── the crew registry ───────────────────────────────────────────────────────


def test_every_crew_is_hpc_or_a_builtin_template() -> None:
    assert crews.known_crews() == sorted({"hpc", *TEMPLATE_CREWS})
    assert crews.crew_template("hpc") is None
    from r4.teams.plan.templates import list_templates

    names = {row["name"] for row in list_templates()}
    for crew in TEMPLATE_CREWS:
        assert crews.crew_template(crew) in names
    assert crews.crew_template("research") == "research-round"


@pytest.mark.parametrize(
    ("alias", "crew"),
    [
        ("paper", "paper-writing"),
        ("Manuscript", "paper-writing"),
        ("kaken", "kakenhi-report"),
        ("review", "peer-review"),
        ("referee", "peer-review"),
        ("coding", "code"),
        ("claude-code", "code"),
        ("science", "research"),
        (" HPC ", "hpc"),
    ],
)
def test_aliases_select_their_crew(alias: str, crew: str) -> None:
    assert crews.crew_name(alias) == crew


def test_an_unknown_crew_is_refused_naming_the_crews() -> None:
    with pytest.raises(KeyError, match="banana.*hpc.*research"):
        crews.crew_name("banana")


def test_list_crews_reads_each_template() -> None:
    rows = {row["name"]: row for row in crews.list_crews()}
    assert rows["hpc"]["template"] is None
    paper = rows["paper-writing"]
    assert paper["template"] == "paper-writing" and paper["aliases"] == ["manuscript", "paper"]
    assert paper["phases"][0] == "Plan the sections" and "subject" in paper["params"]


# ── the templates ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("crew", TEMPLATE_CREWS)
def test_each_crew_template_validates_with_a_static_bound(crew: str) -> None:
    spec = _compile(crew)
    summary = plan_summary(spec.plan)
    assert summary["agent_bound"] == BOUNDS[crew]
    assert summary["agent_bound"] <= spec.plan["budget"]["max_agents"]
    assert spec.budgets.max_tokens == spec.plan["budget"]["max_tokens"]


@pytest.mark.parametrize("crew", [c for c in TEMPLATE_CREWS if c != "research"])
def test_crew_prompts_are_references_to_one_source_not_copies(crew: str) -> None:
    template = crews.crew_template(crew)
    raw = raw_template(template)
    resolved = load_template(template)
    for name, profile in raw["agents"].items():
        assert profile["instructions"] == {"ref": f"crew_role:{crew}/{name}"}
        expected = crews.CREW_ROLE_PROMPTS[crew][name]
        assert resolved["agents"][name]["instructions"] == expected


def test_the_code_crew_keeps_its_role_prompts() -> None:
    resolved = load_template("code")["agents"]
    assert resolved["architect"]["instructions"].startswith("You are the architect")
    assert resolved["implementer"]["instructions"].startswith("You are the implementer")
    assert resolved["tester"]["instructions"].startswith("You are the tester")
    assert resolved["reviewer"]["instructions"].startswith("You are the reviewer")


def test_an_unknown_crew_role_reference_fails_validation_by_name() -> None:
    plan = raw_template("peer-review")
    plan["agents"]["chair"]["instructions"] = {"ref": "crew_role:peer-review/nobody"}
    from r4.teams.plan_sources import resolve_refs

    with pytest.raises(ValueError, match="no crew role peer-review/nobody"):
        resolve_refs(plan)


@pytest.mark.parametrize("crew", TEMPLATE_CREWS)
def test_no_hpc_scaffolding_reaches_a_crew(crew: str) -> None:
    """The maintainer's complaint: crews ran the HPC preset's prompts and
    scaffolding. A crew's plan, resolved prompts and tools included, names
    none of it, and no profile is one of the preset's four slots."""
    spec = _compile(crew)
    text = json.dumps(spec.plan, ensure_ascii=False)
    assert not HPC_MARKERS.findall(text), HPC_MARKERS.findall(text)
    assert not set(spec.plan["agents"]) & {role.value for role in AgentRole}
    tools = {t for p in spec.plan["agents"].values() for t in p.get("tools", ())}
    assert not tools & {"hpc_submit", "teamup_start", "teamup_run", "workflow_upload"}


def test_the_hpc_markers_do_catch_the_hpc_preset() -> None:
    """The scan above is not vacuous: every HPC role prompt trips it."""
    for spec in ROLE_SPECS.values():
        assert HPC_MARKERS.search(spec.system_prompt), spec.name


async def test_research_crew_runs_end_to_end(tmp_path: Path) -> None:
    hyps = [{"statement": f"H{i}", "rationale": "r", "test": "t"} for i in range(2)]
    design = {"hypothesis": "H", "protocol": "p", "controls": "c", "resources": "r"}
    broker = MemoryAgentBroker(
        {
            "planner": {"hypotheses": hyps},
            "experimenter": {**design, "refuting_outcome": "o"},
            "analyst": {"test": "t", "power": "p", "threats": "none"},
            "reviewer": lambda req: {"score": 9 - 5 * req.metadata["item_index"], "reason": "r"},
        }
    )
    result, doc = await _run("research", broker, tmp_path, {"judges": 2})
    assert result.status == "success"
    assert doc["output"]["decision"]["status"] == "picked"
    assert broker.calls[0].instructions == RESEARCH_ROLES["RL"]["system_prompt"]


async def test_code_crew_repairs_until_a_review_finds_nothing_new(tmp_path: Path) -> None:
    defect = {"defect": "off by one", "location": "f.py:3", "smallest_fix": "use <="}
    reviews = iter(
        [
            {"verdict": "defects", "defects": [defect]},
            {"verdict": "defects", "defects": [defect]},  # nothing new: dry
        ]
    )
    broker = MemoryAgentBroker(
        {
            "architect": {"increments": [{"name": "i1", "files": ["f.py"], "acceptance": "t"}]},
            "implementer": {"changes": [{"increment": "i1", "files": ["f.py"], "summary": "s"}]},
            "tester": {"command": "pytest", "passed": False, "failures": []},
            "reviewer": lambda req: next(reviews),
        }
    )
    result, doc = await _run("code", broker, tmp_path, {"rounds": 3})
    assert result.status == "success"
    out = doc["output"]
    assert (out["rounds"], out["converged"], out["defects"]) == (2, True, [defect])
    # Turn order: architect, then implementer → tester → reviewer per round.
    labels = [call.label.split("@")[0] for call in broker.calls]
    assert labels == ["architect", *(["implementer", "tester", "reviewer"] * 2)]
    implementers = [call.prompt for call in broker.calls if call.label.startswith("implementer")]
    assert "off by one" in implementers[1]  # the repair round sees the defect
    assert broker.calls[0].instructions == crews.CREW_ROLE_PROMPTS["code"]["architect"]


async def test_paper_writing_crew_runs_end_to_end(tmp_path: Path) -> None:
    section = {"title": "Intro", "purpose": "p", "key_points": ["k"]}
    broker = MemoryAgentBroker(
        {
            "editor": {"sections": [section, {**section, "title": "Methods"}]},
            "drafter": {"title": "x", "text": "body", "claims": [{"claim": "c", "evidence": "e"}]},
            "verifier": {"title": "t", "verdict": "supported", "problems": []},
            "copyeditor": {
                "sections": [{"title": "Intro", "text": "final"}],
                "claim_evidence": ["c: e"],
                "references_to_verify": [],
                "notes": [],
            },
        }
    )
    result, doc = await _run("paper-writing", broker, tmp_path)
    assert result.status == "success"
    assert len(doc["output"]["drafts"]) == 2 and len(doc["output"]["checks"]) == 2
    assert doc["output"]["camera_ready"]["sections"][0]["text"] == "final"
    order = [call.label.split("[")[0] for call in broker.calls]
    assert order == ["editor", "drafter", "drafter", "verifier", "verifier", "copyeditor"]


async def test_kakenhi_report_crew_runs_end_to_end(tmp_path: Path) -> None:
    broker = MemoryAgentBroker(
        {
            "lead-fit": {
                "progress_to_date": "p",
                "deliverables_map": "d",
                "future_plan": "f",
                "unresolved": [],
            },
            "lead": {"aims": "a", "outcomes": [{"outcome": "o", "evidence": "e"}], "remaining": []},
            "writer": lambda req: {"section": _fanout_value(req), "text": "t", "references": []},
            "checker": {"issues": []},
        }
    )
    result, doc = await _run("kakenhi-report", broker, tmp_path)
    assert result.status == "success"
    assert doc["output"]["report"]["future_plan"] == "f"
    writers = [call for call in broker.calls if call.label.startswith("writer")]
    sections = sorted(_fanout_value(call) for call in writers)
    assert sections == ["deliverables-map", "future-plan", "progress-to-date"]


def _fanout_value(req: Any) -> str:
    return req.prompt.split("Draft the section '")[1].split("'")[0]


async def test_peer_review_crew_runs_end_to_end(tmp_path: Path) -> None:
    review = {
        "summary": "s",
        "strengths": ["a"],
        "weaknesses": ["b"],
        "reproducibility": {"data": "d", "code": "c", "environment": "e"},
        "recommendation": "minor_revision",
        "justification": "j",
        "response_to_authors": "r",
    }
    broker = MemoryAgentBroker(
        {
            "chair-review": review,
            "chair": {"summary": "s", "claims": ["c"]},
            "auditor": lambda req: {
                "aspect": req.prompt.split("'")[1],
                "verdict": "sound",
                "strengths": [],
                "weaknesses": [],
            },
        }
    )
    result, doc = await _run("peer-review", broker, tmp_path)
    assert result.status == "success"
    assert doc["output"]["review"]["recommendation"] == "minor_revision"
    aspects = sorted(a["aspect"] for a in doc["output"]["audits"])
    assert aspects == ["methods", "reproducibility", "results-validity"]


async def test_a_crew_runs_on_fake_companions_under_its_own_prompts(tmp_path: Path) -> None:
    """Through the governed broker (RoleTurnAgentBroker) and fake companions:
    each agent's system prompt is its crew profile's, never an HPC role's."""
    from r4.teams.agent_broker import RoleTurnAgentBroker
    from r4.teams.brokers import TokenBudgetMeter

    review = {
        "summary": "s",
        "strengths": [],
        "weaknesses": [],
        "reproducibility": {"data": "d", "code": "c", "environment": "e"},
        "recommendation": "accept",
        "justification": "j",
        "response_to_authors": "r",
    }
    systems: list[str] = []

    class _Companion:
        def __init__(self, _client: Any, **_kwargs: Any) -> None:
            return None

        def on_event(self, _callback: Any) -> None:
            return None

        def set_turn_cancel(self, _token: Any) -> None:
            return None

        def send(self, prompt: str, *, extra_system: str, tools: list, stream: bool) -> str:
            systems.append(extra_system)
            if "Audit the '" in prompt:
                aspect = prompt.split("'")[1]
                answer = {"aspect": aspect, "verdict": "sound", "strengths": [], "weaknesses": []}
            elif "Write the review" in prompt:
                answer = review
            else:
                answer = {"summary": "s", "claims": ["c"]}
            return json.dumps(answer)

    client = SimpleNamespace(list_models=lambda: [], preflight_model_route=lambda _m: None)
    meter = TokenBudgetMeter(BudgetSpec(max_tokens=500_000))
    broker = RoleTurnAgentBroker(client, meter=meter, run_id="r1", factory=_Companion)
    result, doc = await _run("peer-review", broker, tmp_path)
    assert result.status == "success", doc.get("steps")
    assert doc["output"]["review"]["recommendation"] == "accept"
    prompts = crews.CREW_ROLE_PROMPTS["peer-review"]
    # The broker appends the schema's final_output note to the profile's prompt.
    assert sum(system.startswith(prompts["chair"]) for system in systems) == 2
    assert sum(system.startswith(prompts["auditor"]) for system in systems) == 3
    assert not any(HPC_MARKERS.search(system) for system in systems)


# ── crew is an alias for its template: the chat tool ───────────────────────


def _human(
    answer: bool, mode: PermissionMode = PermissionMode.ASK_ALWAYS
) -> tuple[PermissionManager, list[tuple[str, str]]]:
    asked: list[tuple[str, str]] = []

    def _callback(tool: str, description: str, _arguments: dict) -> bool:
        asked.append((tool, description))
        return answer

    return PermissionManager(mode=mode, approval_callback=_callback), asked


@pytest.fixture
def chat(monkeypatch, tmp_path):
    """The session's teamup_start over a scripted plan broker; returns
    (execute, broker, asked) for a human who answers ``answer``."""
    import r4.project

    monkeypatch.setattr(
        r4.project, "get_project_manager", lambda: SimpleNamespace(get_current=lambda: "p")
    )
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path / "teams")
    monkeypatch.setattr(team_service, "TEAM_RUNS_DIR", tmp_path / "runs")

    def _chat(
        answer: bool | None = True,
        script: dict[str, Any] | None = None,
        mode: PermissionMode = PermissionMode.ASK_ALWAYS,
    ):
        broker = MemoryAgentBroker(script or {})
        monkeypatch.setattr(
            team_service.TeamService, "_agent_broker", lambda self, spec, run_id, meter: broker
        )
        manager, asked = _human(bool(answer), mode) if answer is not None else (None, [])
        _chat.manager = manager
        from r4.companion.tools import _project_and_teamup_tools

        client = SimpleNamespace(config=SimpleNamespace(project_id=""))
        tools = _project_and_teamup_tools(client, permission_manager=manager)
        start = next(t for t in tools if t["name"] == "teamup_start")["execute"]
        return start, broker, asked

    return _chat


_PEER = {
    "chair-review": {
        "summary": "s",
        "strengths": [],
        "weaknesses": [],
        "reproducibility": {"data": "d", "code": "c", "environment": "e"},
        "recommendation": "reject",
        "justification": "j",
        "response_to_authors": "r",
    },
    "chair": {"summary": "s", "claims": ["c"]},
    "auditor": lambda req: {
        "aspect": req.prompt.split("'")[1],
        "verdict": "unsound",
        "strengths": [],
        "weaknesses": ["w"],
    },
}


def _settle(run_id: str) -> None:
    with team_plan_tools._LOCK:
        live = team_plan_tools._RUNS.get(run_id)
    assert live is not None and live.done.wait(20)
    assert team_plan_tools._snapshot(run_id).status == "success"


def test_a_crew_runs_its_template_after_the_human_approves_the_plan_card(chat) -> None:
    start, broker, asked = chat(True, _PEER)
    reply = json.loads(start(goal="the attached manuscript", crew="referee"))
    head = {k: reply.get(k) for k in ("ok", "started", "crew", "template")}
    assert head == {"ok": True, "started": True, "crew": "peer-review", "template": "peer-review"}
    _settle(reply["run_id"])
    assert [tool for tool, _ in asked] == ["team_plan_run"]  # the plan's own card (MD-5)
    assert "Run team plan: peer-review, at most 5 agents" in asked[0][1]
    assert broker.calls[0].prompt.count("the attached manuscript") == 1
    assert "session_id" not in reply  # no teamup session was opened
    assert not list(session_module.TEAMUP_SESSIONS_DIR.glob("*/session.json"))


def test_a_rejected_card_runs_nothing(chat) -> None:
    start, broker, asked = chat(False, _PEER)
    reply = json.loads(start(goal="g", crew="peer-review"))
    assert reply["ok"] is False and reply["status"] == "rejected"
    assert len(asked) == 1 and broker.calls == []


def test_with_no_human_to_ask_a_crew_starts_nothing(chat) -> None:
    start, broker, _asked = chat(None, _PEER)
    reply = json.loads(start(goal="g", crew="peer-review"))
    assert reply["ok"] is False and reply["status"] == "rejected" and broker.calls == []


def test_run_false_validates_and_describes_without_asking(chat) -> None:
    start, broker, asked = chat(True)
    reply = json.loads(start(goal="g", crew="paper", run=False))
    assert reply["ok"] is True and reply["started"] is False
    assert reply["agent_bound"] == BOUNDS["paper-writing"]
    assert reply["phases"][0] == "Plan the sections"
    assert asked == [] and broker.calls == []


def test_models_bind_to_the_templates_model_params(chat) -> None:
    start, _broker, _asked = chat(True)
    ok = json.loads(start(goal="g", crew="code", models={"reviewer": "rev-m"}, run=False))
    assert ok["ok"] is True and ok["warnings"] == []  # an independent reviewer now
    bad = json.loads(start(goal="g", crew="code", models={"planner": "x"}, run=False))
    assert bad["ok"] is False and "no model for 'planner'" in bad["problems"][0]
    assert "reviewer" in bad["problems"][0]


def test_the_default_crew_is_research_and_runs_research_round(chat) -> None:
    start, _broker, _asked = chat(True)
    reply = json.loads(start(goal="g", run=False))
    assert (reply["crew"], reply["template"]) == ("research", "research-round")


def test_the_hpc_crew_still_opens_a_teamup_session(chat) -> None:
    start, broker, asked = chat(True)
    reply = json.loads(start(goal="reduce walltime", crew="hpc", run=False))
    assert reply["ok"] is True and reply["crew"] == "hpc" and reply["session_id"]
    assert reply["roles"] == ["pm", "se", "pg"] and broker.calls == []
    loaded = TeamupSession.load(reply["session_id"], SimpleNamespace())
    assert loaded.config.crew_pack == "hpc" and loaded.config.hpc_preset


def test_an_unknown_crew_is_refused_by_name(chat) -> None:
    start, _broker, _asked = chat(True)
    reply = json.loads(start(goal="g", crew="banana"))
    assert reply["ok"] is False and "banana" in reply["error"] and "peer-review" in reply["error"]


# ── one approval for a template crew: the plan's own card ──────────────────


def _as_the_agent(manager: PermissionManager, start: Any, **arguments: Any) -> dict | None:
    """What the companion does with a tool call: the gate, then the executor."""
    if not manager.check("teamup_start", arguments):
        return None
    return json.loads(start(**arguments))


@pytest.mark.parametrize("mode", [PermissionMode.ASK_ALWAYS, PermissionMode.AUTO_SAFE])
def test_a_template_crew_asks_exactly_one_card_the_plans(chat, mode) -> None:
    """Ask and chat modes (MD-5): no teamup_start card, one never-auto
    team_plan_run card with its MD-6 grant list, then the run."""
    start, broker, asked = chat(True, _PEER, mode)
    reply = _as_the_agent(chat.manager, start, goal="the manuscript", crew="peer-review")
    assert [tool for tool, _ in asked] == ["team_plan_run"]
    card = asked[0][1]
    assert card.startswith("[fence: a plan run fans out agents")  # never-auto (MD-5)
    assert "approving grants its agents, for this run only: file_read" in card  # MD-6
    assert reply["started"] is True
    _settle(reply["run_id"])
    assert broker.calls  # the validated template plan, and nothing else, ran


def test_autopilot_runs_a_template_crew_without_a_card(chat) -> None:
    """ADR-104 amendment 2 (maintainer, 2026-11-05: true autopilot): the
    plan card is not asked in autopilot; the run starts, with no MD-6 grant
    (nobody approved a card)."""
    start, broker, asked = chat(False, _PEER, PermissionMode.TRUST)
    reply = _as_the_agent(chat.manager, start, goal="the manuscript", crew="peer-review")
    assert asked == []
    assert reply["started"] is True
    _settle(reply["run_id"])
    assert broker.calls


@pytest.mark.parametrize("mode", [PermissionMode.ASK_ALWAYS, PermissionMode.AUTO_SAFE])
def test_no_plan_card_yes_means_no_run(chat, mode) -> None:
    start, broker, asked = chat(False, _PEER, mode)
    reply = _as_the_agent(chat.manager, start, goal="g", crew="paper")
    assert [tool for tool, _ in asked] == ["team_plan_run"]
    assert (reply["ok"], reply["status"]) == (False, "rejected")
    assert broker.calls == [] and team_plan_tools.live_runs() == []


def test_a_delegated_crew_without_a_plan_executor_runs_nothing() -> None:
    import r4.companion.tools as tools_mod

    manager, asked = _human(True)
    start = next(
        t for t in tools_mod._teamup_tools(SimpleNamespace()) if t["name"] == "teamup_start"
    )
    reply = _as_the_agent(manager, start["execute"], goal="g", crew="code")
    assert reply["ok"] is False and asked == []


def test_the_hpc_crew_keeps_teamup_starts_own_rule(chat) -> None:
    start, _broker, asked = chat(False)  # the human says no to the hpc card
    assert _as_the_agent(chat.manager, start, goal="tune", crew="hpc", run=False) is None
    assert [tool for tool, _ in asked] == ["teamup_start"]
    trust, trust_asked = _human(False, PermissionMode.TRUST)
    assert trust.check("teamup_start", {"goal": "tune", "crew": "hpc"}) is True  # fires, as before
    assert trust_asked == []
    unknown, unknown_asked = _human(False)
    assert unknown.check("teamup_start", {"goal": "g", "crew": "banana"}) is False
    assert [tool for tool, _ in unknown_asked] == ["teamup_start"]


def test_deny_mode_and_plan_agents_never_reach_the_delegation() -> None:
    manager, asked = _human(True)
    manager.set_mode(PermissionMode.DENY)
    assert manager.check("teamup_start", {"goal": "g", "crew": "paper"}) is False
    host, _ = _human(True, PermissionMode.TRUST)
    fork = host.fork_for_agent("a", allow=["teamup_start"], run_id="r1")
    assert fork.refusal("teamup_start") == "team_agent_deny"
    assert fork.check("teamup_start", {"goal": "g", "crew": "paper"}) is False
    assert asked == []


@pytest.mark.parametrize(
    "crew",
    [None, "", "research", "PAPER", " Manuscript ", "hpc", " HPC ", "banana", 3, "plan:code"],
)
def test_the_gate_and_the_executor_read_the_crew_the_same_way(crew, tmp_path, monkeypatch) -> None:
    """The gate waives teamup_start's card exactly for the calls the executor
    runs as a template plan (behind the plan's card); every other call keeps
    the tool's own rule."""
    import r4.companion.tools as tools_mod
    from r4.companion.permissions import plan_card_decides

    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    planned: list[str] = []
    executor = SimpleNamespace(start_crew=lambda c, *a, **kw: planned.append(c) or {"ok": True})
    tools = tools_mod._teamup_tools(SimpleNamespace(), plan_executor=executor)
    start = next(t for t in tools if t["name"] == "teamup_start")["execute"]
    arguments = {"goal": "g", "run": False, **({"crew": crew} if crew is not None else {})}
    start(**arguments)
    assert plan_card_decides("teamup_start", arguments) is bool(planned)


def test_bootstrap_refuses_a_template_crew_by_name() -> None:
    from r4.teamup.bootstrap import build_team_config

    with pytest.raises(ValueError, match="plan template 'code'"):
        build_team_config("g", crew="dev")
    config = build_team_config("g")
    assert config.crew_pack == "hpc" and config.hpc_preset


# ── crew is an alias: the bridge and the CLI ───────────────────────────────


def test_the_bridge_canonicalises_crews_and_lists_their_templates() -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    goal, crew = CompanionBridgeServer._teamup_goal_and_crew({"goal": "g", "crew": "Manuscript"})
    assert (goal, crew) == ("g", "paper-writing")
    with pytest.raises(ValueError, match="banana"):
        CompanionBridgeServer._teamup_goal_and_crew({"goal": "g", "crew": "banana"})
    rows = CompanionBridgeServer.teamup_crews(SimpleNamespace(), {})["crews"]
    assert {row["name"]: row["template"] for row in rows}["research"] == "research-round"


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        (
            {"ok": True, "crew": "code", "template": "code", "run_id": "r1", "started": True},
            "crew_plan_started",
        ),
        ({"ok": False, "status": "rejected", "error": "not approved"}, "team_start_error"),
    ],
)
def test_the_bridge_start_runs_a_template_crew_through_the_plan_executor(reply, kind) -> None:
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    calls: list[tuple] = []
    lines: list[dict] = []
    executor = SimpleNamespace(start_crew=lambda *a, **kw: calls.append((a, kw)) or reply)
    host = SimpleNamespace(
        _plan_executor=lambda: executor,
        _emit_line=lines.append,
        _teamup_bootstrapping=True,
        session=SimpleNamespace(client=None),
    )
    host._crew_plan_thread = lambda spec, run: CompanionBridgeServer._crew_plan_thread(
        host, spec, run
    )
    spec = {"goal": "g", "crew": "code", "models": {"reviewer": "m"}}
    CompanionBridgeServer._teamup_bootstrap_thread(host, spec=spec, run=True, model=None)
    assert calls == [(("code", "g"), {"models": {"reviewer": "m"}, "run": True})]
    assert lines[-1]["team_event"]["kind"] == kind
    assert host._teamup_bootstrapping is False


def test_cli_create_with_a_plan_crew_shows_then_runs_its_template(monkeypatch, tmp_path, capsys):
    from argparse import Namespace

    from r4._cli import teamup as cli

    broker = MemoryAgentBroker(_PEER)
    monkeypatch.setattr(
        team_service.TeamService, "_agent_broker", lambda self, spec, run_id, meter: broker
    )
    monkeypatch.setattr(team_service, "TEAM_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path / "teams")
    monkeypatch.setattr(cli, "_launch_project", lambda args, client: "p")
    args = Namespace(name="ms-42", crew="review", goal=None, start=False)
    assert cli._cmd_teamup_create(args, SimpleNamespace()) == 0
    shown = capsys.readouterr().out
    assert "runs the plan template 'peer-review'" in shown and "dry-run" in shown
    assert broker.calls == []

    args.start = True
    assert cli._cmd_teamup_create(args, SimpleNamespace()) == 0
    assert "status:  success" in capsys.readouterr().out
    assert "ms-42" in broker.calls[0].prompt  # the name is the subject without --goal
    assert not (tmp_path / "teams").exists()  # no teamup session


# ── AgentRole is frozen; AgentKey reads old keys ───────────────────────────


def test_agent_role_is_frozen_to_the_hpc_preset() -> None:
    assert [role.value for role in AgentRole] == ["pm", "se", "pg", "cd"]
    assert set(ROLE_SPECS) == set(AgentRole)


def _imports_from(path: Path, part: str) -> list[ast.ImportFrom]:
    """The ``from <module> import …`` statements of ``path`` whose module
    names ``part``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    return [node for node in nodes if part in (node.module or "")]


def _agent_role_importers() -> set[str]:
    importers = set()
    for path in SRC.rglob("*.py"):
        names = {alias.name for node in _imports_from(path, "teamup") for alias in node.names}
        if "AgentRole" in names:
            importers.add(path.relative_to(SRC).as_posix())
    return importers


def test_only_the_hpc_preset_modules_import_agent_role() -> None:
    """No crew re-skins the four slots: outside the HPC preset's own modules
    (the teamup package, the native engine that runs it, and the surfaces
    that open it), nothing imports AgentRole."""
    allowed = {
        "teams/native.py",
        "_cli/teamup.py",
        "_cli/chat.py",
        "companion/ui/bridge_server.py",
    }
    outside = {rel for rel in _agent_role_importers() if not rel.startswith("teamup/")}
    assert outside <= allowed, outside - allowed


@pytest.mark.parametrize(
    "rel", ["teams/crews.py", "teams/plan_sources.py", "research_loop/team.py"]
)
def test_no_crew_source_imports_the_hpc_preset(rel: str) -> None:
    assert _imports_from(SRC / rel, "teamup") == []


@pytest.mark.parametrize("raw", ["pm", "PM", " Pm ", "AgentRole.PM", AgentRole.PM])
def test_agent_key_reads_every_persisted_spelling(raw: Any) -> None:
    key = AgentKey(raw)
    assert key == "pm" and key.value == "pm" and key.role is AgentRole.PM
    assert {"pm": 1}[key] == 1  # hashes as the stored key
    assert get_role_spec(raw).role is AgentRole.PM


def test_agent_key_outside_the_preset_has_no_role() -> None:
    assert AgentKey("planner").role is None
    with pytest.raises(ValueError, match="HPC preset"):
        get_role_spec("planner")


def _old_snapshot(tmp_path: Path, **config: Any) -> str:
    """A session.json written before slice 4: odd role spellings, a research
    crew's prompts, the retired budget/target knobs and an unknown key."""
    sid = "old00001"
    session_dir = tmp_path / sid
    (session_dir / "agent-shared").mkdir(parents=True)
    base = {
        "name": "old research team",
        "roles": ["PM", "AgentRole.PG", " se ", "planner"],
        "compute_budget": 0,
        "target_performance": 1.0,
        "crew_pack": "research",
        "role_models": {"CD": "rev-model", "PM": "m"},
        "role_prompts": {"PM": RESEARCH_ROLES["RL"]["system_prompt"]},
        "optimization_goal": "does X hold?",
        "max_iterations": 3,
    }
    base.update(config)
    data = {
        "session_id": sid,
        "config": base,
        "status": "running",
        "agent_states": {
            "PM": {"role": "AgentRole.PM", "status": "idle", "messages_sent": 4},
            "planner": {"role": "planner", "status": "idle"},
        },
        "iteration": 1,
        "started_at": "2026-09-30T10:00:00",
        "updated_at": "2026-09-30T11:00:00",
        "current_sota": None,
        "budget_used": 0,
    }
    (session_dir / "session.json").write_text(json.dumps(data))
    return sid


def test_an_old_crew_session_still_loads_through_agent_key(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    sid = _old_snapshot(tmp_path)
    session = TeamupSession.load(sid, SimpleNamespace())
    assert session.config.roles == [AgentRole.PM, AgentRole.PG, AgentRole.SE]
    assert session.config.role_models == {"cd": "rev-model", "pm": "m"}
    assert session.config.role_prompts["pm"] == RESEARCH_ROLES["RL"]["system_prompt"]
    assert session.agent_states["pm"].messages_sent == 4
    assert "planner" in caplog.text  # the unknown key is named, not fatal
    rows = TeamupSession.list_sessions()
    assert [row["session_id"] for row in rows] == [sid]


def _loaded_old_session(tmp_path: Path, monkeypatch) -> TeamupSession:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    return TeamupSession.load(_old_snapshot(tmp_path), SimpleNamespace())


def test_an_old_crew_session_has_no_hpc_scaffolding(tmp_path, monkeypatch) -> None:
    """A pre-slice-4 crew session is not the HPC preset: no SOTA tracker, no
    hardware profile, nothing of the preset in its roles' context."""
    session = _loaded_old_session(tmp_path, monkeypatch)
    assert session.config.hpc_preset is False
    assert (session.sota, session.hardware) == (None, None)
    context = session._build_agent_context_sync(AgentRole.PM)
    assert not HPC_MARKERS.search(context), context
    report = asyncio.run(session.generate_report())
    assert "## Performance" not in report
    assert "Goal: does X hold?" in report


def test_an_old_crew_session_keeps_running_past_its_retired_budget(tmp_path, monkeypatch) -> None:
    """Its budget of 0 used to stop it; the iteration cap is the one rule now,
    and the retired knobs are not written back."""
    session = _loaded_old_session(tmp_path, monkeypatch)
    contexts: list[str] = []
    monkeypatch.setattr(
        session_module,
        "run_role_turn",
        lambda _s, _r, context, **_kw: contexts.append(context) or ("ok", []),
    )
    assert asyncio.run(session.run_iteration()) is True
    assert (session.iteration, len(contexts)) == (2, 3)
    assert "SOTA" not in "".join(contexts)
    saved = json.loads((tmp_path / session.session_id / "session.json").read_text())
    assert "budget_used" not in saved
    assert not {"compute_budget", "target_performance"} & set(saved["config"])


# ── the HPC preset is unchanged for HPC ────────────────────────────────────


def _hpc_session(tmp_path: Path, monkeypatch, **config: Any) -> TeamupSession:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    cfg = TeamupSessionConfig(name="tune", optimization_goal="reduce walltime", **config)
    return TeamupSession.create(cfg, SimpleNamespace())


def test_the_hpc_preset_keeps_its_scaffolding(tmp_path, monkeypatch) -> None:
    session = _hpc_session(tmp_path, monkeypatch)
    assert session.config.hpc_preset
    assert session.config.roles == [AgentRole.PM, AgentRole.SE, AgentRole.PG]
    assert session.sota is not None
    assert session.hardware is not None
    claude = (session.session_dir / "agent-shared" / "CLAUDE.md").read_text()
    assert "SOTA Files" in claude
    assert "Hardware Target" in claude
    assert "## Budget" not in claude  # the retired compute budget


def _hpc_turns(tmp_path: Path, monkeypatch) -> tuple[TeamupSession, list[tuple[str, str, str]]]:
    session = _hpc_session(tmp_path, monkeypatch)
    turns: list[tuple[str, str, str]] = []

    def _turn(_sess, role, context, *, system_prompt, model):
        turns.append((role.value, system_prompt, context))
        return f"{role.value} done", []

    monkeypatch.setattr(session_module, "run_role_turn", _turn)
    assert asyncio.run(session.run_iteration()) is True
    return session, turns


def _hpc_context(session: TeamupSession, role: str, context: str) -> str:
    """The HPC preset's role context as it was before slice 4, less the
    retired budget lines."""
    return (
        "## Current Session State\n\n"
        "**Iteration**: 1\n"
        "**Session**: tune\n"
        "**Goal**: reduce walltime\n\n"
        "### Recent Activity\n"
        f"{_recent(context)}\n\n"
        "### SOTA Status\n"
        f"{session.sota.get_status_report()}\n\n"
        "### Your Turn\n"
        f"What is your next action as {role.upper()}?\n"
        "Consider the recent activity and current SOTA. Provide your action and reasoning.\n"
    )


def test_the_hpc_preset_keeps_its_roles_prompts_order_and_context(tmp_path, monkeypatch) -> None:
    session, turns = _hpc_turns(tmp_path, monkeypatch)
    assert [role for role, _s, _c in turns] == ["pm", "se", "pg"]  # turn order kept
    for role, system, context in turns:
        assert system == ROLE_SPECS[AgentRole(role)].system_prompt  # prompts unchanged
        assert context == _hpc_context(session, role, context)


def _recent(context: str) -> str:
    return context.split("### Recent Activity\n", 1)[1].split("\n\n### SOTA Status", 1)[0]


def test_the_iteration_cap_is_the_one_stop_rule(tmp_path, monkeypatch) -> None:
    session = _hpc_session(tmp_path, monkeypatch, max_iterations=2)
    monkeypatch.setattr(session_module, "run_role_turn", lambda *a, **kw: ("ok", []))
    assert asyncio.run(session.run_iteration()) is True
    assert asyncio.run(session.run_iteration()) is False
    assert session.status is SessionStatus.COMPLETED and session.iteration == 2
    status = session.get_status()
    assert "budget" not in status and status["current_sota"] is None


def test_the_retired_budget_and_tracker_are_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("r4.teamup.budget")
    import r4.teamup

    assert not hasattr(r4.teamup, "BudgetTracker")
    source = (SRC / "teamup" / "session.py").read_text(encoding="utf-8")
    assert "_on_sota_update" not in source and "target_performance =" not in source


# ── generate_report ─────────────────────────────────────────────────────────


def test_generate_report_no_longer_crashes_on_a_session_with_a_goal(tmp_path, monkeypatch) -> None:
    """Regression: ``generate_report`` called ``SOTATracker.get_best_score``,
    which never existed, so every session with an optimisation goal raised
    AttributeError (``r4 teamup report``, the bridge's ``teamup.report``)."""
    session = _hpc_session(tmp_path, monkeypatch)
    report = asyncio.run(session.generate_report())
    assert "Goal: reduce walltime" in report
    assert "**Improvement**: No measurements" in report
    assert "## Optimization History" in report


def test_generate_report_reads_the_trackers_own_improvement(tmp_path, monkeypatch) -> None:
    session = _hpc_session(tmp_path, monkeypatch)
    session.sota.record(value=100.0, agent="pg", technique="baseline")
    session.sota.record(value=150.0, agent="pg", technique="tile_64")
    report = asyncio.run(session.generate_report())
    assert "+50.0% over the first measurement" in report
    assert "150.0000 gflops by pg (tile_64)" in report
    assert "Most active: pm" in report


def test_the_cli_report_command_prints_the_report(tmp_path, monkeypatch, capsys) -> None:
    from argparse import Namespace

    from r4._cli import teamup as cli

    session = _hpc_session(tmp_path, monkeypatch)
    cli._cmd_teamup_report(Namespace(session_id=session.session_id, output=None), None)
    assert "# Teamup Session Report: tune" in capsys.readouterr().out
