"""ADR-105 S3 / D-4: plan → commit, and who approved the commit."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion import job_watch as jw
from r4.companion.permissions import (
    COMMIT_TOOL,
    RISKY_TOOLS,
    SAFE_TOOLS,
    Decision,
    PermissionManager,
    PermissionMode,
)
from r4.companion.plan_commit_tools import build_plan_commit_tools
from r4.companion.session import _plan_read_only_tools

PLAN = {
    "plan_id": "plan-abc",
    "digest": "d" * 64,
    "signed": {"workflow_name": "relax", "parameters": {"steps": 3}, "expires_at": "x"},
    "max_gpu_hours": 0.5,
}


@pytest.fixture
def client():
    c = MagicMock()
    c.config = SimpleNamespace(project_id="p")
    c.workflows.describe.return_value = {"parameter_schema": {"type": "object"}}
    c.workflows.plan.return_value = PLAN
    c.workflows.commit.return_value = {"execution_id": "wf-1", "plan_id": "plan-abc"}
    return c


def _tools(client, pm=None) -> dict:
    return {t["name"]: t["execute"] for t in build_plan_commit_tools(client, pm)}


def test_classes():
    assert "workflow_plan" in SAFE_TOOLS and COMMIT_TOOL in RISKY_TOOLS
    assert "workflow_plan" in _plan_read_only_tools(SAFE_TOOLS)  # usable in plan mode


def test_plan_returns_the_signed_plan(client):
    out = json.loads(
        _tools(client)["workflow_plan"](workflow_name="relax", parameters={"steps": 3})
    )
    assert out["digest"] == PLAN["digest"]
    client.workflows.plan.assert_called_once_with(
        "relax", parameters={"steps": 3}, input_files=None
    )


def test_plan_prechecks_the_parameters(client):
    client.workflows.describe.return_value = {
        "parameter_schema": {"type": "object", "required": ["steps"]}
    }
    out = json.loads(_tools(client)["workflow_plan"](workflow_name="relax", parameters={}))
    assert out["code"] == "workflow_parameters_invalid"
    client.workflows.plan.assert_not_called()


def test_commit_hands_back_signed_and_digest(client):
    _tools(client)["job_commit"](plan=PLAN)
    client.workflows.commit.assert_called_once_with(
        "relax", signed=PLAN["signed"], digest=PLAN["digest"], auto=True
    )


def test_commit_refuses_a_plan_without_a_signature(client):
    out = json.loads(_tools(client)["job_commit"](plan={"signed": {}}))
    assert out["code"] == "invalid_plan"
    client.workflows.commit.assert_not_called()


def _pm(mode, *, checker=None, answer=True):
    return PermissionManager(
        mode=mode, approval_callback=lambda *a, **k: answer, trust_checker=checker
    )


def test_a_human_approved_commit_is_not_auto(client):
    pm = _pm(PermissionMode.AUTO_SAFE)
    assert pm.resolve(Decision("ask", "default", ""), COMMIT_TOOL, {"plan": PLAN})
    _tools(client, pm)["job_commit"](plan=PLAN)
    assert client.workflows.commit.call_args.kwargs["auto"] is False


def test_an_allowlisted_autopilot_commit_is_auto(client):
    pm = _pm(PermissionMode.TRUST, checker=lambda tool, scope: True)
    assert pm.resolve(Decision("allow", "trust", escalate=False), COMMIT_TOOL, {"plan": PLAN})
    _tools(client, pm)["job_commit"](plan=PLAN)
    assert client.workflows.commit.call_args.kwargs["auto"] is True


def test_autopilot_commits_as_an_auto_commit(client):
    """ADR-104 amendment 2 lifts ADR-105 D-4's autopilot ask: the commit runs
    flagged auto, so UMRS's auto-commit threshold still decides."""
    pm = _pm(PermissionMode.TRUST)
    assert pm._rung_trust(COMMIT_TOOL, {}).behavior == "allow"
    assert pm._auto_runs(COMMIT_TOOL)
    assert pm.check(COMMIT_TOOL, {"plan": PLAN})
    _tools(client, pm)["job_commit"](plan=PLAN)
    assert client.workflows.commit.call_args.kwargs["auto"] is True


def test_a_commit_is_watched_for_its_end():
    assert COMMIT_TOOL in jw.SUBMIT_TOOLS


# ── review fixes (ADR-105 S3) ───────────────────────────────────────────────


def test_an_unconsumed_human_yes_never_labels_another_commit(client):
    """A human-approved malformed call must not bank a 'human' token."""
    pm = _pm(PermissionMode.AUTO_SAFE)
    bad = {"plan": {"signed": "oops"}}
    assert pm.resolve(Decision("ask", "default", ""), COMMIT_TOOL, bad)
    assert json.loads(_tools(client, pm)["job_commit"](**bad))["code"] == "invalid_plan"
    pm.mode = PermissionMode.TRUST
    pm._trust_checker = lambda tool, scope: True
    assert pm.resolve(Decision("allow", "trust", escalate=False), COMMIT_TOOL, {"plan": PLAN})
    _tools(client, pm)["job_commit"](plan=PLAN)
    assert client.workflows.commit.call_args.kwargs["auto"] is True


def test_a_human_yes_counts_only_for_that_plan(client):
    pm = _pm(PermissionMode.AUTO_SAFE)
    other = {**PLAN, "digest": "e" * 64}
    assert pm.resolve(Decision("ask", "default", ""), COMMIT_TOOL, {"plan": other})
    _tools(client, pm)["job_commit"](plan=PLAN)
    assert client.workflows.commit.call_args.kwargs["auto"] is True


def test_a_decision_gate_ask_counts_as_human(client):
    """ADR-076 gate escalates a session 'always' into an ask the human answers
    (chat; in autopilot the doubt is only noted, ADR-104 amendment 2)."""
    pm = _pm(PermissionMode.AUTO_SAFE)
    pm._escalation_check = lambda tool, args: "costly run"
    assert pm.resolve(Decision("allow", "session", escalate=True), COMMIT_TOOL, {"plan": PLAN})
    _tools(client, pm)["job_commit"](plan=PLAN)
    assert client.workflows.commit.call_args.kwargs["auto"] is False


def test_reset_session_forgets_commit_approvals():
    pm = _pm(PermissionMode.AUTO_SAFE)
    assert pm.resolve(Decision("ask", "default", ""), COMMIT_TOOL, {"plan": PLAN})
    pm.reset_session()
    assert pm.take_commit_approval(PLAN["digest"]) is False


def test_team_agents_never_commit_compute():
    from r4_contracts.team_engine.plan import TEAM_AGENT_DENY

    assert COMMIT_TOOL in TEAM_AGENT_DENY


def test_a_fork_shares_the_commit_store():
    pm = _pm(PermissionMode.AUTO_SAFE)
    fork = pm.fork_for_agent(label="agent", allow=None, run_id="r1")
    assert fork._commit_approvals is pm._commit_approvals


def test_the_card_shows_only_what_umrs_signed():
    from r4.companion.permissions import _describe

    forged = {**PLAN, "max_gpu_hours": 0.1, "lane": "made-up"}
    card = _describe(COMMIT_TOOL, {"plan": forged})
    assert "workflow_name: relax" in card and "dddddddddddd" in card
    assert "0.1" not in card and "made-up" not in card
