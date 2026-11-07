"""A team plan run on the server venue (ADR-092 slice 8, D-29, D-63).

UMRS hosts the run in a sandboxed frontend-venue worker that runs the r4
plan engine headless (``r4 venue worker --plan-run <id>``); its record is
slice 7's, read and written through the rail; its gates are intents in the
UMRS approvals inbox that only the run's owner answers. These tests run the
real worker, ``TeamService`` and plan engine on scripted turns
(``MemoryAgentBroker``) against ``VenueUmrs``: UMRS's plan-run routes and
venue verbs in memory, reached through the real HTTP adapter's URLs. No
live container, no live UMRS, no model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_teamengine_spi.testing import MemoryAgentBroker
from test_plan_durable_records import SCRIPT, FakeUmrs, _plan

from r4.companion.web_access import WebAccess, make_web_access
from r4.errors import APIError
from r4.teams import plan_worker, server_venue
from r4.teams import service as team_service
from r4.teams.plan import spec_from_plan
from r4.teams.service import TeamService
from r4.venue_rail import VenueRailClient

RAIL_BASE = "http://egress:8081/umrs/companion/sessions/planrun-x/rail/gw"
GATEWAY = "https://gw.example"
_ROUTE = re.compile(r"/traces/plan-runs/(?P<run>[^/]+)(?P<tail>(/.*)?)$")


class VenueUmrs(FakeUmrs):
    """FakeUmrs plus what slice 8 adds: hosting, caps, the worker's outcome,
    the venue verbs, and the inbox's plan-gate intents."""

    def __init__(self) -> None:
        super().__init__()
        self.venue: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.gates: dict[str, dict[str, Any]] = {}
        self.on_status: Any = None  # the server working between two polls
        self.refuse_start: str | None = None

    def seed(self, run_id: str, definition: dict[str, Any], caps: dict[str, Any]) -> None:
        self.runs[run_id] = {
            "definition": json.loads(json.dumps(definition)),
            "required": True,
            "attempts": [],
            "active": None,
            "rows": [],
            "hosting": "venue",
            "caps": dict(caps),
            "status": "planned",
        }

    def run(self, run_id):
        view = super().run(run_id)
        run = self.runs[run_id]
        view.update(hosting=run.get("hosting", "client"), caps=run.get("caps") or {})
        view["status"] = run.get("status", "running")
        view["attempts"] = [
            {
                "label": a["label"],
                "status": a["status"],
                "plan_status": a.get("plan_status"),
                "summary": a.get("summary"),
            }
            for a in run["attempts"]
        ]
        return view

    def finish(self, run_id, attempt_id, *, status, error, summary):
        answer = super().finish(run_id, attempt_id, status=status, error=error, summary=summary)
        att = next(a for a in self.runs[run_id]["attempts"] if a["attempt_id"] == attempt_id)
        att["plan_status"] = status
        return answer

    # venue verbs (a user's client)
    def start_on_venue(self, run_id, body):
        if not body.get("source_refs"):
            raise APIError("400", status_code=400, response_body='{"detail": "source_refs"}')
        if body.get("web_policy") not in ("none", "allowlist", "public"):
            # ADR-099 D-2: UMRS refuses a start without the user's web choice
            # (UMRS_SESSION_WEB_CHOICE_REQUIRED=true, the default).
            raise APIError(
                "400",
                status_code=400,
                response_body='{"detail": "sessionpolicy.web_choice_required"}',
                error_code="sessionpolicy.web_choice_required",
            )
        if self.refuse_start:
            raise APIError(
                "503",
                status_code=503,
                response_body=json.dumps({"detail": self.refuse_start}),
                error_code="companionvenue.unavailable",
            )
        if run_id not in self.runs:
            self.seed(run_id, body["definition"], {})
        self.venue[run_id] = {"state": "provisioning", "body": body}
        return {"job_id": f"job-{run_id}", "created": True, "venue": {"state": "provisioning"}}

    def venue_status(self, run_id):
        if self.on_status is not None:
            hook, self.on_status = self.on_status, None
            hook()
        return {**self.run(run_id), "venue": self.venue.get(run_id, {})}

    def cancel(self, run_id):
        self.runs[run_id]["status"] = "cancelled"
        self.venue[run_id] = {"state": "stopped"}
        return {"cancelled": True, "job_status": "cancelled", "venue": {"state": "stopped"}}


class FakeHttp:
    """The client's ``http.request`` routed onto ``VenueUmrs`` by URL."""

    def __init__(self, umrs: VenueUmrs) -> None:
        self.umrs = umrs

    def request(self, method: str, url: str, *, json_body: Any = None, timeout: Any = None):
        del timeout
        self.umrs.calls.append((method, url))
        match = _ROUTE.search(url)
        assert match, url
        run_id, tail = match["run"], match["tail"]
        return self._dispatch(method, run_id, tail, json_body or {})

    def _dispatch(self, method, run_id, tail, body):
        u = self.umrs
        if tail == "/attempts":
            return u.open_attempt(
                run_id,
                attempt=body["attempt"],
                definition=body["definition"],
                required=body["required"],
            )
        if tail == "/records":
            if method == "POST":
                return u.append(run_id, body["attempt_id"], body["record"])
            return {"records": u.records(run_id)}
        if tail.endswith("/finish"):
            attempt_id = tail.split("/")[2]
            return u.finish(run_id, attempt_id, **body)
        if tail == "/venue":
            return u.start_on_venue(run_id, body) if method == "POST" else u.venue_status(run_id)
        if tail == "/cancel":
            return u.cancel(run_id)
        return u.run(run_id)


class FakeRail:
    """The worker's rail: heartbeats and plan-gate intents, nothing else."""

    def __init__(self, umrs: VenueUmrs, *, stop_after: int | None = None) -> None:
        self.umrs = umrs
        self.beats: list[tuple[str, str]] = []
        self.stop_after = stop_after
        self.gate_polls = 0
        self.decide_on_poll: tuple[int, str] | None = None

    def heartbeat(self, *, phase, detail="", **_kw):
        self.beats.append((phase, detail))
        stop = self.stop_after is not None and len(self.beats) > self.stop_after
        return {"ok": True, "stop_requested": stop}

    def request_plan_gate(self, *, gate, trigger_class, prompt):
        approval_id = f"pgate-{len(self.umrs.gates) + 1}"
        self.umrs.gates[approval_id] = {
            "gate": gate,
            "class": trigger_class,
            "prompt": prompt,
            "status": "pending",
        }
        return {"approval_id": approval_id, "status": "pending"}

    def get_plan_gate(self, approval_id):
        self.gate_polls += 1
        if self.decide_on_poll and self.gate_polls >= self.decide_on_poll[0]:
            decision = self.decide_on_poll[1]
            # the owner answered in the inbox, from another client
            self.umrs.gates[approval_id].update(
                status="approved" if decision == "once" else "rejected", decided_by="alice"
            )
        return dict(self.umrs.gates[approval_id], approval_id=approval_id)


def _client(umrs: VenueUmrs, *, base: str = GATEWAY, model: str = "m1") -> Any:
    approvals: list[tuple[str, str]] = []

    def list_loop_approvals(*, loop_id=None):
        rows = [
            {"id": k, "loop_id": loop_id, "action": v}
            for k, v in umrs.gates.items()
            if v["status"] == "pending"
        ]
        return {"pending": rows}

    def decide_loop_approval(approval_id, decision):
        approvals.append((approval_id, decision))
        umrs.gates[approval_id]["status"] = "approved" if decision == "once" else "rejected"
        return {"status": umrs.gates[approval_id]["status"]}

    return SimpleNamespace(
        config=SimpleNamespace(model=model, project_id="proj", gateway_url=base),
        http=FakeHttp(umrs),
        list_loop_approvals=list_loop_approvals,
        decide_loop_approval=decide_loop_approval,
        decided=approvals,
    )


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """Scripted turns; a plan gate goes through the real broker gate path."""
    monkeypatch.setattr(team_service, "TEAM_RUNS_DIR", tmp_path / "client-runs")
    state: dict[str, Any] = {"brokers": [], "script": dict(SCRIPT), "specs": []}

    def _broker(self, spec, run_id, meter):
        from r4.teams.agent_broker import RoleTurnAgentBroker

        broker = MemoryAgentBroker(dict(state["script"]))
        lane = RoleTurnAgentBroker(None, meter=meter, run_id=run_id, gate_lane=self._gate_lane)
        broker.ask_gate = lane.ask_gate  # the real gate path over the worker's lane
        state["brokers"].append(broker)
        state["specs"].append(spec)
        return broker

    monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker)
    return state


def _spec(plan: dict[str, Any] | None = None):
    spec, problems = spec_from_plan(plan or _plan(), {}, project="proj")
    assert not problems, problems
    return spec


def _definition(umrs: VenueUmrs, plan: dict[str, Any] | None = None) -> tuple[str, dict]:
    client_service = TeamService(_client(umrs), records=None)
    return client_service.hosted_definition(_spec(plan))


def _worker(umrs: VenueUmrs, root: Path, run_id: str, rail: FakeRail | None = None, **kw):
    return plan_worker.PlanRunWorker(
        run_id=run_id,
        rail=rail or FakeRail(umrs),
        client_factory=lambda model: _client(umrs, base=RAIL_BASE, model=model),
        work_root=root,
        progress_sec=0.01,
        sleep=lambda _s: None,
        **kw,
    )


def _gated() -> dict[str, Any]:
    return {
        "plan_version": 1,
        "name": "gated",
        "description": "an agent, a human's yes, an agent",
        "budget": {"max_tokens": 10_000},
        "phases": [
            {
                "id": "p",
                "title": "P",
                "steps": [
                    {"id": "a", "kind": "agent", "prompt": "first"},
                    {"id": "ask", "kind": "gate", "class": "cost", "prompt": "Go on?"},
                    {"id": "b", "kind": "agent", "prompt": "second"},
                ],
            }
        ],
        "output": {"result": "steps.b.output"},
    }


# ── the worker ─────────────────────────────────────────────────────────────


def test_the_worker_runs_the_plan_from_its_record_and_writes_its_records(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs)
    umrs.seed(run_id, definition, {"max_wall_clock_sec": 120})
    rail = FakeRail(umrs)
    code = _worker(umrs, tmp_path, run_id, rail).run()
    run = umrs.runs[run_id]
    (attempt,) = run["attempts"]
    worker_dir = tmp_path / "team_runs" / run_id
    plan_doc = json.loads((worker_dir / "plan.json").read_text())
    assert {
        "code": code,
        "attempt": (attempt["label"], attempt["status"], attempt["plan_status"]),
        "records": len(run["rows"]),
        "output": attempt["summary"]["output"]["result"],
        "wallclock": plan_doc["budget"]["max_wallclock_sec"],
        "root": world["specs"][0].context["project_root"],
        "last_beat": rail.beats[-1],
    } == {
        "code": 0,
        "attempt": (1, "success", "success"),
        "records": 8,
        "output": "done",
        "wallclock": 120,  # the project's ADR-067 cap, recorded by the server
        "root": str((tmp_path / "work").resolve()),
        "last_beat": ("done", "plan run success"),
    }
    # every record call rode the rail's gw prefix to this run's record only
    paths = {url for _m, url in umrs.calls}
    assert all(url.startswith(f"{RAIL_BASE}/traces/plan-runs/{run_id}") for url in paths)


def test_a_record_the_worker_cannot_write_stops_the_run_fail_closed(tmp_path, world):
    """The worker's record is required: never a local-only run in a sandbox
    that is torn down when it ends."""
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs)
    umrs.seed(run_id, definition, {})
    umrs.fail_after["append"] = 2
    rail = FakeRail(umrs)
    assert _worker(umrs, tmp_path, run_id, rail).run() == 1
    assert rail.beats[-1][0] == "done" and "durable_record_failed" in rail.beats[-1][1]


def test_the_cap_only_ever_lowers_the_plans_wall_clock():
    from r4.teams.service import _within_wallclock

    def wall(own, cap):
        budget = {"max_tokens": 1} if own is None else {"max_tokens": 1, "max_wallclock_sec": own}
        return _within_wallclock({"budget": budget}, cap)["budget"].get("max_wallclock_sec")

    assert (wall(600, 120), wall(60, 120), wall(None, 120), wall(600, None)) == (
        120,
        60,
        120,
        600,
    )


def test_a_second_worker_resumes_from_the_record_at_zero_tokens(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs)
    umrs.seed(run_id, definition, {})
    assert _worker(umrs, tmp_path / "w1", run_id).run() == 0
    assert _worker(umrs, tmp_path / "w2", run_id).run() == 0  # another sandbox
    second = umrs.runs[run_id]["attempts"][1]
    assert (second["label"], second["plan_status"]) == (2, "success")
    assert world["brokers"][1].calls == []  # every ok call replayed from UMRS
    assert second["summary"]["replayed"] == 8


def test_the_owners_stop_cancels_the_run_as_a_unit(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs, _gated())
    umrs.seed(run_id, definition, {})
    world["script"] = {"a": "x", "b": "y"}
    rail = FakeRail(umrs, stop_after=1)  # "running"; then the owner stops it
    code = _worker(umrs, tmp_path, run_id, rail).run()
    attempt = umrs.runs[run_id]["attempts"][0]
    assert (code, attempt["plan_status"]) == (1, "cancelled")
    rows = [json.loads(r["record"]) for r in umrs.runs[run_id]["rows"]]
    assert not [r for r in rows if r.get("kind") == "gate"]  # a refusal is not an answer
    assert rail.beats[-1][0] == "done"


def test_a_gate_goes_to_the_inbox_and_only_the_owners_answer_passes_it(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs, _gated())
    umrs.seed(run_id, definition, {})
    world["script"] = {"a": "x", "b": "y"}
    rail = FakeRail(umrs)
    rail.decide_on_poll = (3, "once")  # pending twice, then the owner says yes
    code = _worker(umrs, tmp_path, run_id, rail).run()
    ((approval_id, intent),) = umrs.gates.items()
    rows = [json.loads(r["record"]) for r in umrs.runs[run_id]["rows"]]
    (answer,) = [r for r in rows if r.get("kind") == "gate"]
    assert {
        "code": code,
        "intent": (intent["gate"], intent["class"], intent["decided_by"]),
        "polls": rail.gate_polls,
        "answer": (answer["ok"], answer["answered"], answer["decided_by"]),
        "waiting_beat": any(
            phase == "awaiting_approval" and approval_id in detail for phase, detail in rail.beats
        ),
    } == {
        "code": 0,
        "intent": ("ask", "cost", "alice"),
        "polls": 3,
        "answer": (True, True, "human"),
        "waiting_beat": True,
    }


def test_a_rejected_gate_stops_the_run(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs, _gated())
    umrs.seed(run_id, definition, {})
    world["script"] = {"a": "x", "b": "y"}
    rail = FakeRail(umrs)
    rail.decide_on_poll = (1, "no")
    assert _worker(umrs, tmp_path, run_id, rail).run() == 1
    assert umrs.runs[run_id]["attempts"][0]["plan_status"] == "cancelled"
    assert world["brokers"][0].calls[-1].label != "b"


def test_an_unanswered_gate_is_refused_never_answered(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs, _gated())
    umrs.seed(run_id, definition, {})
    world["script"] = {"a": "x", "b": "y"}
    rail = FakeRail(umrs)  # nobody decides
    code = _worker(umrs, tmp_path, run_id, rail, gate_timeout_sec=0.0).run()
    rows = [json.loads(r["record"]) for r in umrs.runs[run_id]["rows"]]
    assert code == 1
    assert not [r for r in rows if r.get("kind") == "gate"]
    assert umrs.gates["pgate-1"]["status"] == "pending"  # still the owner's to answer


def test_the_worker_has_no_way_to_decide_a_gate():
    verbs = {name for name in dir(VenueRailClient) if not name.startswith("_")}
    assert {"request_plan_gate", "get_plan_gate"} <= verbs
    assert not {v for v in verbs if "decide" in v or "decision" in v}
    lane = plan_worker.InboxGateLane(
        SimpleNamespace(
            request_plan_gate=lambda **_k: {"approval_id": "p1"},
            get_plan_gate=lambda _id: {"status": "pending"},
        ),
        stopped=iter([False, True]).__next__,
        heartbeat=lambda *_a, **_k: None,
        sleep=lambda _s: None,
    )
    with pytest.raises(plan_worker.GateUnanswered):
        lane.ask("team_plan_gate", "", {"gate": "g"})


def test_a_worker_refuses_a_run_that_is_not_venue_hosted(tmp_path, world):
    umrs = VenueUmrs()
    run_id, definition = _definition(umrs)
    umrs.seed(run_id, definition, {})
    umrs.runs[run_id]["hosting"] = "client"
    rail = FakeRail(umrs)
    assert _worker(umrs, tmp_path, run_id, rail).run() == plan_worker.EXIT_BOOTSTRAP_FAILED
    assert rail.beats[-1][0] == "exited" and "not hosted" in rail.beats[-1][1]
    assert umrs.runs[run_id]["attempts"] == []


def test_the_worker_holds_only_its_session_token(monkeypatch):
    """The sandbox's client: the rail's gw prefix and the sidecar, the
    session token as its only credential (no JWT, no provider key)."""
    from r4.venue_rail import build_rail_platform_client

    for name in ("R4_API_KEY", "R4_GATEWAY_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    client = build_rail_platform_client(
        rail_url="http://egress:8081",
        session_id="planrun-x",
        token="tok-123",
        endpoint_url="http://egress:8080",
        model="m1",
    )
    config = client.config
    assert (config.api_key, config.endpoint_api_key) == ("tok-123", "tok-123")
    assert config.gateway_url == ("http://egress:8081/umrs/companion/sessions/planrun-x/rail/gw")
    assert config.endpoint_url == "http://egress:8080"


# ── the client: --venue server ─────────────────────────────────────────────


def _serve(umrs: VenueUmrs, root: Path, run_id_box: list[str]) -> Any:
    """The server between two polls: its worker runs the run."""

    def _go():
        _worker(umrs, root, run_id_box[0]).run()

    return _go


def test_the_cli_starts_a_server_run_and_follows_it_to_its_result(tmp_path, world, capsys):
    from r4._cli import team_plan

    umrs = VenueUmrs()
    client = _client(umrs)
    box: list[str] = []
    original = umrs.start_on_venue

    def _start(run_id, body):
        box.append(run_id)
        umrs.on_status = _serve(umrs, tmp_path / "server", box)
        return original(run_id, body)

    umrs.start_on_venue = _start
    args = _run_args(tmp_path, venue="server", venue_sources="openai-main,azure-b")
    code = team_plan._cmd_run(args, client)
    out = capsys.readouterr().out
    run_id = box[0]
    body = umrs.venue[run_id]["body"]
    # its result, read by run id from UMRS
    result_code = team_plan._cmd_result(SimpleNamespace(run_id=run_id, path=None), client)
    result = json.loads(capsys.readouterr().out)
    printed = [f"run_id: {run_id}", "status:  success", "[agent] src ok"]
    assert {
        "code": (code, result_code),
        "body": (body["source_refs"], body["definition"]["plan"]["name"]),
        "printed": [line for line in printed if line not in out],
        "venue_call": ("POST", f"{GATEWAY}/gw/traces/plan-runs/{run_id}/venue") in umrs.calls,
        # nothing ran on the client: no local run directory, no client-side agent
        "local_run_dir": (tmp_path / "client-runs" / run_id).exists(),
        "result": (result["venue"], result["status"], result["output"]["result"]),
    } == {
        "code": (0, 0),
        "body": (["openai-main", "azure-b"], "durable"),
        "printed": [],
        "venue_call": True,
        "local_run_dir": False,
        "result": ("server", "success", "done"),
    }


def test_a_site_without_the_venue_refuses_by_name(tmp_path, world, capsys):
    from r4._cli import team_plan

    umrs = VenueUmrs()
    umrs.refuse_start = (
        "plan_iteration_needs_server_venue: this server cannot host a team plan run: "
        "the frontend venue is not enabled or not configured (missing: UMRS_VENUE_ENABLED)"
    )
    args = _run_args(tmp_path, venue="server", venue_sources="openai-main")
    assert team_plan._cmd_run(args, _client(umrs)) == 1
    err = capsys.readouterr().err
    assert "refused: plan_iteration_needs_server_venue" in err and "UMRS_VENUE_ENABLED" in err
    assert umrs.runs == {}


def test_a_server_run_needs_a_pushed_source(tmp_path, world, capsys):
    from r4._cli import team_plan

    umrs = VenueUmrs()
    assert team_plan._cmd_run(_run_args(tmp_path, venue="server"), _client(umrs)) == 2
    assert "--source" in capsys.readouterr().err
    assert umrs.calls == []


def test_cancel_and_resume_by_run_id(tmp_path, world, capsys):
    from r4._cli import team_plan

    umrs = VenueUmrs()
    run_id, definition = _definition(umrs)
    umrs.seed(run_id, definition, {})
    assert _worker(umrs, tmp_path / "w1", run_id).run() == 0
    client = _client(umrs)
    # resume: a fresh worker for attempt 2; the follower ignores attempt 1
    umrs.on_status = _serve(umrs, tmp_path / "w2", [run_id])
    args = SimpleNamespace(
        run_id=run_id,
        from_phase=None,
        venue="server",
        venue_sources="openai-main",
        venue_default_source=None,
        venue_web_policy="public",  # ADR-099: a remote start needs a choice
        detach=False,
        durable=None,
    )
    assert team_plan._cmd_resume(args, client) == 0
    assert umrs.venue[run_id]["body"]["definition"] == {}  # the recorded one is used
    assert [a["label"] for a in umrs.runs[run_id]["attempts"]] == [1, 2]
    assert team_plan._cmd_cancel(SimpleNamespace(run_id=run_id), client) == 0
    assert umrs.runs[run_id]["status"] == "cancelled"
    assert f"{run_id}: cancelled; worker stopped" in capsys.readouterr().out


def test_the_follower_names_a_waiting_gate_and_the_owner_may_answer_it(tmp_path):
    umrs = VenueUmrs()
    umrs.seed("r-1", {"plan": {}}, {})
    umrs.gates["pgate-1"] = {"gate": "ask", "class": "cost", "prompt": "Go?", "status": "pending"}
    client = _client(umrs)
    lines: list[str] = []
    asked: list[str] = []

    def _ask(question):
        asked.append(question)
        return True

    follower = server_venue.Follower(
        client, "r-1", emit=lines.append, ask=_ask, sleep=lambda _s: None
    )
    # without a person to ask (the chat tool, a pipe) nothing is decided
    silent = server_venue.Follower(client, "r-1", emit=lines.append, sleep=lambda _s: None)
    assert silent.follow(timeout_sec=0)["status"] == "running"
    assert client.decided == [] and umrs.gates["pgate-1"]["status"] == "pending"
    assert "r4 project loop decide pgate-1" in lines[0]
    assert follower.follow(timeout_sec=0)["status"] == "running"
    # the owner's answer goes through the inbox's decide route, as the owner
    assert client.decided == [("pgate-1", "once")] and "Go?" in asked[0]


def test_outcome_reads_only_attempts_after_the_baseline():
    view = {
        "status": "running",
        "attempts": [{"label": 1, "status": "failed", "plan_status": "failed", "summary": {}}],
        "venue": {"state": "provisioning"},
    }
    assert server_venue.outcome(view, baseline=1) is None
    assert server_venue.outcome(view)["status"] == "failed"
    view["venue"] = {"state": "failed", "reason": "worker: boom"}
    assert server_venue.outcome(view, baseline=1) == {
        "status": "failed",
        "error": "worker_ended: worker: boom",
    }
    assert server_venue.outcome({"status": "cancelled"})["status"] == "cancelled"


@pytest.mark.parametrize(
    ("venue", "attempts", "expected"),
    [
        ({"state": "stopped"}, [{"status": "running"}], None),  # its attempt still runs
        ({"state": "failed", "reason": "pull"}, [], "worker_ended: pull"),
        (
            {"state": "stopped", "runtime": {"detail": "oom"}},
            [{"status": "lost"}],
            "worker_ended: oom",
        ),
        ({"state": "running"}, [], None),
    ],
)
def test_a_worker_that_ended_without_closing_its_attempt_fails_the_run(venue, attempts, expected):
    view = {"status": "running", "venue": venue, "attempts": attempts}
    assert (server_venue.outcome(view) or {}).get("error") == expected


@pytest.mark.parametrize(
    ("record", "line"),
    [
        (
            {"kind": "gate", "node": "g", "ok": True, "decided_by": "alice"},
            "[gate] g: approved by alice",
        ),
        ({"kind": "gate", "node": "g", "ok": False}, "[gate] g: rejected by a human"),
        ({"kind": "invalidate"}, "[durable] phases invalidated"),
        ({"kind": "agent", "label": "a#0", "ok": True}, "[agent] a#0 ok"),
        ({"node": "n", "ok": False, "reason": "timeout"}, "[agent] n null(timeout)"),
    ],
)
def test_each_record_kind_has_its_follow_line(record, line):
    assert server_venue._record_line({"record": json.dumps(record)}) == line


def test_a_model_naming_a_source_is_refused_for_the_server_venue(world):
    umrs = VenueUmrs()
    plan = _plan()
    plan["agents"] = {"pro": {"instructions": "x", "model": "gpt-x@openai-main"}}
    plan["phases"][0]["steps"][0]["use"] = "pro"
    with pytest.raises(ValueError, match="name another source: gpt-x@openai-main"):
        _definition(umrs, plan)
    # slice 8b: the site's own models are a venue source
    plan["agents"]["pro"]["model"] = "qwen3-8b@site"
    _run_id, definition = _definition(umrs, plan)
    assert definition["plan"]["agents"]["pro"]["model"] == "qwen3-8b@site"
    # the pinned default drops its source: the venue's sources serve it
    service = TeamService(_client(umrs, model="m1@openai-main"), records=None)
    _run_id, definition = service.hosted_definition(_spec())
    assert definition["manifest"]["default_model"] == "m1"
    service = TeamService(_client(umrs, model="qwen3-8b@site"), records=None)
    _run_id, definition = service.hosted_definition(_spec())
    assert definition["manifest"]["default_model"] == "qwen3-8b@site"


def test_the_sandbox_routes_model_at_site_to_the_site_source(monkeypatch):
    """``model@site``: the same sidecar under its ``site/`` prefix, the bare
    model in the payload, no sources.json and no other credential."""
    from r4.venue_rail import build_rail_platform_client

    for name in ("R4_API_KEY", "R4_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    client = build_rail_platform_client(
        rail_url="http://egress:8081",
        session_id="planrun-x",
        token="tok-123",
        endpoint_url="http://egress:8080",
        model="qwen3-8b@site",
    )
    payload, route = client._normalize_route({"model": "qwen3-8b@site", "messages": []})
    assert (route.base_url, payload["model"]) == ("http://egress:8080/site", "qwen3-8b")
    plain = client._resolve_gated_route("m1")
    assert plain.base_url == "http://egress:8080"
    assert plan_worker.bare_model("qwen3-8b@site") == "qwen3-8b@site"
    assert plan_worker.bare_model("m1@openai-main") == "m1"


def test_the_chat_tool_runs_on_the_server_venue(tmp_path, world):
    from r4.companion.team_plan_tools import build_team_plan_tools

    umrs = VenueUmrs()
    client = _client(umrs)
    box: list[str] = []
    original = umrs.start_on_venue

    def _start(run_id, body):
        box.append(run_id)
        umrs.on_status = _serve(umrs, tmp_path / "server", box)
        return original(run_id, body)

    umrs.start_on_venue = _start
    long_answer = "the reviewers found two defects in the solver's boundary handling"
    world["script"]["fin"] = long_answer
    imported: list[Any] = []
    lead = SimpleNamespace(
        taint_registry=SimpleNamespace(
            import_segments=lambda segments: imported.extend(segments) or len(segments)
        )
    )
    tools = {
        t["name"]: t
        for t in build_team_plan_tools(
            client,
            run_root=tmp_path / "chat",
            permission_manager=lead,
            web_access=lambda: make_web_access("allowlist", ".arxiv.org", source="prompt"),
        )
    }
    reply = json.loads(
        tools["team_plan_run"]["execute"](
            plan=_plan(), venue="server", sources=["openai-main"], wait_sec=5
        )
    )
    assert (reply["venue"], reply["status"], reply["output"]["result"]) == (
        "server",
        "success",
        long_answer,
    )
    # the session's choice reaches the worker's start (ADR-099 D-2; RS-1
    # round-2 fix W2-1: the chat sent none, so UMRS refused or widened it)
    started = umrs.venue[box[0]]["body"]
    assert (started["web_policy"], started["web_allow_domains"]) == ("allowlist", [".arxiv.org"])
    # the worker's output is untrusted: absorbed whole into the lead's fence (D-6)
    assert reply["taint_segments"] >= 1
    assert any(seg["text_excerpt"] in long_answer for seg in imported)
    later = json.loads(tools["team_plan_result"]["execute"](run_id=box[0]))
    assert (later["venue"], later["status"]) == ("server", "success")
    umrs.refuse_start = "plan_iteration_needs_server_venue: missing: UMRS_VENUE_ENABLED"
    refused = json.loads(
        tools["team_plan_run"]["execute"](plan=_plan(), venue="server", sources=["s"])
    )
    assert (refused["ok"], refused["code"]) == (False, "plan_iteration_needs_server_venue")


@pytest.mark.parametrize(
    "choice",
    [
        WebAccess(mode=""),  # never chosen
        make_web_access("none", source="default_off"),  # r4 -p's fallback, nobody chose it
    ],
)
def test_the_chat_tool_refuses_a_server_run_without_the_users_web_choice(tmp_path, world, choice):
    """RS-1 round-2 fix W2-1: no choice → the typed refusal before anything is
    sent (it reached UMRS without one: refused, or the site default)."""
    from r4.companion.team_plan_tools import build_team_plan_tools

    umrs = VenueUmrs()
    sent: list[str] = []
    umrs.start_on_venue = lambda run_id, body: sent.append(run_id)
    tools = {
        t["name"]: t
        for t in build_team_plan_tools(
            _client(umrs), run_root=tmp_path / "chat", web_access=lambda: choice
        )
    }
    reply = json.loads(
        tools["team_plan_run"]["execute"](plan=_plan(), venue="server", sources=["s"])
    )
    assert (reply["ok"], reply["code"]) == (False, "sessionpolicy.web_choice_required")
    assert "/web" in reply["error"] and "--web" in reply["error"]
    assert sent == []


def test_the_chat_tool_resumes_a_server_run_with_the_users_web_choice(tmp_path, world):
    from r4.companion.team_plan_tools import build_team_plan_tools, executor_of

    umrs = VenueUmrs()
    run_id, definition = _definition(umrs, _plan())
    umrs.seed(run_id, definition, {})
    tools = {
        t["name"]: t
        for t in build_team_plan_tools(
            _client(umrs),
            run_root=tmp_path / "chat",
            web_access=lambda: make_web_access("public", source="flag"),
        )
    }
    executor = executor_of(list(tools.values()))
    call = SimpleNamespace(given={"sources": ["s"]}, resume=run_id, spec=None)
    executor._on_server(call, 0)
    assert umrs.venue[run_id]["body"]["web_policy"] == "public"


def test_the_engine_closes_with_its_outputs_when_they_fit():
    from r4.teams.plan.run import MAX_CLOSE_OUTPUT_BYTES, _close_output

    assert _close_output({"a": 1}) == {"output": {"a": 1}}
    assert _close_output({"a": "x" * (MAX_CLOSE_OUTPUT_BYTES + 1)}) == {"output_truncated": True}


def _run_args(tmp_path: Path, **over: Any) -> SimpleNamespace:
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(_plan()))
    base = {
        "ref": str(plan_file),
        "arg": [],
        "args_json": None,
        "max_tokens": None,
        "concurrency": None,
        "model": None,
        "project": None,
        "dry_run": False,
        "durable": None,
        "venue": "local",
        "venue_sources": None,
        "venue_default_source": None,
        "venue_web_policy": "public",  # ADR-099: a remote start needs a choice
        "detach": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_the_venue_worker_command_hosts_one_plan_run(monkeypatch):
    """``r4 venue worker --plan-run <id>`` is the plan worker; without the flag
    it is the chat loop (the command UMRS writes into the session's job)."""
    import argparse

    from r4._cli import venue as venue_cli
    from r4.teams import plan_worker as worker_module

    seen: list[str] = []
    monkeypatch.setattr(worker_module, "main", lambda run_id: seen.append(run_id) or 0)
    parser = argparse.ArgumentParser()
    command = venue_cli.VenueCommand()
    command.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["venue", "worker", "--plan-run", "r-1"])
    assert command.handle(args) == 0 and seen == ["r-1"]


# ── slice 8b: a server-hosted loop of plan runs ────────────────────────────


def _loop_spec(**over):
    from r4.runtime.api import LoopSpec

    base = {
        "project_id": "proj",
        "research_goal": "find a better solver",
        "iteration_plan": "research-round",
        "plan_sources": ("site",),
        # ADR-099 D-2 (M-4): the user's web choice for the loop's runs.
        "plan_web_policy": "none",
    }
    base.update(over)
    return LoopSpec(**base)


def test_the_umrs_runtime_sends_the_plan_loop_for_the_server_to_run():
    from r4.runtime.umrs import UmrsRuntime

    sent: list[dict[str, Any]] = []

    def start_loop(project, goal, **kw):
        sent.append({"project": project, "goal": goal, **kw})
        return {"loop_id": "loop-1", "status": "running"}

    client = SimpleNamespace(config=SimpleNamespace(model="qwen3-8b@site"), start_loop=start_loop)
    state = UmrsRuntime(client).start_loop(_loop_spec(plan_args={"judges": 3}))
    plan = sent[0]["plan"]
    assert state.loop_id == "loop-1"
    assert {
        "template": plan["iteration_plan"],
        "resolved": all(
            isinstance(p["instructions"], str)
            for p in plan["iteration_plan_doc"]["agents"].values()
        ),
        "args": plan["plan_args"],
        "sources": plan["plan_sources"],
        "model": plan["plan_default_model"],
        "mode": plan["mode"],
        "web": plan["plan_web_policy"],
    } == {
        "template": "research-round",
        "resolved": True,  # r4 owns the templates and resolves their references
        "args": {"judges": 3},
        "sources": ["site"],
        "model": "qwen3-8b@site",
        "mode": "ask",  # a plan loop with no mode asks (fail closed)
        "web": "none",
    }


def test_the_loop_table_is_checked_before_anything_is_sent():
    from r4.runtime.umrs import UmrsRuntime

    client = SimpleNamespace(
        config=SimpleNamespace(model="m"),
        start_loop=lambda *a, **k: pytest.fail("nothing is sent"),
    )
    with pytest.raises(ValueError, match="belong to the loop"):
        UmrsRuntime(client).start_loop(_loop_spec(plan_args={"objective": "mine"}))
    with pytest.raises(ValueError, match="cannot run as a loop iteration"):
        UmrsRuntime(client).start_loop(_loop_spec(iteration_plan="code"))


def test_the_servers_named_refusal_is_raised_by_name():
    from r4.runtime.umrs import PlanIterationRefused, UmrsRuntime

    detail = (
        "plan_iteration_needs_server_venue: … (this site has no server venue "
        "(missing: UMRS_VENUE_ENABLED); enable the frontend venue …)"
    )

    def start_loop(*_a, **_k):
        raise APIError("422", status_code=422, response_body=json.dumps({"detail": detail}))

    client = SimpleNamespace(config=SimpleNamespace(model="m"), start_loop=start_loop)
    with pytest.raises(PlanIterationRefused) as exc:
        UmrsRuntime(client).start_loop(_loop_spec())
    assert str(exc.value) == detail and exc.value.code == "plan_iteration_needs_server_venue"


def test_the_chat_tool_starts_a_server_plan_loop_with_its_sources(monkeypatch):
    from r4.companion.research_loop_tools import _build_research_loop_start

    monkeypatch.setenv("R4_RUNTIME_MODE", "umrs")
    sent: list[dict[str, Any]] = []
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="proj", model="m1"),
        start_loop=lambda p, g, **kw: sent.append(kw) or {"loop_id": "loop-9", "status": "running"},
        get_platform_project=lambda _p: {},
        get=lambda *_a, **_k: {},
    )
    from r4.companion.web_access import make_web_access

    session_choice = lambda: make_web_access("public", source="prompt")  # noqa: E731
    reply = json.loads(
        _build_research_loop_start(client, session_choice)["execute"](
            research_goal="g", project_id="proj", plan="research-round", sources="openai-main,site"
        )
    )
    assert reply.get("loop_id") == "loop-9", reply
    assert sent[0]["plan"]["plan_sources"] == ["openai-main", "site"]
    assert sent[0]["plan"]["plan_web_policy"] == "public"


def test_the_local_loop_reads_the_shared_table():
    """r4's consumer of r4_contracts.loop_plan (the one pin is there)."""
    from r4_contracts import loop_plan

    from r4.runtime import api, plan_executor

    assert plan_executor.LOOP_TEMPLATES is loop_plan.LOOP_TEMPLATES
    assert plan_executor.loop_decision is loop_plan.loop_decision
    assert (api.DECISION_PUBLISH, api.LOOP_DECISION_KEY) == (
        loop_plan.DECISION_PUBLISH,
        loop_plan.LOOP_DECISION_KEY,
    )
