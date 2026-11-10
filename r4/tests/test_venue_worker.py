"""Frontend-venue worker loop + rail client (wave 2026-09-21, G-RS-13/18)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from r4.errors import R4Error
from r4.venue_rail import VenueRailClient, VenueRailError
from r4.venue_worker import (
    EXIT_ISOLATION_FAILED,
    EXIT_LEASE_REFUSED,
    VenueWorker,
    WorkerEnv,
    isolation_selftest,
)

# ── env ────────────────────────────────────────────────────────────────────


class TestWorkerEnv:
    def test_from_environ_requires_identity_and_rail(self):
        with pytest.raises(R4Error) as exc:
            WorkerEnv.from_environ({"R4_VENUE_SESSION_ID": "s1"})
        assert "R4_VENUE_RAIL_URL" in str(exc.value)

    def test_from_environ_defaults(self):
        env = WorkerEnv.from_environ(
            {
                "R4_VENUE_SESSION_ID": "s1",
                "R4_VENUE_RAIL_URL": "http://egress:8081",
                "R4_VENUE_SESSION_TOKEN_FILE": "/run/secrets/session_token",
                "R4_VENUE_POLL_SEC": "0.5",
                "R4_VENUE_SKIP_SELFTEST": "1",
            }
        )
        assert env.endpoint_url == "http://egress:8080"
        assert env.poll_sec == 0.5 and env.skip_selftest is True
        assert env.heartbeat_sec == 600.0


# ── D-7 self-test ──────────────────────────────────────────────────────────


class TestSelfTest:
    def test_isolated_sandbox_passes(self):
        def connect(*_a, **_k):
            raise OSError("no route")

        def resolve(host, *_a, **_k):
            if host == "egress":
                return [("egress", 8080)]
            raise OSError("nxdomain")

        assert isolation_selftest(connect=connect, resolve=resolve) is None

    def test_direct_egress_fails_the_test(self):
        conn = SimpleNamespace(close=lambda: None)
        assert "direct tcp" in isolation_selftest(
            connect=lambda *_a, **_k: conn, resolve=lambda *_a, **_k: []
        )

    def test_external_dns_fails_the_test(self):
        def connect(*_a, **_k):
            raise OSError()

        assert "external DNS" in isolation_selftest(connect=connect, resolve=lambda *_a, **_k: [])

    def test_missing_egress_alias_fails_the_test(self):
        def connect(*_a, **_k):
            raise OSError()

        def resolve(*_a, **_k):
            raise OSError()

        assert "egress does not resolve" in isolation_selftest(connect=connect, resolve=resolve)


# ── rail client over a mock transport ──────────────────────────────────────


def _rail(handler):
    return VenueRailClient(
        "http://egress:8081", "sess-1", "tok-1", transport=httpx.MockTransport(handler)
    )


class TestRailClient:
    def test_bearer_and_path_anchor(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            seen["path"] = request.url.path
            return httpx.Response(200, json={"session_id": "sess-1", "payload": "{}"})

        assert _rail(handler).get_snapshot()["session_id"] == "sess-1"
        assert seen["auth"] == "Bearer tok-1"
        assert seen["path"] == "/umrs/companion/sessions/sess-1/rail/snapshot"

    def test_pop_queue_shapes(self):
        answers = iter(
            [
                {"applied": True, "front": {"id": "q-1", "content": "hi"}, "queue": []},
                {"applied": True, "front": None, "queue": []},
            ]
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=next(answers))

        rail = _rail(handler)
        assert rail.pop_queue()["content"] == "hi"
        assert rail.pop_queue() is None

    def test_named_refusal_carries_status_and_code(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"detail": "lease_revoked"},
                headers={"x-platform-error-code": "companionvenue.unauthorized"},
            )

        with pytest.raises(VenueRailError) as exc:
            _rail(handler).heartbeat(phase="idle")
        assert exc.value.status == 401
        assert exc.value.reason == "lease_revoked"
        assert exc.value.code == "companionvenue.unauthorized"
        assert "tok-1" not in str(exc.value)

    def test_unreachable_is_status_zero(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with pytest.raises(VenueRailError) as exc:
            _rail(handler).pop_queue()
        assert exc.value.status == 0


# ── platform client routed through the rail ───────────────────────────────


class TestRailPlatformClient:
    def test_urls_land_on_the_rail_gw_prefix_with_bearer_token(self):
        from r4.venue_rail import build_rail_platform_client, rail_gateway_base

        assert (
            rail_gateway_base("http://egress:8081/", "sess-1")
            == "http://egress:8081/umrs/companion/sessions/sess-1/rail/gw"
        )
        client = build_rail_platform_client(
            rail_url="http://egress:8081",
            session_id="sess-1",
            token="tok-1",
            endpoint_url="http://egress:8080",
            model="glm-5.3-flash",
        )
        assert client._api_prefix == ""
        assert client.workflows._wf_url("/x") == (
            "http://egress:8081/umrs/companion/sessions/sess-1/rail/gw/workflows/x"
        )
        assert client.http.build_headers()["Authorization"] == "Bearer tok-1"
        # inference stays on the sidecar proxy, never on the gw prefix
        from r4.model_routing import resolve_model_route

        route = resolve_model_route(client.config, "glm-5.3-flash")
        assert route.kind == "public"
        assert route.base_url.startswith("http://egress:8080")


# ── worker loop with fakes ─────────────────────────────────────────────────


class FakeRail:
    def __init__(self, queue: list[str] | None = None):
        self.queue = list(queue or [])
        self.heartbeats: list[dict] = []
        self.snapshots: list[dict] = []
        self.approvals: dict[str, dict] = {}
        self.approval_script: list[str] = []  # statuses returned by get_approval, in order
        self.stop_after_heartbeats: int | None = None
        self.refuse: VenueRailError | None = None
        # ADR-099 D-8: the owner's agent settings (rev 0 = none sent).
        self.settings: dict = {"rev": 0, "settings": {}}
        self.bus_calls: list[tuple[str, str, dict | None]] = []
        self.bus_inbox: list[dict] = []

    def get_settings(self):
        return {"session_id": "sess-1", **self.settings}

    def get_snapshot(self):
        return {"session_id": "sess-1", "payload": json.dumps({"id": "sess-1"})}

    def put_snapshot(self, **body):
        self.snapshots.append(body)
        return {"status": "ok", "applied": True}

    def pop_queue(self):
        if self.refuse:
            raise self.refuse
        if not self.queue:
            return None
        item = self.queue.pop(0)
        if isinstance(item, dict):
            return item
        return {"id": "q", "content": item, "client_id": "tui", "added_at": "t"}

    def session_bus_request(self, method, tail, json=None):
        if self.refuse:
            raise self.refuse
        self.bus_calls.append((method, tail, json))
        if tail == "presence":
            return {"sessions": []}
        if tail == "inbox":
            messages = list(self.bus_inbox)
            if isinstance(json, dict) and json.get("action") == "take":
                self.bus_inbox = []
            return {"messages": messages}
        return {
            "bound": method != "DELETE",
            "session_id": "sess-1",
            "bus_session_id": "sess-1",
            "venue": "frontend",
        }

    def heartbeat(self, **body):
        if self.refuse:
            raise self.refuse
        self.heartbeats.append(body)
        stop = (
            self.stop_after_heartbeats is not None
            and len(self.heartbeats) >= self.stop_after_heartbeats
        )
        return {"ok": True, "renewed": True, "stop_requested": stop}

    def request_approval(self, *, turn_id, action, approval_id=None):
        record = {
            "approval_id": "appr-1",
            "turn_id": turn_id,
            "action": action,
            "status": "pending",
        }
        self.approvals["appr-1"] = record
        return record

    def get_approval(self, approval_id):
        status = self.approval_script.pop(0) if self.approval_script else "pending"
        record = dict(self.approvals[approval_id])
        if status != "pending":
            record["status"] = "approved" if status in ("once", "always") else "rejected"
            record["decision"] = status
        return record


class FakePermissions:
    def __init__(self):
        self.callback = None
        self.session_approvals: list[str] = []

    def set_approval_callback(self, cb):
        self.callback = cb

    def approve_for_session(self, tool_name):
        self.session_approvals.append(tool_name)


class FakeAgent:
    def __init__(self):
        self.model = "gpt-x@openai-main"
        self.listeners = []

    def on_event(self, cb):
        self.listeners.append(cb)


class FakeSession:
    def __init__(self, *, fail=False):
        self.session_id = "sess-1"
        self.metadata = {"turn_seq": 0}
        self.messages: list = []
        self.permissions = FakePermissions()
        self.agent = FakeAgent()
        self.client = None
        self.sent: list[str] = []
        self._fail = fail
        self._on_turn_started_hook = None
        self._current_turn_id = ""
        self.bus_presence = None

    def send(self, text, *, stream=False):
        del stream
        self.metadata["turn_seq"] = int(self.metadata["turn_seq"]) + 1
        self._current_turn_id = f"t{self.metadata['turn_seq']}"
        hook = self._on_turn_started_hook
        if hook:
            hook(self._current_turn_id)
        self.sent.append(text)
        if self._fail:
            raise RuntimeError("upstream exploded")
        # stream a couple of chunks through the agent listeners
        for listener in self.agent.listeners:
            listener(
                SimpleNamespace(event_type=SimpleNamespace(value="response_start"), message="")
            )
            listener(
                SimpleNamespace(event_type=SimpleNamespace(value="response_chunk"), message="hel")
            )
            listener(
                SimpleNamespace(event_type=SimpleNamespace(value="response_chunk"), message="lo")
            )
        self.messages.append({"role": "assistant", "content": "hello"})
        return "hello"


class FakeStore:
    def __init__(self):
        self.saved = 0
        self.poster = None

    def set_mirror_poster(self, poster, _client):
        self.poster = poster

    def save(self, session):
        self.saved += 1
        if self.poster:
            self.poster(
                "/ignored",
                json={
                    "name": "S",
                    "updated_at": "now",
                    "payload": json.dumps({"id": session.session_id}),
                    "state": "ongoing",
                },
            )


def _env(**over):
    base = {
        "session_id": "sess-1",
        "rail_url": "http://egress:8081",
        "token_file": "/run/secrets/session_token",
        "poll_sec": 0,
        "heartbeat_sec": 1000,
        "approval_timeout_sec": 30,
        "skip_selftest": True,
    }
    base.update(over)
    return WorkerEnv(**base)


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))


def _worker(rail: FakeRail, session: FakeSession, **env_over):
    clock = _Clock()
    store = FakeStore()
    worker = VenueWorker(
        _env(**env_over),
        rail,
        token="tok-1",
        store=store,
        session_loader=lambda: session,
        clock=clock,
        sleep=clock.sleep,
    )
    return worker, store, clock


def _partials(heartbeats: list[dict], phase: str) -> list[str]:
    """Partial texts reported while in ``phase`` (helper keeps the pin simple)."""
    out: list[str] = []
    for hb in heartbeats:
        if hb["phase"] == phase:
            out.append(hb["partial"])
    return out


class TestWorkerLoop:
    def test_run_once_pops_runs_and_mirrors(self):
        rail = FakeRail(["what is 2+2?"])
        session = FakeSession()
        worker, store, _ = _worker(rail, session)
        worker.bootstrap()
        assert worker.run_once() is True
        assert session.sent == ["what is 2+2?"]
        # user row mirrored at turn start + final mirror after the turn
        assert store.saved == 2
        assert len(rail.snapshots) == 2
        assert worker.run_once() is False  # queue drained

    def test_post_turn_save_outranks_the_turn_start_copy(self):
        """Review 2026-09-25: turn start and post-turn saves shared one marker
        and the journal applies ties — a stale turn-start copy could replace
        the reply. The post-turn save carries a strictly newer marker, in the
        session's own offset, also when the turn failed."""
        from datetime import datetime

        start = "2026-09-24T10:00:00.000001+05:45"
        for fail in (False, True):
            rail = FakeRail(["q"])
            session = FakeSession(fail=fail)
            session.updated_at = start
            worker, _, _ = _worker(rail, session)
            worker.bootstrap()
            worker.run_once()
            assert datetime.fromisoformat(session.updated_at) > datetime.fromisoformat(start)
            assert session.updated_at.endswith("+05:45")

    def test_run_once_reports_phases_and_partials(self):
        rail = FakeRail(["what is 2+2?"])
        worker, _, _ = _worker(rail, FakeSession())
        worker.bootstrap()
        worker.run_once()
        phases = [hb["phase"] for hb in rail.heartbeats]
        assert "running" in phases
        assert phases[-1] == "idle"
        assert rail.heartbeats[-1]["turn_seq"] == 1
        assert _partials(rail.heartbeats, "running")[-1].startswith("hel")
        assert rail.heartbeats[-1]["partial"] == ""

    def test_turn_failure_is_reported_not_fatal(self):
        rail = FakeRail(["boom"])
        session = FakeSession(fail=True)
        worker, store, _ = _worker(rail, session)
        worker.bootstrap()
        assert worker.run_once() is True
        assert rail.heartbeats[-1]["phase"] == "error"
        assert "upstream exploded" in rail.heartbeats[-1]["detail"]
        assert store.saved >= 1

    def test_run_exits_when_server_requests_stop(self):
        rail = FakeRail()
        rail.stop_after_heartbeats = 1
        worker, _, _ = _worker(rail, FakeSession())
        assert worker.run() == 0

    def test_run_exits_3_when_lease_refused(self):
        rail = FakeRail()
        rail.refuse = VenueRailError(401, "lease_revoked")
        worker, _, _ = _worker(rail, FakeSession())
        assert worker.run() == EXIT_LEASE_REFUSED

    def test_bootstrap_failure_reports_exit_reason(self):
        rail = FakeRail()

        def boom():
            raise RuntimeError("no such store")

        worker = VenueWorker(_env(), rail, token="tok-1", store=FakeStore(), session_loader=boom)
        assert worker.run() == 4
        assert rail.heartbeats[-1]["phase"] == "exited"
        assert "RuntimeError: no such store" in rail.heartbeats[-1]["detail"]

    def test_run_refuses_unisolated_sandbox(self, monkeypatch):
        monkeypatch.setattr("r4.venue_worker.isolation_selftest", lambda: "direct tcp succeeded")
        worker, _, _ = _worker(FakeRail(), FakeSession(), skip_selftest=False)
        assert worker.run() == EXIT_ISOLATION_FAILED

    def test_bare_model_and_venue_pinned_on_bootstrap(self):
        session = FakeSession()
        worker, _, _ = _worker(FakeRail(), session)
        worker.bootstrap()
        assert session.permissions.callback is not None
        assert session._on_turn_started_hook is not None


class TestApprovals:
    def test_pending_then_always_grants_session_scope(self):
        rail = FakeRail()
        rail.approval_script = ["pending", "always"]
        session = FakeSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        assert session.permissions.callback("shell_exec", "ls", {"cmd": "ls"}) is True
        assert session.permissions.session_approvals == ["shell_exec"]
        assert any(hb["phase"] == "awaiting_approval" for hb in rail.heartbeats)

    def test_ordinary_intent_is_not_fenced(self):
        rail = FakeRail()
        rail.approval_script = ["once"]
        session = FakeSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        assert session.permissions.callback("shell_exec", "ls", {"cmd": "ls"}) is True
        action = rail.approvals["appr-1"]["action"]
        assert action["fenced"] is False
        assert action["fence_reason"] == ""

    def test_fenced_intent_carries_the_fence(self):
        # Fix 2026-10-30: the owner's card and bridge read the fence from the
        # durable intent; the callback runs inside the fence context.
        from r4.companion.permissions import _APPROVAL_FENCE

        rail = FakeRail()
        rail.approval_script = ["once"]
        session = FakeSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        token = _APPROVAL_FENCE.set("taint fence")
        try:
            assert session.permissions.callback("shell_exec", "curl", {"cmd": "curl"}) is True
        finally:
            _APPROVAL_FENCE.reset(token)
        action = rail.approvals["appr-1"]["action"]
        assert action["fenced"] is True
        assert action["fence_reason"] == "taint fence"

    def test_reject(self):
        rail = FakeRail()
        rail.approval_script = ["reject"]
        session = FakeSession()
        worker, _, _ = _worker(rail, session)
        worker.bootstrap()
        assert session.permissions.callback("shell_exec", "rm", {}) is False

    def test_timeout_denies(self):
        rail = FakeRail()  # always pending
        session = FakeSession()
        worker, _, clock = _worker(rail, session, approval_timeout_sec=5)
        worker.bootstrap()
        assert session.permissions.callback("shell_exec", "rm", {}) is False
        assert clock.t > 100.0
        assert "timed out" in rail.heartbeats[-1]["detail"]

    def test_ask_user_auto_only_under_the_users_auto_setting(self):
        # ADR-099 D-8: the remote auto-answer is now the user's own choice
        # (ask_user_mode=auto); the durable-question path is pinned in
        # test_venue_parity_worker.py.
        rail = FakeRail()
        rail.settings = {"rev": 1, "settings": {"ask_user_mode": "auto"}}
        worker, _, _ = _worker(rail, FakeSession())
        worker.bootstrap()
        assert "ask_user_mode=auto" in worker._ask_user("which?", ["a", "b"])


def test_rail_error_surfaces_the_typed_reason():
    """UMRS typed errors put the reason in "message"; the worker printed only
    "http 401" for the live ip_mismatch refusal (2026-09-25)."""
    import httpx

    from r4.venue_rail import _rail_error

    resp = httpx.Response(
        401,
        json={"error_id": "auth.unauthorized", "message": "ip_mismatch", "request_id": "r"},
    )
    err = _rail_error(resp)
    assert err.status == 401
    assert err.reason == "ip_mismatch"


class TestToolActivity:
    """Worker live tool-activity lines (TUI visibility, 2026-09-27)."""

    def _ev(self, event_type: str, data: dict | None = None):
        from r4.companion.agent import CompanionEvent, CompanionEventType

        return CompanionEvent(
            event_type=CompanionEventType(event_type),
            message=("x" if event_type == "response_chunk" else ""),
            data=data or {},
        )

    def test_tool_call_appends_bounded_line_and_result_marks_it(self):
        rail = FakeRail()
        worker, _store, _clock = _worker(rail, FakeSession())
        worker._on_event(
            self._ev("tool_call", {"name": "hpc_submit", "arguments": "sbatch long-args-here"})
        )
        worker._on_event(self._ev("tool_result", {"status": "ok", "duration_ms": 1500}))
        assert worker._tool_activity == ["▸ hpc_submit sbatch long-args-here ✓ 1.5s"]

    def test_args_are_clipped_to_one_line(self):
        rail = FakeRail()
        worker, _store, _clock = _worker(rail, FakeSession())
        worker._on_event(self._ev("tool_call", {"name": "t", "arguments": "x " * 300}))
        line = worker._tool_activity[0]
        assert "\n" not in line
        assert line.endswith("…")
        assert len(line) <= 80

    def test_activity_resets_at_response_start_and_rides_heartbeats(self):
        rail = FakeRail()
        worker, _store, _clock = _worker(rail, FakeSession())
        worker._on_event(self._ev("tool_call", {"name": "old_tool"}))
        worker._on_event(self._ev("response_start"))
        assert worker._tool_activity == []
        worker._on_event(self._ev("tool_call", {"name": "fresh_tool", "arguments": "a"}))
        worker._heartbeat("running", detail="streaming")
        assert rail.heartbeats[-1]["tool_activity"] == ["▸ fresh_tool a"]

    def test_cap_keeps_last_12(self):
        rail = FakeRail()
        worker, _store, _clock = _worker(rail, FakeSession())
        for i in range(20):
            worker._on_event(self._ev("tool_call", {"name": f"tool{i}"}))
        assert len(worker._tool_activity) == 12
        assert worker._tool_activity[0].startswith("▸ tool8")
        assert worker._tool_activity[-1].startswith("▸ tool19")


class TestWorkerSessionMessages:
    def test_worker_rejects_local_durable_identity_mismatch_before_binding(self):
        rail = FakeRail()
        session = FakeSession()
        session.session_id = "sess-parent-fallback"
        worker, _store, _clock = _worker(rail, session)
        with pytest.raises(R4Error, match="ids do not match"):
            worker.bootstrap()
        assert rail.bus_calls == [] and session.sent == []

    def test_message_body_never_becomes_the_wake_user_turn(self):
        from r4.companion.session_bus_remote import SESSION_MESSAGE_WAKE

        rail = FakeRail(
            [
                {
                    "id": "msg-aaaaaaaaaaaa",
                    "kind": "session_message",
                    "content": "SENDER-CONTROLLED-WAKE-TEXT",
                    "sender_id": "sess-sender01",
                    "sender_name": "sender",
                    "recipient_id": "sess-1",
                    "added_at": "2026-11-10T00:00:00+00:00",
                }
            ]
        )
        session = FakeSession()
        worker, _store, _clock = _worker(rail, session)
        worker.bootstrap()
        assert worker.run_once() is True
        assert session.sent == [SESSION_MESSAGE_WAKE]
        assert "SENDER-CONTROLLED-WAKE-TEXT" not in session.sent[0]
        assert session.bus_presence is not None
        assert any(call[0] == "PUT" for call in rail.bus_calls)

    def test_stop_before_pop_cancels_without_touching_the_message(self, monkeypatch):
        monkeypatch.setenv("R4_SESSION_BUS", "0")
        rail = FakeRail(
            [
                {
                    "id": "msg-aaaaaaaaaaaa",
                    "kind": "session_message",
                    "content": "MUST-STAY-QUEUED",
                }
            ]
        )
        rail.stop_after_heartbeats = 1
        session = FakeSession()
        worker, _store, _clock = _worker(rail, session, heartbeat_sec=0)
        worker.bootstrap()
        assert worker.run_once() is False
        assert session.sent == [] and len(rail.queue) == 1
        assert rail.bus_calls == [] and session.bus_presence is None

    def test_queue_outage_is_not_reclassified_as_a_valid_message(self):
        rail = FakeRail()
        rail.refuse = VenueRailError(0, "rail unreachable")
        session = FakeSession()
        worker, _store, _clock = _worker(rail, session)
        with pytest.raises(VenueRailError):
            worker.bootstrap()
        assert session.sent == []
