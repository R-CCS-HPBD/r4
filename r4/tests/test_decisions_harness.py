"""Harness-side decisions (2026-10-02, r4.decisions.harness).

The decision model reads a plan reply the rules could not place; a
confident run/cancel reading becomes a one-word question, never an action
(ADR-076 D-3). Every call — failures too — lands in the session's
decision log; a failure is never read as an answer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.decisions.harness import (
    CONFIDENT,
    INTENT_NOTICES,
    PLAN_REPLY_RUBRIC,
    PlanReplyHint,
    intent_notice,
    plan_reply_hint,
)

# Candidate order follows the criteria: A=execute, B=cancel, C=revise, D=unclear.
FAVOUR = {
    "execute": {"A": 0.0, "B": -6.0, "C": -6.0, "D": -6.0},
    "cancel": {"A": -6.0, "B": 0.0, "C": -6.0, "D": -6.0},
    "revise": {"A": -6.0, "B": -6.0, "C": 0.0, "D": -6.0},
    "split": {"A": 0.0, "B": -6.0, "C": 0.0, "D": -6.0},
}


class FakeClient:
    def __init__(self, letters=None, *, decision_model="Bespoke-Nimble-9B", error=None):
        self.config = SimpleNamespace(decision_model=decision_model)
        self.letters, self.error, self.calls = letters, error, []

    def text_completion(self, payload, *, timeout=None):
        self.calls.append((payload, timeout))
        if self.error:
            raise self.error
        return {
            "choices": [{"text": "A", "logprobs": {"top_logprobs": [self.letters]}}],
            "usage": {},
        }


def test_off_means_no_call_and_no_log():
    client, log = FakeClient(decision_model="off"), []
    assert plan_reply_hint(client, "plan", "はい", log=log) is None
    assert client.calls == [] and log == []


def test_a_confident_reading_is_logged_with_its_rubric():
    client, log = FakeClient(FAVOUR["execute"]), []
    hint = plan_reply_hint(client, "Refactor the matrix", "はい、お願いします", log=log)
    assert hint.intent == "execute" and hint.probability > 0.95
    (entry,) = log
    assert entry["purpose"] == "plan_reply" and entry["rubric"] == PLAN_REPLY_RUBRIC
    assert entry["choice"] == "execute" and set(entry["probabilities"]) == {
        "execute",
        "cancel",
        "revise",
        "unclear",
    }
    assert client.calls[0][1] == 5.0  # a harness gate never waits long
    assert "はい、お願いします" in client.calls[0][0]["prompt"]


def test_a_failure_is_logged_and_gives_no_hint():
    client, log = FakeClient(error=TimeoutError("relaying")), []
    assert plan_reply_hint(client, "plan", "go for it, friend", log=log) is None
    assert "TimeoutError" in log[0]["error"]


def test_the_log_keeps_the_last_fifty():
    client, log = FakeClient(FAVOUR["revise"]), [{"old": i} for i in range(60)]
    plan_reply_hint(client, "plan", "something", log=log)
    assert len(log) == 50 and log[-1]["choice"] == "revise"


@pytest.mark.parametrize(
    "hint, notice",
    [
        (PlanReplyHint("execute", 0.97), INTENT_NOTICES["execute"]),
        (PlanReplyHint("cancel", CONFIDENT), INTENT_NOTICES["cancel"]),
        (PlanReplyHint("execute", CONFIDENT - 0.01), None),  # not confident: the rules stand
        (PlanReplyHint("revise", 0.99), None),  # revise / unclear: the rules stand
        (PlanReplyHint("unclear", 0.99), None),
        (None, None),
    ],
)
def test_only_a_confident_run_or_cancel_reading_changes_the_question(hint, notice):
    assert intent_notice(hint) == notice


def test_a_split_reading_is_not_confident():
    hint = plan_reply_hint(FakeClient(FAVOUR["split"]), "plan", "eh, sure, but", log=[])
    assert hint.probability < CONFIDENT and intent_notice(hint) is None


# --- tool gate (escalate-only) and lesson check (veto-only) ---------------------
# noul candidates: A = false, B = true.
TRUE = {"A": -6.0, "B": 0.0}
FALSE = {"A": 0.0, "B": -6.0}


def test_tool_gate_escalates_a_harmful_looking_call_with_a_reason():
    from r4.decisions.harness import tool_call_escalation

    client, log = FakeClient(TRUE), []
    reason = tool_call_escalation(
        client, "shell_exec", {"command": "rm -rf ~/data"}, "list my files", log=log
    )
    assert reason and "shell_exec" in reason and "ADR-076" in reason
    assert log[-1]["purpose"] == "tool_gate" and log[-1]["escalated"] is True
    assert "rm -rf ~/data" in client.calls[0][0]["prompt"]


def test_tool_gate_stays_quiet_when_harmless_off_or_failing():
    from r4.decisions.harness import tool_call_escalation

    assert (
        tool_call_escalation(FakeClient(FALSE), "file_write", {"path": "a"}, "write a", log=[])
        is None
    )
    off = FakeClient(TRUE, decision_model="off")
    assert (
        tool_call_escalation(off, "shell_exec", {"command": "x"}, "y", log=[]) is None
        and off.calls == []
    )
    log = []
    assert (
        tool_call_escalation(FakeClient(error=OSError("down")), "shell_exec", {}, "y", log=log)
        is None
    )
    assert "OSError" in log[-1]["error"]


def test_tool_gate_evidence_cuts_long_file_contents():
    from r4.decisions.harness import tool_call_escalation

    client = FakeClient(FALSE)
    tool_call_escalation(
        client, "file_write", {"path": "big.txt", "content": "x" * 10_000}, "save it", log=[]
    )
    assert len(client.calls[0][0]["prompt"]) < 5_000


def test_lesson_check_only_ever_vetoes():
    from r4.decisions.harness import lesson_is_real

    log = []
    assert lesson_is_real(FakeClient(FALSE), "No, that's great", "Done.", log=log) is False
    assert log[-1]["kept"] is False
    assert lesson_is_real(FakeClient(TRUE), "No, use the other file", "Edited a.py", log=[]) is True
    assert lesson_is_real(FakeClient(TRUE, decision_model="off"), "no", "x", log=[]) is True
    assert (
        lesson_is_real(FakeClient(error=TimeoutError()), "no", "x", log=[]) is True
    )  # failure: rules decide


# --- loop control ------------------------------------------------------------------
# choice candidates: A = done, B = continue, C = failed.


def test_loop_decision_uses_only_a_confident_reading():
    from r4.decisions.harness import loop_decision

    log = []
    assert (
        loop_decision(
            FakeClient({"A": 0.0, "B": -6.0, "C": -6.0}), "goal", 2, "converged at 1e-6", log=log
        )
        == "done"
    )
    assert log[-1]["used"] is True and log[-1]["purpose"] == "loop_decision"
    split = FakeClient({"A": 0.0, "B": 0.0, "C": -6.0})
    assert loop_decision(split, "goal", 2, "maybe", log=log) is None and log[-1]["used"] is False
    assert loop_decision(FakeClient(error=OSError()), "goal", 1, "x", log=log) is None


def test_a_mock_config_value_is_not_a_decision_model():
    from unittest.mock import MagicMock

    from r4.decisions.harness import loop_decision

    client = MagicMock()
    assert loop_decision(client, "goal", 1, "obs") is None
    client.text_completion.assert_not_called()


def test_the_orchestrator_asks_the_decision_model_first_then_the_chat_model():
    from unittest.mock import MagicMock

    from r4.agent import ResearchBudget, ResearchOrchestrator

    client = FakeClient({"A": -6.0, "B": -6.0, "C": 0.0})
    client.config.project_id = "p"
    client.chat = MagicMock(side_effect=AssertionError("chat model must not be asked"))
    orch = ResearchOrchestrator(client)
    assert (
        orch._replan_decision("goal", "the solver diverges every time", 3, ResearchBudget())
        == "failed"
    )
    assert orch.decision_log[-1]["choice"] == "failed"

    unsure = FakeClient({"A": 0.0, "B": 0.0, "C": 0.0})
    unsure.config.project_id = "p"
    unsure.chat = MagicMock(return_value=SimpleNamespace(content="continue"))
    orch = ResearchOrchestrator(unsure)
    assert orch._replan_decision("goal", "some progress", 1, ResearchBudget()) == "continue"
    unsure.chat.assert_called_once()


@pytest.mark.parametrize(
    "reply, decision",
    [
        ("done", "done"),
        ("Continue.", "continue"),
        ("failed", "failed"),
        ("Not done yet, continue", "continue"),  # read as "done" before 2026-10-02
        ("The goal isn't complete", "continue"),
        ("I think the goal is achieved", "done"),
        ("This approach cannot work", "failed"),
        ("hmm", "continue"),
    ],
)
def test_the_chat_reply_parser_respects_negation(reply, decision):
    from r4.agent.research_orchestrator import _parse_replan_reply

    assert _parse_replan_reply(reply) == decision


# --- tool gate cost (W7/H13, 2026-10-05): read-only skip, per-turn verdict cache ------


class CountingClient(FakeClient):
    """FakeClient that counts decision-model calls."""

    @property
    def asked(self) -> int:
        return len(self.calls)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat README.md",
        "head -n 20 src/main.py",
        "tail -5 log.txt",
        "wc -l a.txt b.txt",
        "find src -name '*.py'",
        "cat data.csv | wc -l",
        "pwd",
    ],
)
def test_tool_gate_skips_read_only_shell_commands(command):
    from r4.decisions.harness import tool_call_escalation

    client, log = CountingClient(TRUE), []
    assert tool_call_escalation(client, "shell_exec", {"command": command}, "look", log=log) is None
    assert client.asked == 0  # no decision-model call for a read-only probe
    (entry,) = log
    assert entry["purpose"] == "tool_gate" and entry["outcome"] == "skipped"
    assert entry["skip"] == "read_only_shell" and entry["count"] == 1


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("shell_exec", {"command": "rm -rf build"}),
        ("shell_exec", {"command": "ls && rm -rf build"}),
        ("shell_exec", {"command": "ls ; rm -rf build"}),
        ("shell_exec", {"command": "ls\nrm -rf build"}),
        ("shell_exec", {"command": "cat files.txt | xargs rm"}),
        ("shell_exec", {"command": "find /tmp/x -name '*.o' -delete"}),
        ("shell_exec", {"command": "find src -exec rm {} +"}),
        ("shell_exec", {"command": "echo $(rm -rf build)"}),
        ("shell_exec", {"command": "echo `rm -rf build`"}),
        ("shell_exec", {"command": "ls > listing.txt"}),
        ("shell_exec", {"command": "cat ~/.ssh/id_rsa"}),  # credential fence: never skipped
        ("shell_exec", {"command": "python train.py"}),
        ("python_exec", {"code": "print(open('a').read())"}),  # no Python classifier exists
        ("file_write", {"path": "a.txt", "content": "x"}),
    ],
)
def test_tool_gate_still_asks_about_everything_else(tool, arguments):
    from r4.decisions.harness import tool_call_escalation

    client, log = CountingClient(TRUE), []
    assert tool_call_escalation(client, tool, arguments, "do the task", log=log)
    assert client.asked == 1 and log[-1]["outcome"] == "asked"


def test_consecutive_skips_coalesce_and_never_push_out_a_verdict():
    from r4.decisions.harness import tool_call_escalation

    client, log = CountingClient(FALSE), []
    for command in ("ls", "cat a", "ls -la", "pwd"):
        tool_call_escalation(client, "shell_exec", {"command": command}, "req", log=log)
    assert len(log) == 1 and log[0]["count"] == 4
    tool_call_escalation(client, "shell_exec", {"command": "make"}, "req", log=log)
    tool_call_escalation(client, "shell_exec", {"command": "ls"}, "req", log=log)
    assert [e["outcome"] for e in log] == ["skipped", "asked", "skipped"]


def test_tool_gate_repeat_in_one_turn_reuses_the_verdict():
    from r4.decisions.harness import tool_call_escalation

    client, log = CountingClient(TRUE), []
    first = tool_call_escalation(
        client, "shell_exec", {"command": "make install"}, "build it", log=log, turn="t3"
    )
    again = tool_call_escalation(
        client, "shell_exec", {"command": "  make install \r\n"}, "build it", log=log, turn="t3"
    )
    assert first and again == first  # the cached verdict still adds the question
    assert client.asked == 1
    asked, cached = log
    assert asked["outcome"] == "asked" and cached["outcome"] == "cached"
    assert cached["key"] == asked["key"] and cached["p_true"] == asked["p_true"]
    assert cached["escalated"] is True


def test_tool_gate_cache_misses_across_turns_and_other_arguments():
    from r4.decisions.harness import tool_call_escalation

    client, log = CountingClient(FALSE), []
    call = {"command": "make install"}
    tool_call_escalation(client, "shell_exec", call, "build it", log=log, turn="t1")
    tool_call_escalation(client, "shell_exec", call, "build it", log=log, turn="t2")
    assert client.asked == 2  # a new turn asks again
    tool_call_escalation(client, "shell_exec", {**call, "cwd": "/other"}, "x", log=log, turn="t2")
    assert client.asked == 3  # other arguments are another call
    # Without a turn id the turn's request scopes the cache.
    tool_call_escalation(client, "python_exec", {"code": "x = 1"}, "first request", log=log)
    tool_call_escalation(client, "python_exec", {"code": "x = 1"}, "first request", log=log)
    tool_call_escalation(client, "python_exec", {"code": "x = 1"}, "second request", log=log)
    assert client.asked == 5
    assert [e["outcome"] for e in log[-3:]] == ["asked", "cached", "asked"]


def test_tool_gate_failure_is_never_cached():
    from r4.decisions.harness import tool_call_escalation

    client, log = CountingClient(error=TimeoutError("relaying")), []
    for _ in range(2):
        assert (
            tool_call_escalation(client, "shell_exec", {"command": "make"}, "r", log=log, turn="t1")
            is None
        )
    assert client.asked == 2 and [e["outcome"] for e in log] == ["error", "error"]
    assert all(isinstance(e["gate_ms"], int) and e["gate_ms"] >= 0 for e in log)


def test_tool_gate_log_records_outcome_latency_and_scope():
    from r4.decisions.harness import TOOL_GATE_RUBRIC, tool_call_escalation

    client, log = CountingClient(FALSE), []
    tool_call_escalation(client, "file_write", {"path": "a", "content": "x"}, "w", log=log)
    (entry,) = log
    expected = {
        "purpose": "tool_gate",
        "rubric": TOOL_GATE_RUBRIC,
        "model": "Bespoke-Nimble-9B",
        "tool": "file_write",
        "outcome": "asked",
        "escalated": False,
    }
    assert {k: entry[k] for k in expected} == expected
    assert isinstance(entry["latency_ms"], int)
    assert isinstance(entry["gate_ms"], int)
    assert entry["gate_ms"] >= 0
    assert len(entry["key"]) == 16
    assert entry["turn"].startswith("req:")  # no turn id passed: the request scopes it


def test_tool_gate_off_still_returns_before_any_call_or_log():
    from r4.decisions.harness import tool_call_escalation

    off, log = CountingClient(TRUE, decision_model="off"), []
    for command in ("ls", "rm -rf build", "rm -rf build"):
        assert tool_call_escalation(off, "shell_exec", {"command": command}, "r", log=log) is None
    assert off.asked == 0 and log == []
