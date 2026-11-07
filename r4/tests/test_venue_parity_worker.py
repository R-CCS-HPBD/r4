"""ADR-099 D-8 (M-6): the remote worker acts like the user's local session.

- ask_user raises a durable question and returns the owner's answer; it
  expires on timeout with the no-answer text (the old worker auto-answered);
- ask_user_mode=auto is the only auto-answer;
- the worker applies the local bridge's rule: auto_safe by default,
  autopilot -> trust, the owner's pushed settings from the next turn.
"""

from __future__ import annotations

import httpx
import pytest
from test_venue_worker import FakeRail, FakeSession, _worker

from r4.companion import tools as companion_tools
from r4.companion.agent_settings import (
    ASK_USER_AUTO_ANSWER,
    ASK_USER_NO_ANSWER,
    ASK_USER_OLD_CLIENT,
)
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.venue_rail import VenueRailClient, VenueRailError
from r4.venue_worker import WorkerEnv


class QuestionRail(FakeRail):
    """FakeRail + the question verbs; ``answers`` scripts get_question. The
    owner's client is an RS-1 one: it sent its settings (rev 1)."""

    def __init__(self, queue=None):
        super().__init__(queue)
        self.settings = {"rev": 1, "settings": {}}
        self.questions: dict[str, dict] = {}
        self.answers: list[tuple[str, str]] = []  # (status, answer) per poll
        self.expired: list[str] = []
        self.ask_refusal: VenueRailError | None = None

    def ask_question(self, *, turn_id, question, options):
        if self.ask_refusal:
            raise self.ask_refusal
        qid = f"q-{len(self.questions) + 1}"
        record = {
            "question_id": qid,
            "turn_id": turn_id,
            "question": question,
            "options": options,
            "status": "pending",
            "answer": "",
        }
        self.questions[qid] = record
        return dict(record)

    def get_question(self, question_id):
        record = dict(self.questions[question_id])
        if self.answers:
            record["status"], record["answer"] = self.answers.pop(0)
        return record

    def expire_question(self, question_id):
        self.expired.append(question_id)
        self.questions[question_id]["status"] = "expired"
        return dict(self.questions[question_id])


class RealPermsSession(FakeSession):
    """FakeSession with the real PermissionManager (modes, session approvals)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.permissions = PermissionManager()


@pytest.fixture(autouse=True)
def _reset_workflow_arm():
    yield
    companion_tools.set_workflow_armed_flag(False)


class TestAskUser:
    def test_question_reaches_the_owner_and_returns_the_answer(self):
        rail = QuestionRail()
        rail.answers = [("pending", ""), ("answered", "use dataset B")]
        worker, _, clock = _worker(rail, RealPermsSession())
        worker.bootstrap()
        answer = worker._ask_user("Which dataset?", ["A", "B"])
        assert answer == "use dataset B"
        assert rail.questions["q-1"]["options"] == ["A", "B"]
        assert any(hb["phase"] == "awaiting_user" for hb in rail.heartbeats)
        assert clock.t > 100.0  # it waited one poll
        assert rail.expired == []

    def test_timeout_expires_the_question_with_the_no_answer_text(self):
        rail = QuestionRail()  # always pending
        worker, _, _ = _worker(rail, RealPermsSession(), question_timeout_sec=6)
        worker.bootstrap()
        answer = worker._ask_user("Proceed?", [])
        assert answer == ASK_USER_NO_ANSWER
        assert "do not assume an answer" in answer
        assert rail.expired == ["q-1"]

    def test_expired_elsewhere_is_no_answer(self):
        rail = QuestionRail()
        rail.answers = [("expired", "")]
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        assert worker._ask_user("?", []) == ASK_USER_NO_ANSWER

    def test_long_wait_keeps_the_lease_warm_and_honours_stop(self):
        rail = QuestionRail()
        rail.stop_after_heartbeats = 3
        worker, _, _ = _worker(rail, RealPermsSession(), heartbeat_sec=4, question_timeout_sec=600)
        worker.bootstrap()
        assert worker._ask_user("?", []) == ASK_USER_NO_ANSWER
        assert [hb["phase"] for hb in rail.heartbeats][1:3] == ["awaiting_user", "awaiting_user"]
        assert worker._stop is True

    def test_auto_mode_answers_without_a_question(self):
        rail = QuestionRail()
        rail.settings = {"rev": 1, "settings": {"ask_user_mode": "auto"}}
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        assert worker._ask_user("?", ["x"]) == ASK_USER_AUTO_ANSWER
        assert rail.questions == {}

    def test_rail_refusal_is_named_never_assumed(self):
        rail = QuestionRail()
        rail.ask_refusal = VenueRailError(404, "not found")
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        answer = worker._ask_user("?", [])
        assert "could not reach the user" in answer and "not found" in answer
        assert "do not assume an answer" in answer


class FlakyRail(QuestionRail):
    """get_question fails with ``errors`` (statuses) first, then answers."""

    def __init__(self, errors, then=("answered", "use B")):
        super().__init__()
        self.errors = list(errors)
        self.then = then
        self.polls = 0

    def get_question(self, question_id):
        self.polls += 1
        if self.errors:
            raise VenueRailError(self.errors.pop(0), "transient")
        record = dict(self.questions[question_id])
        record["status"], record["answer"] = self.then
        return record


class TestAskUserFixes:
    """RS-1 review fixes A1, A2, A4, A6 (2026-10-23)."""

    def test_heartbeats_name_the_question_never_its_text(self):
        # A1: the runtime row reaches admin views; the question is content.
        rail = QuestionRail()
        rail.answers = [("pending", "")] * 3 + [("answered", "x")]
        worker, _, _ = _worker(rail, RealPermsSession(), heartbeat_sec=1)
        worker.bootstrap()
        worker._ask_user("Which patient cohort?", ["A"])
        awaiting = [hb for hb in rail.heartbeats if hb["phase"] == "awaiting_user"]
        assert len(awaiting) >= 2  # the first and a keep-alive
        assert {hb["detail"] for hb in awaiting} == {"question q-1"}
        assert not any("cohort" in str(hb) for hb in rail.heartbeats)

    @pytest.mark.parametrize("status", [0, 502, 503])
    def test_a_transient_poll_failure_is_retried(self, status):
        # A2: one failed poll used to abandon the question (left pending).
        rail = FlakyRail([status, status])
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        assert worker._ask_user("?", []) == "use B"
        assert rail.polls == 3 and rail.expired == []

    def test_retries_back_off(self):
        rail = FlakyRail([0, 0, 0, 0])
        worker, _, clock = _worker(rail, RealPermsSession())
        worker.bootstrap()
        start = clock.t
        assert worker._ask_user("?", []) == "use B"
        assert clock.t - start == pytest.approx(2 + 4 + 8 + 16)

    def test_retries_stop_at_the_deadline_and_expire(self):
        rail = FlakyRail([0] * 1000)
        worker, _, _ = _worker(rail, RealPermsSession(), question_timeout_sec=60)
        worker.bootstrap()
        assert worker._ask_user("?", []) == ASK_USER_NO_ANSWER
        assert rail.expired == ["q-1"]
        assert rail.heartbeats[-1]["phase"] == "running"

    @pytest.mark.parametrize("status", [401, 404])
    def test_a_lost_lease_or_question_ends_the_wait_and_expires_it(self, status):
        rail = FlakyRail([status])
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        assert worker._ask_user("?", []) == ASK_USER_NO_ANSWER
        assert rail.polls == 1
        assert rail.expired == ["q-1"]
        assert rail.heartbeats[-1]["phase"] == "running"

    def test_stop_expires_the_question(self):
        rail = QuestionRail()
        rail.stop_after_heartbeats = 2
        worker, _, _ = _worker(rail, RealPermsSession(), heartbeat_sec=4)
        worker.bootstrap()
        assert worker._ask_user("?", []) == ASK_USER_NO_ANSWER
        assert rail.expired == ["q-1"]
        assert rail.heartbeats[-1]["phase"] == "running"

    def test_an_answer_racing_the_timeout_is_used(self):
        # A6: the owner answered between the last poll and the expiry.
        class Race(QuestionRail):
            def expire_question(self, question_id):
                self.questions[question_id].update(status="answered", answer="B")
                return dict(self.questions[question_id])

        rail = Race()
        worker, _, _ = _worker(rail, RealPermsSession(), question_timeout_sec=4)
        worker.bootstrap()
        assert worker._ask_user("?", []) == "B"
        assert rail.heartbeats[-1]["phase"] == "running"
        assert rail.heartbeats[-1]["detail"] == "question answered"


class TestOldClient:
    """A4 (agent decision 2026-10-23): a client that never sent settings
    (rev 0) cannot show a remote question; say so after a short wait
    instead of blocking the turn for 30 minutes."""

    def test_rev_0_waits_briefly_then_names_the_upgrade(self):
        rail = QuestionRail()
        rail.settings = {"rev": 0, "settings": {}}
        worker, _, clock = _worker(rail, RealPermsSession())
        worker.bootstrap()
        start = clock.t
        answer = worker._ask_user("?", [])
        assert answer == ASK_USER_OLD_CLIENT
        assert "your r4 client is too old to answer remote questions; upgrade r4" in answer
        assert "do not assume an answer" in answer.lower()
        assert clock.t - start == pytest.approx(120, abs=2)
        assert rail.expired == ["q-1"]

    def test_the_short_timeout_is_configurable(self):
        rail = QuestionRail()
        rail.settings = {"rev": 0, "settings": {}}
        worker, _, clock = _worker(rail, RealPermsSession(), question_timeout_old_client_sec=10)
        worker.bootstrap()
        start = clock.t
        assert worker._ask_user("?", []) == ASK_USER_OLD_CLIENT
        assert clock.t - start == pytest.approx(10, abs=2)
        env = WorkerEnv.from_environ(
            {
                "R4_VENUE_SESSION_ID": "s1",
                "R4_VENUE_RAIL_URL": "http://egress:8081",
                "R4_VENUE_SESSION_TOKEN_FILE": "/run/secrets/t",
                "R4_VENUE_QUESTION_TIMEOUT_OLD_CLIENT_SEC": "45",
            }
        )
        assert env.question_timeout_old_client_sec == 45.0
        assert env.question_timeout_sec == 1800.0

    def test_an_old_client_answer_in_time_is_still_used(self):
        rail = QuestionRail()
        rail.settings = {"rev": 0, "settings": {}}
        rail.answers = [("answered", "B")]
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        assert worker._ask_user("?", []) == "B"

    def test_settings_sent_after_bootstrap_count(self):
        # A client that pushed its settings after the worker started (e.g. a
        # retried push) is an RS-1 client: the full timeout applies.
        rail = QuestionRail()
        rail.settings = {"rev": 0, "settings": {}}
        worker, _, clock = _worker(rail, RealPermsSession(), question_timeout_sec=600)
        worker.bootstrap()
        rail.settings = {"rev": 1, "settings": {}}
        start = clock.t
        assert worker._ask_user("?", []) == ASK_USER_NO_ANSWER
        assert clock.t - start >= 590


class TestSettings:
    def test_default_is_the_bridges_auto_safe(self):
        session = RealPermsSession()
        worker, _, _ = _worker(QuestionRail(), session)
        worker.bootstrap()
        assert session.permissions.mode == PermissionMode.AUTO_SAFE

    def test_autopilot_session_trusts(self):
        session = RealPermsSession()
        session.metadata["interaction_mode"] = "autopilot"
        worker, _, _ = _worker(QuestionRail(), session)
        worker.bootstrap()
        assert session.permissions.mode == PermissionMode.TRUST

    def test_pushed_settings_apply_at_bootstrap(self):
        rail = QuestionRail()
        rail.settings = {
            "rev": 4,
            "settings": {
                "permission_mode": "ask_always",
                "interaction_mode": "plan",
                "workflow_armed": True,
                "write_requires_plan": True,
                "session_approved_tools": ["file_write"],
                "unknown_future_key": 1,
            },
        }
        session = RealPermsSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        # ADR-104 D-1: the mode decides; a pushed permission_mode is derived
        # (an older client's ask_always override no longer pins anything).
        assert session.permissions.mode == PermissionMode.AUTO_SAFE
        assert session.metadata["interaction_mode"] == "plan"
        assert session.metadata["write_requires_plan"] is True
        assert companion_tools._WORKFLOW_ARMED is True
        assert session.permissions.session_approved_names == frozenset({"file_write"})

    def test_a_change_between_turns_applies_at_the_next_pop(self):
        rail = QuestionRail(queue=["first", "second"])
        session = RealPermsSession()
        seen: list[PermissionMode] = []
        original_send = session.send

        def send(text, *, stream=False):
            seen.append(session.permissions.mode)
            return original_send(text, stream=stream)

        session.send = send
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        assert worker.run_once() is True
        rail.settings = {"rev": 2, "settings": {"permission_mode": "trust"}}
        assert worker.run_once() is True
        assert seen == [PermissionMode.AUTO_SAFE, PermissionMode.TRUST]

    def test_same_rev_is_not_reapplied(self):
        rail = QuestionRail(queue=["one"])
        rail.settings = {"rev": 1, "settings": {"permission_mode": "trust"}}
        session = RealPermsSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        session.permissions.set_mode(PermissionMode.DENY)  # e.g. a fence
        worker.run_once()
        assert session.permissions.mode == PermissionMode.DENY

    def test_unreadable_settings_keep_the_defaults(self):
        rail = QuestionRail()

        def refuse():
            raise VenueRailError(404, "no route")

        rail.get_settings = refuse
        session = RealPermsSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        assert session.permissions.mode == PermissionMode.AUTO_SAFE

    def test_a_refused_lease_at_the_settings_read_keeps_the_turn_queued(self):
        # A10: the read used to follow the pop, so a 401 lost the popped turn.
        rail = QuestionRail(queue=["keep me"])
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()

        def refuse():
            raise VenueRailError(401, "lease_revoked")

        rail.get_settings = refuse
        with pytest.raises(VenueRailError):
            worker.run_once()
        assert rail.queue == ["keep me"]

    def test_lease_refusal_on_settings_read_propagates(self):
        rail = QuestionRail()

        def refuse():
            raise VenueRailError(401, "lease_revoked")

        rail.get_settings = refuse
        worker, _, _ = _worker(rail, RealPermsSession())
        with pytest.raises(VenueRailError):
            worker.bootstrap()


def _rail(handler):
    return VenueRailClient(
        "http://egress:8081", "sess-1", "tok", transport=httpx.MockTransport(handler)
    )


def test_rail_client_question_and_settings_verbs():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/settings"):
            return httpx.Response(200, json={"session_id": "sess-1", "rev": 2, "settings": {}})
        return httpx.Response(200, json={"question": {"question_id": "q-1", "status": "pending"}})

    rail = _rail(handler)
    assert rail.get_settings()["rev"] == 2
    assert rail.ask_question(turn_id="t", question="?", options=["a"])["question_id"] == "q-1"
    assert rail.get_question("q-1")["status"] == "pending"
    assert rail.expire_question("q-1")["question_id"] == "q-1"
    prefix = "/umrs/companion/sessions/sess-1/rail"
    assert seen == [
        ("GET", f"{prefix}/settings"),
        ("POST", f"{prefix}/questions"),
        ("GET", f"{prefix}/questions/q-1"),
        ("POST", f"{prefix}/questions/q-1/expire"),
    ]


class TestRound2Fixes:
    """RS-1 round-2 review (2026-10-23): findings 1, 2 and 4."""

    def _approval_heartbeats(self, question: str) -> list[dict]:
        rail = QuestionRail()
        session = RealPermsSession()
        worker, _, _ = _worker(rail, session, approval_timeout_sec=4)
        worker.bootstrap()
        # The default (auto_safe): ask_user is not a safe tool, so the real
        # PermissionManager raises an approval card before the question.
        assert session.permissions.mode == PermissionMode.AUTO_SAFE
        session.permissions.check("ask_user", {"question": question, "options": ["yes"]})
        return [hb for hb in rail.heartbeats if hb["phase"] == "awaiting_approval"]

    def test_an_approval_heartbeat_names_the_tool_never_the_call(self):
        # Finding 1: the detail was f"{tool}: {description}" — the question
        # text (or a shell command) reached the admin's session view.
        beats = self._approval_heartbeats("Share cohort P-123 genome?")
        assert beats, "the approval card heartbeats awaiting_approval"
        assert all("P-123" not in str(hb) for hb in beats)
        assert beats[0]["detail"].startswith("ask_user (approval ")

    def test_admin_view_never_shows_an_approval_card(self):
        # End to end: the real PermissionManager's card, through the admin
        # view (the umrs side also hides the detail of an older worker:
        # umrs tests/test_venue_parity_routes.py).
        frontend_service = pytest.importorskip("umrs.venues.frontend_service")
        admin_view = getattr(frontend_service, "admin_view", None)
        if admin_view is None:
            pytest.skip("the importable umrs predates RS-1 admin views")
        beats = self._approval_heartbeats("Share cohort P-123 genome?")
        row = {"runtime": {"phase": beats[0]["phase"], "detail": beats[0]["detail"]}}
        shown = admin_view(row)
        assert shown["runtime"]["phase"] == "awaiting_approval"
        assert "P-123" not in repr(shown)

    def _refusing_rail(self, phase: str) -> QuestionRail:
        rail = QuestionRail()
        beat = rail.heartbeat

        def heartbeat(**body):
            if body.get("phase") == phase:
                raise VenueRailError(503, "rail busy")
            return beat(**body)

        rail.heartbeat = heartbeat
        return rail

    def test_a_refused_awaiting_user_heartbeat_still_waits_for_the_answer(self):
        # Finding 2: the refusal escaped _ask_user; the question stayed
        # pending with no worker reading it (the answer was lost).
        rail = self._refusing_rail("awaiting_user")
        rail.answers = [("pending", ""), ("answered", "go")]
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        assert worker._ask_user("Proceed?", []) == "go"

    def test_a_refused_awaiting_user_heartbeat_never_orphans_the_question(self):
        rail = self._refusing_rail("awaiting_user")
        worker, _, _ = _worker(rail, RealPermsSession(), question_timeout_sec=6)
        worker.bootstrap()
        assert worker._ask_user("Proceed?", []) == ASK_USER_NO_ANSWER
        assert rail.questions["q-1"]["status"] == "expired" and rail.expired == ["q-1"]

    def test_a_refused_approval_heartbeat_still_waits_for_the_decision(self):
        rail = self._refusing_rail("awaiting_approval")
        rail.approval_script = ["pending", "once"]
        session = RealPermsSession()
        worker, _, _ = _worker(rail, session, approval_timeout_sec=600)
        worker.bootstrap()
        assert session.permissions.check("shell_exec", {"command": "ls"}) is True

    def test_a_long_question_is_clipped_not_refused(self):
        # Finding 4: the rail refuses > 4000 chars / > 24 options (422) and
        # the model heard "could not reach the user"; locally it is shown.
        rail = QuestionRail()
        rail.answers = [("answered", "ok")]
        worker, _, _ = _worker(rail, RealPermsSession())
        worker.bootstrap()
        options = [f"opt {i}" for i in range(30)] + ["  ", "x" * 900]
        assert worker._ask_user("Q" * 5000, options) == "ok"
        sent = rail.questions["q-1"]
        assert len(sent["question"]) == 4000 and sent["question"].endswith("…")
        assert sent["options"] == [f"opt {i}" for i in range(24)]
        rail.answers = [("answered", "ok")]
        worker._ask_user("", ["a", "", "b" * 900])
        assert rail.questions["q-2"]["options"] == ["a", "b" * 500]
        assert rail.questions["q-2"]["question"] == "(no question text)"
