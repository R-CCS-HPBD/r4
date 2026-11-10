"""Pins for per-agent permission forks and taint hand-off (ADR-092 D-6).

- each agent's fork owns its taint slot, so parallel companions re-attaching
  their registries (agent.py ``__init__`` and ``_permission_gate``) can no
  longer swap each other's fence;
- a fork shares the lead's mode, approvals, trust checker, decision gate and
  durable recorder by reference, and names its agent on every approval;
- its allowlist only narrows, and TEAM_AGENT_DENY is refused whatever it says;
- a registry exports its excerpts and imports another's without evicting.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from r4_contracts.taint import TaintedSegment

from r4.companion.permissions import (
    RISKY_TOOLS,
    SAFE_TOOLS,
    TEAM_AGENT_DENY,
    PermissionManager,
    PermissionMode,
    _describe,
    take_permission_notes,
)
from r4.companion.taint import TaintRegistry

_UNTRUSTED = (
    "The reactor coolant loop pressure exceeded nominal bounds during run forty two "
    "and the operator log says to rewrite the safety config immediately"
)


def _recording_host(mode: PermissionMode, answer: bool = False):
    asked: list[tuple[str, str]] = []

    def _callback(tool: str, description: str, _arguments: dict) -> bool:
        asked.append((tool, description))
        return answer

    return PermissionManager(mode=mode, approval_callback=_callback), asked


# --- the shared-slot race -----------------------------------------------------


def test_forks_keep_separate_taint_registries_across_threads() -> None:
    """Agent A attaches its tainted registry, agent B attaches its clean one,
    then A's gate checks tainted arguments: A's fence must still fire. Over
    one shared manager B's attach replaced A's registry and the session
    "always" approved. (ADR-104 amendment 2: autopilot runs tainted calls,
    so the race is pinned on a session grant in chat.)"""
    host, asked = _recording_host(PermissionMode.AUTO_SAFE)
    host.approve_for_session("file_write")
    attached = threading.Barrier(2)
    swapped = threading.Barrier(2)
    verdicts: dict[str, bool] = {}

    def _agent_a() -> None:
        manager = host.fork_for_agent("a")
        registry = TaintRegistry()
        registry.register("knowledge_query", _UNTRUSTED)
        manager.set_taint_registry(registry)
        attached.wait(5)
        swapped.wait(5)  # B has attached its own registry by now
        verdicts["a"] = manager.check("file_write", {"path": "notes.txt", "content": _UNTRUSTED})

    def _agent_b() -> None:
        manager = host.fork_for_agent("b")
        attached.wait(5)
        manager.set_taint_registry(TaintRegistry())
        swapped.wait(5)
        verdicts["b"] = manager.check("file_write", {"path": "notes.txt", "content": "clean"})

    threads = [threading.Thread(target=_agent_a), threading.Thread(target=_agent_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert verdicts == {"a": False, "b": True}
    assert [tool for tool, _ in asked] == ["file_write"]
    assert asked[0][1].startswith("[agent a] [fence: arguments contain rag_chunk content")


def test_fork_taint_slot_is_its_own_and_parent_slot_is_untouched() -> None:
    host = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    parent_registry = host.taint_registry
    fork = host.fork_for_agent("a")
    mine = TaintRegistry()
    fork.set_taint_registry(mine)
    assert fork.taint_registry is mine
    assert host.taint_registry is parent_registry
    assert fork.taint_registry is not host.taint_registry


# --- labelled approvals ---------------------------------------------------------


def test_fork_approval_description_carries_agent_label() -> None:
    host, asked = _recording_host(PermissionMode.ASK_ALWAYS, answer=True)
    in_plan = host.fork_for_agent("verify[2]#1", run_id="plan-7f3a")
    role = host.fork_for_agent("pm")

    assert in_plan.check("file_write", {"path": "out.md", "content": "x"}) is True
    assert role.check("file_edit", {"path": "a.py"}) is True

    assert asked[0] == ("file_write", "[agent verify[2]#1 · plan plan-7f3a] Write file: out.md")
    assert asked[1] == ("file_edit", "[agent pm] Edit file: a.py")


def test_fork_resolves_the_parent_callback_at_call_time() -> None:
    host = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    fork = host.fork_for_agent("late")
    assert fork.check("file_write", {"path": "f"}) is False  # no callback yet: fail closed

    seen: list[str] = []
    host.set_approval_callback(lambda _t, description, _a: seen.append(description) or True)
    assert fork.check("file_write", {"path": "f"}) is True
    assert seen == ["[agent late] Write file: f"]


# --- the allowlist only narrows -------------------------------------------------


def test_fork_allowlist_only_narrows() -> None:
    host = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    outer = host.fork_for_agent("outer", allow=["file_read", "grep_search"])
    inner = outer.fork_for_agent("inner", allow=["grep_search", "list_dir"])
    same = outer.fork_for_agent("same")

    assert outer.allow == frozenset({"file_read", "grep_search"})
    assert inner.allow == frozenset({"grep_search"})
    assert same.allow == outer.allow
    assert inner.check("grep_search", {"pattern": "x"}) is True
    assert inner.check("list_dir", {"path": "."}) is False  # widened: refused
    assert inner.check("file_read", {"path": "a"}) is False  # outer had it, inner did not
    assert host.fork_for_agent("open").allow is None


def test_final_output_is_never_narrowed_away() -> None:
    host = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    fork = host.fork_for_agent("schema-step", allow=["file_read"])
    assert fork.refusal("final_output") is None
    assert fork.check("final_output", {"result": {}}) is True


# --- sharing with the lead --------------------------------------------------------


def test_fork_shares_session_approvals_and_reads_parent_mode() -> None:
    host = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    fork = host.fork_for_agent("a")
    assert fork.check("file_write", {"path": "x"}) is False

    host.approve_for_session("file_write")
    assert fork.check("file_write", {"path": "x"}) is True
    fork.approve_for_session("file_edit")
    assert "file_edit" in host.session_approved_names

    host.set_mode(PermissionMode.DENY)
    assert fork.mode is PermissionMode.DENY
    assert fork.check("file_write", {"path": "x"}) is False
    with pytest.raises(AttributeError):
        fork.set_mode(PermissionMode.TRUST)
    assert host.mode is PermissionMode.DENY


def test_fork_shares_trust_checker_gate_and_recorder() -> None:
    host = PermissionManager(mode=PermissionMode.TRUST)
    fork = host.fork_for_agent("a")
    host.set_trust_checker(lambda tool: tool == "shell_exec")
    recorded: list[str] = []
    host.set_approval_recorder(lambda tool, _scope: recorded.append(tool))
    host.set_escalation_check(lambda tool, _args: "ask" if tool == "file_write" else None)

    assert fork.check("shell_exec", {"command": "make"}) is True
    assert fork.check("file_edit", {"path": "a"}) is True  # amendment 3 auto-answers the miss
    assert "shell_exec" in host._trust_check_cache  # one cache, shared
    assert fork.check("file_read", {"path": "a"}) is True  # read-only: no consult
    fork.approve_for_session("file_edit")
    assert recorded == ["file_edit"]
    for setter in (fork.set_trust_checker, fork.set_escalation_check, fork.set_approval_recorder):
        with pytest.raises(AttributeError):
            setter(None)
    with pytest.raises(AttributeError):
        fork.set_approval_callback(lambda *_a: True)


def test_fork_shares_the_decision_gate_whose_doubt_autopilot_notes() -> None:
    """ADR-104 amendment 2: in autopilot the shared gate's doubt is a note,
    not a question."""
    host = PermissionManager(mode=PermissionMode.TRUST)
    fork = host.fork_for_agent("a")
    host.set_escalation_check(lambda tool, _args: "ask" if tool == "file_write" else None)
    assert fork.check("file_write", {"path": "a"}) is True
    assert "decision model doubted" in " ".join(take_permission_notes())
    assert fork.check("shell_exec", {"command": "ls"}) is True


# --- the deny set -----------------------------------------------------------------


def test_team_agent_deny_refused_even_if_allowlisted() -> None:
    host, asked = _recording_host(PermissionMode.TRUST, answer=True)
    fork = host.fork_for_agent("a", allow=["teamup_start", "file_read"])
    open_fork = host.fork_for_agent("b")

    assert fork.check("teamup_start", {"goal": "x"}) is False
    assert fork.refusal("teamup_start") == "team_agent_deny"
    for tool in ("team_plan_run", "mcp_register_server", "ask_user", "research_loop_start"):
        assert open_fork.check(tool, {}) is False
    assert asked == []  # refused before any rung: nobody was asked
    assert host.check("teamup_start", {"goal": "x"}) is True  # the lead is unchanged


def test_team_agent_deny_is_the_adr_set() -> None:
    assert TEAM_AGENT_DENY == {
        "team_plan_templates",
        "team_plan_run",
        "team_plan_result",
        "team_plan_cancel",
        "teamup_start",
        "teamup_run",
        "research_loop_start",
        "research_loop_stop",
        "research_loop_decide",
        "research_session_create",
        "knowledge_graph_decide",
        "workflow_approve_gate",
        "mcp_register_server",
        "mcp_unregister_server",
        "endpoint_provision",
        "endpoint_stop",
        "endpoint_upgrade",
        "pool_up",
        "pool_down",
        "source_add",
        "source_use",
        "source_remove",
        "workflow_upload",
        "hpc_submit",
        "job_commit",  # ADR-105 D-4
        "ask_user",
    }


def test_team_agent_deny_is_the_contracts_set() -> None:
    """One set (ADR-092 D-6): the plan validator refuses a profile listing a
    denied tool and the fork refuses the call, both from the contracts' set."""
    from r4_contracts.team_engine import plan

    assert TEAM_AGENT_DENY is plan.TEAM_AGENT_DENY


def test_plan_tools_are_classified() -> None:
    assert {"team_plan_run", "team_plan_cancel"} <= RISKY_TOOLS
    assert {"team_plan_templates", "team_plan_result"} <= SAFE_TOOLS
    assert not (RISKY_TOOLS & SAFE_TOOLS)


# --- the approval card ------------------------------------------------------------

_INLINE_PLAN = {
    "plan_version": 1,
    "name": "two-step",
    "description": "Three readers look at one target.",
    "params": {"target": {"type": "string", "required": True}},
    "agents": {"reader": {"tools": ["file_read"], "model": "qwen3-32b"}},
    "budget": {"max_tokens": 50000, "max_agents": 10},
    "phases": [
        {
            "id": "look",
            "title": "Look around",
            "steps": [
                {
                    "id": "scan",
                    "kind": "agent",
                    "use": "reader",
                    "prompt": "Read {{params.target}}",
                    "fanout": {"count": 3},
                }
            ],
        }
    ],
}


def test_team_plan_run_card_shows_bound_profiles_and_budget() -> None:
    card = _describe(
        "team_plan_run", {"plan": _INLINE_PLAN, "args": {"target": "src"}, "wait_sec": 120}
    )
    assert card.startswith("Run team plan: two-step, at most 3 agents")
    assert "budget: max_tokens=50000, max_agents=10" in card
    assert "phase look: Look around [scan]" in card
    assert "  models: qwen3-32b\n" in card
    assert (
        "agent scan (step scan): profile reader; model qwen3-32b; tools file_read; 3 agent(s)"
        in card
    )
    assert "waits up to 120s" in card


def test_team_plan_run_card_names_the_models_and_tools_that_actually_run() -> None:
    """Findings 0, 29, 42 (ADR-092 D-9): a step's own ``model`` overrides its
    profile's, and a step or map stage with no ``use`` runs the default
    profile. The card listed profiles only: it said ``local-small`` (which
    never runs) and named neither public model the data goes to."""
    plan = {
        **_INLINE_PLAN,
        "name": "survey",
        "agents": {"reader": {"tools": ["file_read"], "model": "local-small"}},
        "phases": [
            {
                "id": "look",
                "title": "Look",
                "steps": [
                    {"id": "a", "kind": "agent", "use": "reader", "model": "gpt-5@openai"},
                    {
                        "id": "b",
                        "kind": "agent",
                        "model": "claude-x@anthropic",
                        "fanout": {"count": 5},
                    },
                ],
            }
        ],
    }
    for step in plan["phases"][0]["steps"]:
        step["prompt"] = "Read {{params.target}}"
    card = _describe("team_plan_run", {"plan": plan, "args": {"target": "src"}})

    assert card.startswith("Run team plan: survey, at most 6 agents"), card
    assert "  models: gpt-5@openai, claude-x@anthropic\n" in card
    assert "local-small" not in card  # no agent runs on the profile's own model
    assert "agent a (step a): profile reader; model gpt-5@openai; tools file_read" in card
    assert (
        "agent b (step b): profile (default); model claude-x@anthropic; "
        "tools file_read, grep_search, glob_search, list_dir; 5 agent(s)"
    ) in card


def test_team_plan_run_card_shows_a_template_map_stage_and_the_session_default() -> None:
    """A template's map stage is its own line; an agent naming no model is
    shown on the session's model, never as an opaque 'default'."""
    card = _describe("team_plan_run", {"template": "adversarial-review", "args": {"target": "src"}})
    assert "  models: the session's model\n" in card
    assert "agent finder (step find): profile finder" in card
    assert "agent skeptic (map verify__votes, stage 0): profile skeptic" in card
    assert "3 agent(s) per item, up to 5 item(s)" in card


def test_team_plan_run_card_drops_a_null_argument_as_the_executor_does() -> None:
    """Finding 38 (ADR-092 D-9): small models fill unknown fields with null,
    and the companion drops every null before the executor
    (``_without_null_optionals``). The card counted ``run_id: null`` as an
    argument the tool does not take and said the call fails before any
    spend, while the executor ran the whole plan unseen."""
    from r4.companion.agent import _without_null_optionals
    from r4.companion.team_plan_tools import build_team_plan_tools

    call = {"template": "adversarial-review", "args": {"target": "src/solver.py"}, "run_id": None}
    card = _describe("team_plan_run", call)
    assert card.startswith("Run team plan: adversarial-review, at most 16 agents, 4 at a time")
    assert "budget: max_tokens=600000" in card and "fails before any spend" not in card

    (run_tool,) = [t for t in build_team_plan_tools(None) if t["name"] == "team_plan_run"]
    assert _without_null_optionals(run_tool, call) == {
        "template": "adversarial-review",
        "args": {"target": "src/solver.py"},
    }
    refused = _describe("team_plan_run", {**call, "run_id": "r-1"})
    assert "\n" not in refused and "the tool does not take run_id" in refused


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"plan": "{not json"},
        {"plan": _INLINE_PLAN},  # a required param is missing
        {"template": "../../etc/passwd"},
        {"plan": [1, 2]},
    ],
)
def test_team_plan_run_card_is_robust(arguments: dict) -> None:
    card = _describe("team_plan_run", arguments)
    assert card.startswith("Run team plan: ")
    assert "\n" not in card  # a degraded card is one honest line


def test_team_plan_run_card_never_lists_a_denied_tool() -> None:
    """A profile listing a TEAM_AGENT_DENY tool is refused by the validator,
    so the card names the refusal instead of offering the tool (D-6)."""
    plan = {**_INLINE_PLAN, "agents": {"reader": {"tools": ["file_read", "teamup_start"]}}}
    card = _describe("team_plan_run", {"plan": plan, "args": {"target": "src"}})
    assert "\n" not in card
    assert "agents.reader.tools[1]" in card and "[denied_tool]" in card


def test_team_plan_run_card_summarises_a_builtin_template() -> None:
    card = _describe("team_plan_run", {"template": "adversarial-review", "args": {"target": "src"}})
    assert card.startswith("Run team plan: adversarial-review, at most 16 agents, 4 at a time")


def test_team_plan_run_card_reads_templates_through_load_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The card reads a template the way the tool does (D-10), not from a
    directory of its own: move the templates and the card follows."""
    from r4.teams.plan import templates as plan_templates

    template = {**_INLINE_PLAN, "name": "three-readers"}
    (tmp_path / "three-readers.json").write_text(json.dumps(template), encoding="utf-8")
    monkeypatch.setattr(plan_templates, "TEMPLATES_DIR", tmp_path)

    card = _describe("team_plan_run", {"template": "three-readers", "args": {"target": "src"}})
    assert card.startswith("Run team plan: three-readers, at most 3 agents")


@pytest.mark.parametrize("name", ["Adversarial-Review", "ADVERSARIAL-REVIEW"])
def test_team_plan_run_card_refuses_names_load_template_refuses(name: str) -> None:
    """The tool refuses these names. The old card accepted mixed case, and a
    case-insensitive disk (macOS) opened the file, so it summarised a plan
    the tool would then refuse."""
    from r4.teams.plan.templates import load_template

    with pytest.raises(KeyError):
        load_template(name)
    card = _describe("team_plan_run", {"template": name, "args": {"target": "src"}})
    assert "\n" not in card
    assert card.startswith(f"Run team plan: {name} (invalid: 1 problem(s), first no plan template")
    assert card.endswith("the tool refuses it before any spend)")


# --- taint hand-off -------------------------------------------------------------------


def _segment(text: str, at: float = 0.0) -> TaintedSegment:
    return TaintedSegment(
        text_excerpt=text, taint_class="rag_chunk", source_tool="knowledge_query", registered_at=at
    )


def test_export_import_segments_round_trip() -> None:
    source = TaintRegistry()
    source.register("knowledge_query", _UNTRUSTED)
    exported = source.export_segments()
    assert {type(seg) for seg in exported} == {TaintedSegment}

    target = TaintRegistry()
    before = time.time()
    assert target.import_segments(exported) == len(exported)
    assert target.is_tainted({"content": _UNTRUSTED}) == "rag_chunk"
    assert min(seg.registered_at for seg in target.export_segments()) >= before  # re-stamped
    # A re-import refreshes, never duplicates.
    assert (target.import_segments(exported), len(target)) == (len(exported), len(exported))


def test_import_never_evicts_existing_entries() -> None:
    target = TaintRegistry(max_entries=3)
    own = [f"lead's own untrusted excerpt number {i} from the knowledge base" for i in range(3)]
    for text in own:
        target.register("knowledge_query", text)
    assert len(target) == 3

    incoming = [
        _segment(f"agent excerpt {i} that a plan agent read from a document") for i in range(4)
    ]
    assert target.import_segments(incoming) == 4
    excerpts = {seg.text_excerpt for seg in target.export_segments()}
    assert (set(own) <= excerpts, len(target)) == (True, 7)


def test_import_accepts_journal_dicts_and_skips_junk() -> None:
    target = TaintRegistry()
    added = target.import_segments(
        [
            {"text_excerpt": _UNTRUSTED, "taint_class": "web", "source_tool": "web_search"},
            {"text_excerpt": ""},
            "not a segment",
            None,
        ]
    )
    assert added == 1
    assert target.is_tainted([_UNTRUSTED]) == "web"


def test_export_purges_expired_entries() -> None:
    registry = TaintRegistry(ttl_seconds=60)
    registry.import_segments([_segment(_UNTRUSTED)])
    registry._entries[0] = _segment(_UNTRUSTED, at=time.time() - 3600)
    assert registry.export_segments() == ()


def test_a_register_racing_an_import_is_never_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Findings 11/47 (ADR-092 D-6, ADR-039): ``team_plan_result`` imports a
    plan's excerpts on one tool worker while ``lab_file_read``'s result is
    registered on another. The import copies the ring, then swaps in a new
    one; a register landing between the two went to the old ring and was
    dropped, so that lab content passed the fence unasked. The swap point
    is made deterministic by holding the import there while the other
    worker registers (a daemon thread, joined with a timeout: no spin)."""
    from r4.companion import taint as taint_module

    registry = TaintRegistry()
    lab_text = "lab notebook: the sample was annealed at nine hundred kelvin for twelve hours"
    real_deque = taint_module.deque
    workers: list[threading.Thread] = []

    def _at_the_swap(*args, **kwargs):
        if args and not workers:  # the import rebuilding its ring, not a constructor
            worker = threading.Thread(
                target=registry.register, args=("lab_file_read", lab_text), daemon=True
            )
            workers.append(worker)
            worker.start()
            worker.join(0.5)  # unlocked, the register finishes here, on the old ring
        return real_deque(*args, **kwargs)

    monkeypatch.setattr(taint_module, "deque", _at_the_swap)
    assert registry.import_segments([_segment(_UNTRUSTED)]) == 1
    workers[0].join(5)

    assert not workers[0].is_alive()
    assert registry.is_tainted({"content": lab_text}) == "lab_file"
    assert registry.is_tainted({"content": _UNTRUSTED}) == "rag_chunk"
