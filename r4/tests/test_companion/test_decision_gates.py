"""Decision gates in the harness (2026-10-02, ADR-076 D-3).

- The permission gate is consulted only where a RISKY call would be
  auto-approved (trust allowlist, a session approval, a remembered call). A
  reason turns that into an explicit approval. It can add a question, never
  remove one, and a broken gate neither blocks nor approves.
- The lesson check can drop a rule-matched "correction", never invent one;
  the rules no longer read agreement ("No, that's great") as a correction.
"""

from __future__ import annotations

from r4.companion.harness.steering import SteeringLoop, _is_correction
from r4.companion.permissions import PermissionManager, PermissionMode


def _manager(mode, gate):
    asked = []

    def approval(tool, description, args):
        asked.append((tool, description))
        return True

    manager = PermissionManager(mode=mode, approval_callback=approval)
    manager.set_escalation_check(gate)
    return manager, asked


def test_trust_mode_gate_doubt_is_a_note_not_a_question():
    """ADR-104 amendment 2: true autopilot — the gate's doubt is noted."""
    from r4.companion.permissions import take_permission_notes

    manager, asked = _manager(
        PermissionMode.TRUST, lambda tool, args: "decision model: looks destructive"
    )
    assert manager.check("shell_exec", {"command": "make deploy"}) is True
    assert asked == []
    assert any("looks destructive" in note for note in take_permission_notes())


def test_chat_session_always_is_escalated_when_the_gate_has_a_reason():
    manager, asked = _manager(
        PermissionMode.AUTO_SAFE, lambda tool, args: "decision model: looks destructive"
    )
    manager.approve_for_session("shell_exec")
    assert manager.check("shell_exec", {"command": "make deploy"}) is True  # the user approved
    assert asked and "[fence: decision model: looks destructive]" in asked[0][1]


def test_trust_mode_proceeds_without_a_question_when_the_gate_is_quiet():
    seen = []
    manager, asked = _manager(PermissionMode.TRUST, lambda tool, args: seen.append(tool))
    assert manager.check("shell_exec", {"command": "ls"}) is True
    assert seen == ["shell_exec"] and asked == []


def test_safe_tools_never_reach_the_gate_and_ask_always_mode_always_asks():
    seen = []
    trust, _ = _manager(PermissionMode.TRUST, lambda tool, args: seen.append(tool) or "never")
    assert trust.check("file_read", {"path": "a"}) is True and seen == []
    ask, asked = _manager(PermissionMode.ASK_ALWAYS, lambda tool, args: seen.append(tool))
    ask.check("shell_exec", {"command": "ls"})
    assert len(asked) == 1 and seen == []  # the gate only ever adds to auto-approvals


def test_a_session_approval_is_gated_too():
    manager, asked = _manager(
        PermissionMode.AUTO_SAFE, lambda tool, args: "decision model: off-task"
    )
    manager.approve_for_session("file_write")
    manager.check("file_write", {"path": "/etc/hosts", "content": "x"})
    assert asked and "off-task" in asked[0][1]


def test_a_broken_gate_neither_blocks_nor_asks():
    def boom(tool, args):
        raise RuntimeError("decision endpoint exploded")

    manager, asked = _manager(PermissionMode.TRUST, boom)
    assert manager.check("shell_exec", {"command": "ls"}) is True and asked == []


def test_agreement_is_not_a_correction_but_corrections_still_are():
    for agree in ("No, that's great", "no problem", "No worries!", "no, it's fine", "No. Perfect."):
        assert not _is_correction(agree), agree
    for correct in (
        "No, use the other file",
        "no. run it on slurm",
        "Actually, use v2",
        "that's wrong",
    ):
        assert _is_correction(correct), correct


def test_the_lesson_check_can_veto_but_never_invent(tmp_path):
    loop = SteeringLoop(lessons_dir=tmp_path)
    loop.observe_turn("hi", "Edited a.py")
    loop.observe_turn("No, edit b.py instead", "ok", confirm=lambda u, r: False)
    assert loop.list_lessons() == []  # vetoed
    loop.observe_turn("No, edit c.py instead", "ok", confirm=lambda u, r: True)
    assert len(loop.list_lessons()) == 1
    loop.observe_turn("Thanks, looks right", "ok", confirm=lambda u, r: True)
    assert len(loop.list_lessons()) == 1  # not a correction: the check is never asked to create one


# --- gate cost (W7/H13, 2026-10-05): the real decision gate behind TRUST mode --------


class _GateClient:
    """A decision endpoint that always reads harm (noul: B = true) and counts calls."""

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.config = SimpleNamespace(decision_model="Bespoke-Nimble-9B")
        self.calls = 0

    def text_completion(self, payload, *, timeout=None):
        self.calls += 1
        return {
            "choices": [{"text": "B", "logprobs": {"top_logprobs": [{"A": -6.0, "B": 0.0}]}}],
            "usage": {},
        }


def _trust_with_real_gate(turn):
    from r4.decisions.harness import tool_call_escalation

    client, log = _GateClient(), []
    manager, asked = _manager(
        PermissionMode.TRUST,
        lambda tool, args: tool_call_escalation(
            client, tool, args, "fix the build", log=log, turn=turn[0]
        ),
    )
    return manager, asked, client, log


def test_trust_mode_read_only_shell_runs_without_a_decision_call():
    manager, asked, client, log = _trust_with_real_gate(["t1"])
    assert manager.check("shell_exec", {"command": "ls -la src"}) is True
    assert manager.check("shell_exec", {"command": "cat Makefile | head -20"}) is True
    assert client.calls == 0 and asked == []
    assert log[-1]["outcome"] == "skipped" and log[-1]["count"] == 2


def test_trust_mode_mutating_shell_is_still_escalated_once_per_turn():
    turn = ["t1"]
    manager, asked, client, log = _trust_with_real_gate(turn)
    for _ in range(3):
        manager.check("shell_exec", {"command": "rm -rf build"})
    assert client.calls == 1  # one decision call; the repeats reuse the verdict
    assert asked == []  # ADR-104 amendment 2: autopilot notes the doubt, never asks
    turn[0] = "t2"
    manager.check("shell_exec", {"command": "rm -rf build"})
    assert client.calls == 2  # a new turn asks again
    assert [e["outcome"] for e in log] == ["asked", "cached", "cached", "asked"]


def test_gate_entries_land_in_the_transcript(monkeypatch):
    """Decision lane observability (2026-10-06): one transcript line per new entry."""
    from types import SimpleNamespace

    import r4.decisions.harness as harness
    from r4.companion.session import CompanionSession

    def fake_gate(client, tool, args, request, *, log, turn):
        if args.get("command") != "again":
            log.append({"purpose": "tool_gate", "outcome": "asked", "escalated": False})
        return None

    monkeypatch.setattr(harness, "tool_call_escalation", fake_gate)
    written = []
    session = SimpleNamespace(
        metadata={},
        client=None,
        _turn_user_message="build it",
        _current_turn_id="t1",
        _transcript=SimpleNamespace(write=lambda *a: written.append(a)),
    )
    gate = CompanionSession._decision_tool_gate
    assert gate(session, "shell_exec", {"command": "make"}) is None
    gate(session, "shell_exec", {"command": "again"})  # no new entry, no line
    assert written == [("decision", "shell_exec", session.metadata["decision_log"][0])]
