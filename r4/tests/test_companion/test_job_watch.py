"""ADR-105 S2 / D-5: pushed UMRS job events at the round boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion import job_watch as jw
from r4.companion.agent import ResearchCompanion as CompanionAgent


class FakeFeed:
    """``client.jobs.events`` over an in-memory notifications table."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.reads = 0
        self.fail = False

    def add(self, kind: str, **detail) -> None:
        self.rows.append(
            {"id": len(self.rows) + 1, "kind": kind, "title": f"{kind} {detail}", "detail": detail}
        )

    def events(self, *, after=0, limit=50, tail=False, session_id=None):
        self.reads += 1
        if self.fail:
            raise RuntimeError("404 job not found")
        if tail:
            return {"events": [], "cursor": len(self.rows)}
        page = [r for r in self.rows if r["id"] > after][:limit]
        return {"events": page, "cursor": page[-1]["id"] if page else after}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def feed():
    return FakeFeed()


@pytest.fixture
def client(feed):
    c = SimpleNamespace(jobs=SimpleNamespace(events=feed.events), companion_session_id="")
    c.workflows = SimpleNamespace(status=MagicMock(side_effect=KeyError("unknown")))
    c.jobs.get = MagicMock(side_effect=KeyError("unknown"))
    return c


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def watch(client, clock):
    return jw.JobWatch(client, clock=clock, min_interval=5.0)


def _submit(watch, feed, execution_id="wf-1"):
    feed.add("job.succeeded", job_id="job-000000aaaaaa", execution_id="wf-old")  # history
    watch.observe("workflow_run", json.dumps({"execution_id": execution_id, "status": "submitted"}))


def test_non_submitting_tools_are_ignored(watch, feed):
    watch.observe("job_status", json.dumps({"job_id": "job-abcdef123456"}))
    assert watch.watching() == set() and feed.reads == 0


def test_submission_starts_the_cursor_at_now(watch, feed, clock):
    _submit(watch, feed)
    assert watch.watching() == {"wf-1"}
    clock.now = 10
    assert watch.drain() == []  # the older event is history, not news


def test_an_ended_run_becomes_one_notification(watch, feed, clock):
    _submit(watch, feed)
    feed.add(
        "job.succeeded",
        job_id="job-111111bbbbbb",
        execution_id="wf-1",
        job_type="workflow_run",
        status="succeeded",
    )
    clock.now = 10
    (note,) = watch.drain()
    assert "<execution-id>wf-1</execution-id>" in note and "<status>succeeded</status>" in note
    assert note.endswith(jw.NOTE_FOOTER)
    assert watch.watching() == set()


def test_other_jobs_are_not_announced(watch, feed, clock):
    _submit(watch, feed)
    feed.add("job.failed", job_id="job-222222cccccc", execution_id="wf-other")
    clock.now = 10
    assert watch.drain() == []


def test_the_sessions_own_stamped_jobs_are_announced(watch, feed, clock, client):
    client.companion_session_id = "sess-1"
    _submit(watch, feed)
    feed.add("job.failed", job_id="job-333333dddddd", session_id="sess-1")
    clock.now = 10
    assert len(watch.drain()) == 1


def test_reads_are_rate_limited(watch, feed, clock):
    _submit(watch, feed)
    reads = feed.reads
    clock.now = 10
    watch.drain()
    watch.drain()
    assert feed.reads == reads + 1


def test_an_older_umrs_goes_quiet_without_breaking_rounds(watch, feed, clock):
    feed.fail = True
    _submit(watch, feed)
    for step in range(5):
        clock.now = 10 * (step + 1)
        assert watch.drain() == []
    assert feed.reads <= jw._MAX_FAILURES


def test_hpc_submit_text_names_the_job():
    text = (
        "Submitted via UMRS durable job job-0123456789ab (hpc_script). "
        "Track with hpc_job_status job-0123456789ab."
    )
    assert jw.extract_ids(text) == {"job-0123456789ab"}


def test_wait_any_returns_the_first_end_and_keeps_the_rest(watch, feed, clock):
    _submit(watch, feed)
    watch.observe("hpc_submit", "durable job job-444444eeeeee")
    feed.add("job.succeeded", job_id="job-444444eeeeee")
    feed.add("job.failed", execution_id="wf-1")
    found = watch.wait_any({"wf-1"}, 30, sleep=clock.sleep)
    assert [e["detail"].get("execution_id") for e in found] == ["wf-1"]
    clock.now += 10
    assert len(watch.drain()) == 1  # the hpc job's end was buffered, not lost


def test_wait_any_sees_a_job_that_ended_before_it_was_watched(client, clock):
    client.jobs.get = MagicMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="completed"))
    )
    watch = jw.JobWatch(client, clock=clock)
    (event,) = watch.wait_any({"job-555555ffffff"}, 30, sleep=clock.sleep)
    assert event["detail"] == {"job_id": "job-555555ffffff", "status": "completed"}


def test_wait_any_times_out(watch, clock):
    assert watch.wait_any({"wf-9"}, 10, sleep=clock.sleep) == []
    assert clock.now >= 10


def test_wait_tool_answers_timeout_and_refuses_empty_handles(client, monkeypatch):
    monkeypatch.setattr(jw.time, "sleep", lambda s: None)
    monkeypatch.setattr(jw, "MAX_WAIT_SEC", 0)
    (tool,) = jw.build_job_watch_tools(client)
    assert json.loads(tool["execute"](handles=[]))["status"] == "error"
    assert json.loads(tool["execute"](handles=["wf-9"], timeout_sec=1))["status"] == "timeout"


def test_a_mock_client_gets_a_real_watch():
    assert isinstance(jw.job_watch_for(MagicMock()), jw.JobWatch)


def test_the_agent_drains_job_notes_with_subagent_notes(client, feed):
    watch = jw.job_watch_for(client)
    watch._min_interval = 0
    _submit(watch, feed)
    feed.add("job.succeeded", execution_id="wf-1")
    agent = SimpleNamespace(
        client=client, _async_pool=SimpleNamespace(drain=lambda: ["<subagent-notification/>"])
    )
    agent._session_job_watch = lambda: CompanionAgent._session_job_watch(agent)
    agent._drain_job_notes = lambda: CompanionAgent._drain_job_notes(agent)
    messages = CompanionAgent._drain_async_notifications(agent, [])
    (msg,) = messages
    assert "<subagent-notification/>" in msg["content"] and "<job-notification>" in msg["content"]


def test_the_agent_observes_submitting_tools(client):
    agent = SimpleNamespace(client=client)
    agent._session_job_watch = lambda: CompanionAgent._session_job_watch(agent)
    CompanionAgent._observe_submission(agent, "workflow_run", json.dumps({"execution_id": "wf-7"}))
    assert jw.job_watch_for(client).watching() == {"wf-7"}


# ── review fixes (ADR-105 S2) ───────────────────────────────────────────────


def test_a_fast_job_ending_before_observe_is_not_lost(watch, feed, clock):
    """R1: the cursor is taken before the submit runs."""
    watch.prime()
    feed.add("job.succeeded", execution_id="wf-fast")  # ended within the submit call
    watch.observe("workflow_run", json.dumps({"execution_id": "wf-fast", "status": "submitted"}))
    clock.now = 10
    assert len(watch.drain()) == 1


def test_a_late_cursor_asks_the_status_once(client, clock):
    """R1/R2: observe without prime (cursor taken late) checks the status."""
    client.workflows.status = MagicMock(return_value=SimpleNamespace(status="succeeded"))
    watch = jw.JobWatch(client, clock=clock)
    watch.observe("workflow_run", json.dumps({"execution_id": "wf-2", "status": "submitted"}))
    assert len(watch.drain()) == 1


def test_a_failed_first_tail_read_does_not_silence_the_drain(watch, feed, clock):
    """R2: drain polls while something is watched, and retries the cursor."""
    feed.fail = True
    watch.observe("workflow_run", json.dumps({"execution_id": "wf-3", "status": "submitted"}))
    feed.fail = False
    clock.now = 10
    reads = feed.reads
    watch.drain()
    assert feed.reads > reads and watch._cursor is not None


def test_sub_agents_never_drain_the_parents_notices(client, feed):
    """R3: only the top-level companion observes and drains."""
    sub = SimpleNamespace(client=client, _call_type="subagent")
    sub._session_job_watch = lambda: CompanionAgent._session_job_watch(sub)
    CompanionAgent._observe_submission(sub, "workflow_run", json.dumps({"execution_id": "wf-4"}))
    assert jw.job_watch_for(client).watching() == set()
    assert CompanionAgent._drain_job_notes(sub) == []


def test_concurrent_polls_deliver_an_end_once(watch, feed, clock):
    """R3: polls are serialised; delivery is idempotent."""
    import threading

    _submit(watch, feed)
    feed.add("job.succeeded", execution_id="wf-1")
    threads = [threading.Thread(target=watch._poll) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(watch._take_pending()) == 1


def test_a_cancelled_turn_ends_the_wait(watch):
    """R4: the turn's cancel wakes the wait at once."""
    from r4.companion.cancellation import TurnCancellation

    token = TurnCancellation()
    token.request()
    watch.cancel = token
    assert watch.wait_any({"wf-9"}, 300) == []
    out = json.loads(jw._wait_tool_result(watch, [], {"wf-9"}, 300))
    assert out["status"] == "cancelled"


def test_site_tagged_handles_match(watch, feed, clock):
    """R5: workflow_run answers "handle": "<exec>@<site>"."""
    _submit(watch, feed, execution_id="exec-9")
    feed.add("job.succeeded", execution_id="exec-9")
    found = watch.wait_any({"exec-9@rccs"}, 30, sleep=clock.sleep)
    assert [e["detail"]["execution_id"] for e in found] == ["exec-9"]


def test_the_feed_backs_off_and_retries(watch, feed, clock):
    """R6: no permanent silence; the tool says the feed is unavailable."""
    feed.fail = True
    _submit(watch, feed)
    for step in range(4):
        clock.now = 10 * (step + 1)
        watch.drain()
    assert not watch.available()
    out = json.loads(jw._wait_tool_result(watch, [], {"wf-1"}, 10))
    assert out["status"] == "events_unavailable"
    clock.now += jw._BACKOFF_SEC + 1
    assert watch.available()


def test_nothing_watched_means_no_reads(watch, feed, clock, client):
    """R7: a live session id alone never polls."""
    client.companion_session_id = "sess-1"
    watch.prime()
    reads = feed.reads
    clock.now = 100
    watch.drain()
    assert feed.reads == reads


def test_no_duplicate_after_an_already_ended_answer(client, feed, clock):
    """R8: a synthetic end and the real row are one notice."""
    client.companion_session_id = "sess-1"
    client.jobs.get = MagicMock(return_value=SimpleNamespace(status="completed"))
    watch = jw.JobWatch(client, clock=clock)
    watch.prime()
    feed.add("job.succeeded", job_id="job-777777aaaaaa", session_id="sess-1")
    assert len(watch.wait_any({"job-777777aaaaaa"}, 30, sleep=clock.sleep)) == 1
    client.jobs.get = MagicMock(side_effect=KeyError("unknown"))
    watch.observe("hpc_submit", "durable job job-888888bbbbbb")
    clock.now += 10
    assert watch.drain() == []


def test_a_finished_wait_true_run_is_not_watched(watch):
    """R8: workflow_run(wait=True) already reports the end."""
    watch.observe("workflow_run", json.dumps({"execution_id": "wf-5", "status": "succeeded"}))
    assert watch.watching() == set()


def test_several_pages_are_read_per_poll(watch, feed, clock):
    """R10: other sessions' events do not delay this one by many rounds."""
    _submit(watch, feed)
    for i in range(jw._PAGE + 5):
        feed.add("job.succeeded", job_id=f"job-{i:012x}")
    feed.add("job.succeeded", execution_id="wf-1")
    clock.now = 10
    assert len(watch.drain()) == 1
