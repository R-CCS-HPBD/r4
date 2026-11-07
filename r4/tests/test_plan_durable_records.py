"""A plan run's durable record in UMRS; the run directory is its cache (ADR-092 slice 7).

MD-3: the local journal is an attempt-local cache, resume kept. MD-4: the
durable record is UMRS's, a client-hosted ``research_loop_job`` plus one
``execution_traces`` row per portable record keyed by its D-8 call key.
These tests run the real ``TeamService`` and plan engine on scripted turns
(``MemoryAgentBroker``) against ``FakeUmrs``: UMRS's plan-run routes in
memory, with their attempt rules (a superseded or finished attempt's writes
are refused) and JSONB's key reordering of every row it returns.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_contracts.team_engine.events import EventKind
from r4_teamengine_spi.testing import MemoryAgentBroker

from r4.teams import durable_records
from r4.teams import service as team_service
from r4.teams.durable_records import (
    DurableRecordUnavailable,
    DurableRunRecords,
    UmrsPlanRecords,
    umrs_records_for,
)
from r4.teams.plan import spec_from_plan
from r4.teams.plan.journal import PlanJournal
from r4.teams.service import TeamService

PORTABLE = {None, "agent", "gate", "invalidate"}


def _plan() -> dict[str, Any]:
    """Three phases: an agent, a two-stage map over its objects, a final agent."""
    return {
        "plan_version": 1,
        "name": "durable",
        "description": "a plan whose record outlives its machine",
        "budget": {"max_tokens": 100_000},
        "phases": [
            {
                "id": "list",
                "title": "List",
                "steps": [{"id": "src", "kind": "agent", "prompt": "list"}],
            },
            {
                "id": "work",
                "title": "Work",
                "steps": [
                    {
                        "id": "m",
                        "kind": "map",
                        "over": "steps.src.output",
                        "max_items": 10,
                        "stages": [
                            {"kind": "agent", "label": "a", "prompt": "first {{item|json}}"},
                            {"kind": "agent", "label": "b", "prompt": "second {{prev|json}}"},
                        ],
                    }
                ],
            },
            {
                "id": "sum",
                "title": "Sum",
                "steps": [{"id": "fin", "kind": "agent", "prompt": "sum {{steps.m.output|json}}"}],
            },
        ],
        "output": {"result": "steps.fin.output", "items": "steps.m.output"},
    }


# Objects whose keys are NOT sorted: a replay that reordered them would
# change the readers' rendered prompts, so their keys, so they would rerun.
SCRIPT: dict[str, Any] = {
    "src": [{"zeta": 1, "alpha": "x"}, {"zeta": 2, "alpha": "y"}, {"zeta": 3, "alpha": "z"}],
    "a": lambda r: {"why": f"a{r.metadata['item_index']}", "aa": 1},
    "b": lambda r: {"see": f"b{r.metadata['item_index']}", "bb": 2},
    "fin": "done",
}
CALLS = 1 + 3 * 2 + 1


class UmrsDown(RuntimeError):
    pass


class FakeUmrs:
    """UMRS's /umrs/traces/plan-runs routes, in memory (one caller)."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.fail_after: dict[str, int] = {}
        self.fail_once: dict[str, int] = {}  # op -> the one call (1-based) that fails
        self.counts: dict[str, int] = {}

    def _gate(self, op: str) -> None:
        self.counts[op] = self.counts.get(op, 0) + 1
        limit = self.fail_after.get(op)
        if (limit is not None and self.counts[op] > limit) or self.fail_once.get(op) == self.counts[
            op
        ]:
            raise UmrsDown(f"UMRS unavailable ({op}, simulated)")

    def open_attempt(self, run_id, *, attempt, definition, required):
        self._gate("open")
        run = self.runs.setdefault(
            run_id,
            {
                "definition": json.loads(json.dumps(definition)),
                "required": required,
                "attempts": [],
                "active": None,
                "rows": [],
            },
        )
        superseded = []
        for att in run["attempts"]:
            if att["status"] == "running":
                att["status"] = "lost"
                superseded.append(att["attempt_id"])
        att = {"attempt_id": f"att-{len(run['attempts']) + 1}", "label": attempt}
        att["status"] = "running"
        run["attempts"].append(att)
        run["active"] = att["attempt_id"]
        return {
            "job_id": f"job-{run_id}",
            "attempt_id": att["attempt_id"],
            "superseded": superseded,
        }

    def append(self, run_id, attempt_id, record_text):
        self._gate("append")
        run = self.runs[run_id]
        if run["active"] != attempt_id:
            raise UmrsDown(f"409 attempt_not_active: {attempt_id}")
        record = json.loads(record_text)
        assert record.get("kind") in PORTABLE, record.get("kind")
        digest = hashlib.sha256(record_text.encode()).hexdigest()
        row = {"record": record_text, "outputs_hash": digest, "call_key": record["call_key"]}
        run["rows"].append({**row, "attempt_id": attempt_id})
        return {"trace_id": f"trc-{len(run['rows'])}"}

    def records(self, run_id):
        self._gate("records")
        if run_id not in self.runs:
            raise UmrsDown("404")
        return [_jsonb(row) for row in self.runs[run_id]["rows"]]

    def run(self, run_id):
        self._gate("run")
        if run_id not in self.runs:
            raise UmrsDown(f"404 no durable record of plan run {run_id}")
        run = self.runs[run_id]
        return _jsonb(
            {
                "run_id": run_id,
                "job_id": f"job-{run_id}",
                "durable_required": run["required"],
                "definition": run["definition"],
                "attempts": [{"label": a["label"], "status": a["status"]} for a in run["attempts"]],
            }
        )

    def finish(self, run_id, attempt_id, *, status, error, summary):
        self._gate("finish")
        run = self.runs[run_id]
        if run["active"] != attempt_id:
            raise UmrsDown("409 attempt_not_active")
        att = next(a for a in run["attempts"] if a["attempt_id"] == attempt_id)
        att.update(status=status, error=error, summary=summary)
        run["active"] = None
        return {"job_status": "completed" if status in ("success", "partial") else "failed"}

    def keys(self, run_id) -> list[str]:
        return [row["call_key"] for row in self.runs[run_id]["rows"]]


def _jsonb(value: Any) -> Any:
    """What a JSONB round trip does to an object: its keys come back reordered."""
    return json.loads(json.dumps(value, sort_keys=True))


@pytest.fixture()
def world(monkeypatch):
    state: dict[str, Any] = {"brokers": [], "script": dict(SCRIPT)}

    def _broker(self, spec, run_id, meter):
        broker = MemoryAgentBroker(dict(state["script"]))
        state["brokers"].append(broker)
        return broker

    monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker)
    return state


def _client():
    return SimpleNamespace(config=SimpleNamespace(model="m1", project_id="proj"))


def _service(root: Path, umrs: Any, project: Path | None = None) -> TeamService:
    return TeamService(_client(), run_root=root, records=umrs, project_root=project)


def _spec():
    spec, problems = spec_from_plan(_plan(), {}, project="proj")
    assert not problems, problems
    return spec


async def _drain(handle) -> tuple[list[Any], dict[str, Any]]:
    events = [event async for event in handle.events()]
    await handle.result()
    return events, json.loads((handle.run_dir / "result.json").read_text())


def _journal_keys(run_dir: Path) -> list[str]:
    return [
        r["call_key"]
        for r in PlanJournal(run_dir).records()
        if r.get("kind") in PORTABLE and "call_key" in r
    ]


def _messages(events, kind: str) -> list[dict[str, Any]]:
    return [
        e.payload
        for e in events
        if e.kind is EventKind.AGENT_MESSAGE and e.payload.get("type") == kind
    ]


# ── write-through ──────────────────────────────────────────────────────────


async def test_every_agent_call_is_written_through_keyed_by_its_d8_key(tmp_path, world):
    umrs = FakeUmrs()
    handle = await _service(tmp_path, umrs).submit(_spec())
    events, doc = await _drain(handle)
    run = umrs.runs[handle.run_id]
    assert {
        "status": doc["status"],
        "umrs_keys": umrs.keys(handle.run_id),
        "durable": {k: doc["durable"][k] for k in ("home", "state", "written", "local_only")},
        "scope": doc["checkpoint_scope"],
        "job": doc["durable"]["job_id"],
        "finished": (run["attempts"][0]["status"], run["active"]),
        "definition": sorted(run["definition"]),
        "manifest_durable": json.loads((handle.run_dir / "resolution.json").read_text())["durable"],
        "terminal": events[-1].payload["durable"]["state"],
    } == {
        "status": "success",
        "umrs_keys": _journal_keys(handle.run_dir),
        "durable": {"home": "umrs", "state": "ok", "written": CALLS, "local_only": 0},
        "scope": "portable",
        "job": f"job-{handle.run_id}",
        "finished": ("success", None),
        "definition": ["manifest", "params", "plan"],
        "manifest_durable": {
            "home": "umrs",
            "required": False,
            "job_id": f"job-{handle.run_id}",
        },
        "terminal": "ok",
    }
    assert len(umrs.keys(handle.run_id)) == CALLS


async def test_a_run_without_a_umrs_source_stays_local_and_says_so(tmp_path, world):
    handle = await _service(tmp_path, None).submit(_spec())
    _events, doc = await _drain(handle)
    assert doc["status"] == "success"
    assert doc["durable"] == {"home": "local", "state": "local_only", "required": False}
    assert doc["checkpoint_scope"] == "engine_internal"


@pytest.mark.parametrize(("umrs", "scope"), [(FakeUmrs, "portable"), (None, "engine_internal")])
async def test_the_checkpoint_scope_is_portable_only_with_a_umrs_home(
    tmp_path, monkeypatch, umrs, scope
):
    hold = asyncio.Event()

    def _broker(self, spec, run_id, meter):
        return MemoryAgentBroker(dict(SCRIPT), gates={"fin": hold})

    monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker)
    handle = await _service(tmp_path, umrs() if umrs else None).submit(_spec())
    seen = []
    async for event in handle.events():
        seen.append(event)
        if event.kind is EventKind.AGENT_STARTED and event.payload.get("label") == "fin":
            await handle.engine_run.checkpoint()
            hold.set()
    (checkpoint,) = [e.payload for e in seen if e.kind is EventKind.CHECKPOINT_CREATED]
    assert checkpoint["scope"] == scope
    assert checkpoint["journal"] == "local-cache"


async def test_workspace_and_merge_records_stay_on_their_machine(tmp_path):
    sent: list[dict[str, Any]] = []
    broker = SimpleNamespace(required=False, append=sent.append, load=lambda: [])
    journal = PlanJournal(tmp_path, broker)
    journal.append({"call_key": "k1", "ok": True, "output": 1})
    journal.append({"call_key": "g1", "kind": "gate", "ok": True})
    journal.append({"call_key": "workspace:w1", "kind": "workspace", "ok": False})
    journal.append({"call_key": "merge_back:m", "kind": "merge_back", "ok": True})
    assert [r["call_key"] for r in sent] == ["k1", "g1"]
    assert len(journal.records()) == 4


# ── resume from any venue ────────────────────────────────────────────────────


async def test_a_resume_on_another_machine_replays_from_umrs_at_zero_tokens(tmp_path, world):
    umrs = FakeUmrs()
    first = await _service(tmp_path / "a", umrs).submit(_spec())
    _e1, doc1 = await _drain(first)
    shutil.rmtree(first.run_dir)  # the first machine is gone

    world["script"] = {}  # unscripted: any live call would be a failed turn
    elsewhere = _service(tmp_path / "b", umrs, project=tmp_path)
    second = await elsewhere.resume(first.run_id)
    events, doc2 = await _drain(second)
    completed = [e.payload for e in events if e.kind is EventKind.AGENT_COMPLETED]
    manifest = json.loads((second.run_dir / "resolution.json").read_text())
    assert {
        "live_calls": world["brokers"][-1].calls,
        "outputs": doc2["output"],
        "status": (doc2["status"], doc2["attempt"]),
        "replayed": [p["replayed"] for p in completed],
        "tokens": (await second.metrics()).total_tokens,
        "from_umrs": doc2["durable"]["replayed_from_durable"],
        "cache_filled": sorted(_journal_keys(second.run_dir)),
        "attempts": [a["status"] for a in umrs.runs[first.run_id]["attempts"]],
        "root": manifest["project_root"],
    } == {
        "live_calls": [],
        "outputs": doc1["output"],
        "status": ("success", "attempt-2"),
        "replayed": [True] * CALLS,
        "tokens": 0,
        "from_umrs": CALLS,
        "cache_filled": sorted(umrs.keys(first.run_id)),
        "attempts": ["success", "success"],
        "root": str(tmp_path.resolve()),
    }


async def test_a_resume_elsewhere_with_no_umrs_record_is_named(tmp_path, world):
    with pytest.raises(FileNotFoundError, match="UMRS has no durable record"):
        await _service(tmp_path, FakeUmrs()).resume("ghost-00000000")
    with pytest.raises(FileNotFoundError, match="no UMRS source is configured"):
        await _service(tmp_path, None).resume("ghost-00000000")


async def test_from_phase_elsewhere_drops_the_umrs_records_of_that_phase(tmp_path, world):
    umrs = FakeUmrs()
    first = await _service(tmp_path / "a", umrs).submit(_spec())
    await _drain(first)
    shutil.rmtree(first.run_dir)

    world["script"] = {"fin": "again"}
    second = await _service(tmp_path / "b", umrs).resume(first.run_id, from_phase="sum")
    _events, doc = await _drain(second)
    assert [c.label for c in world["brokers"][-1].calls] == ["fin"]
    assert doc["output"]["result"] == "again"
    markers = [json.loads(r["record"]) for r in umrs.runs[first.run_id]["rows"]]
    assert [m["phases"] for m in markers if m.get("kind") == "invalidate"] == [["sum"]]


async def test_reads_prefer_the_local_journal_and_fill_from_umrs(tmp_path):
    local = {"call_key": "k1", "ok": True, "output": "local"}
    remote = [
        {"call_key": "k1", "ok": True, "output": "umrs"},
        {"call_key": "k2", "ok": True, "output": "only-umrs"},
        {"call_key": "k3", "ok": False, "reason": "boom"},
    ]
    PlanJournal(tmp_path).append(local)
    broker = SimpleNamespace(required=False, load=lambda: remote, append=lambda r: None)
    journal = PlanJournal(tmp_path, broker)
    memo = journal.load()
    assert {k: r.get("output") for k, r in memo.items()} == {
        "k1": "local",
        "k2": "only-umrs",
        "k3": None,
    }
    # the cache filled: the next read needs nothing from UMRS
    assert PlanJournal(tmp_path).load()["k2"]["output"] == "only-umrs"
    assert journal.report()["replayed_from_durable"] == 1


async def test_a_local_failure_never_displaces_an_ok_record_from_umrs(tmp_path):
    PlanJournal(tmp_path).append({"call_key": "k1", "ok": False, "reason": "boom"})
    remote = [{"call_key": "k1", "ok": True, "output": "paid"}]
    broker = SimpleNamespace(required=False, load=lambda: remote, append=lambda r: None)
    assert PlanJournal(tmp_path, broker).load()["k1"]["output"] == "paid"


async def test_a_record_whose_text_does_not_match_its_hash_runs_live(tmp_path, world):
    umrs = FakeUmrs()
    first = await _service(tmp_path / "a", umrs).submit(_spec())
    await _drain(first)
    shutil.rmtree(first.run_dir)
    row = next(r for r in umrs.runs[first.run_id]["rows"] if '"label": "fin"' in r["record"])
    row["record"] = row["record"].replace('"done"', '"forged"')

    world["script"] = {"fin": "honest"}
    second = await _service(tmp_path / "b", umrs).resume(first.run_id)
    _events, doc = await _drain(second)
    assert [c.label for c in world["brokers"][-1].calls] == ["fin"]
    assert doc["output"]["result"] == "honest"
    assert doc["durable"]["skipped_records"] == 1


# ── failures are named; required runs fail closed ────────────────────────────


async def test_a_failed_write_is_named_and_the_run_continues_local_only(tmp_path, world):
    umrs = FakeUmrs()
    umrs.fail_after["append"] = 2
    handle = await _service(tmp_path, umrs).submit(_spec())
    events, doc = await _drain(handle)
    notes = _messages(events, "durable")
    assert {
        "status": doc["status"],
        "live": len(world["brokers"][-1].calls),
        "state": doc["durable"]["state"],
        "counts": (doc["durable"]["written"], doc["durable"]["local_only"]),
        "scope": doc["checkpoint_scope"],
        "notes": len(notes),
        "local": len(_journal_keys(handle.run_dir)),
        "umrs": len(umrs.keys(handle.run_id)),
    } == {
        "status": "success",
        "live": CALLS,
        "state": "degraded",
        "counts": (2, CALLS - 2),
        "scope": "engine_internal",
        "notes": 1,
        "local": CALLS,
        "umrs": 2,
    }
    assert "UmrsDown" in doc["durable"]["error"]
    assert "continues local-only" in notes[0]["text"]
    assert events[-1].payload["durable"]["state"] == "degraded"


async def test_after_a_failed_write_the_run_stays_local_only_even_if_umrs_recovers(tmp_path, world):
    umrs = FakeUmrs()
    umrs.fail_once["append"] = 3  # one blip; UMRS answers again afterwards
    handle = await _service(tmp_path, umrs).submit(_spec())
    _events, doc = await _drain(handle)
    # never a record with a gap before it: after the failure nothing more is sent
    assert umrs.counts["append"] == 3
    assert len(umrs.keys(handle.run_id)) == 2
    assert (doc["durable"]["written"], doc["durable"]["local_only"]) == (2, CALLS - 2)
    assert umrs.counts.get("finish", 0) == 0  # the attempt is not reported complete


async def test_a_required_resume_fails_closed_when_umrs_cannot_be_read(tmp_path, world):
    umrs = FakeUmrs()
    first = await _service(tmp_path, umrs).submit(_spec(), durable=True)
    await _drain(first)
    shutil.rmtree(first.run_dir)
    umrs.fail_after["records"] = 0
    world["script"] = dict(SCRIPT)
    second = await _service(tmp_path, umrs).resume(first.run_id)
    events, doc = await _drain(second)
    assert world["brokers"][-1].calls == []  # nothing spent live without the record
    assert doc["status"] == "failed"
    assert doc["error"].startswith("durable_record_failed: read: UmrsDown")
    assert events[0].kind is EventKind.RUN_STARTED
    # it fails at once: no phase is walked, no agent is even refused
    assert not [e for e in events if e.kind is EventKind.AGENT_COMPLETED]
    assert not _messages(events, "phase")


async def test_a_required_run_stops_the_agents_in_flight_when_a_write_fails(tmp_path, monkeypatch):
    held = {label: asyncio.Event() for label in ("a[1]", "a[2]")}  # never released
    brokers: list[MemoryAgentBroker] = []

    def _broker(self, spec, run_id, meter):
        brokers.append(MemoryAgentBroker(dict(SCRIPT), gates=held))
        return brokers[-1]

    monkeypatch.setattr(team_service.TeamService, "_agent_broker", _broker)
    umrs = FakeUmrs()
    umrs.fail_after["append"] = 1  # src is written; a[0]'s write fails
    handle = await _service(tmp_path, umrs).submit(_spec(), durable=True)
    _events, doc = await asyncio.wait_for(_drain(handle), timeout=10)
    assert brokers[-1].cancelled.startswith("durable_record_failed")
    assert doc["status"] == "failed"
    assert {c.label for c in brokers[-1].calls} == {"src", "a[0]", "a[1]", "a[2]"}


async def test_an_open_failure_is_named_and_the_run_is_local_only(tmp_path, world):
    umrs = FakeUmrs()
    umrs.fail_after["open"] = 0
    handle = await _service(tmp_path, umrs).submit(_spec())
    events, doc = await _drain(handle)
    assert doc["status"] == "success"
    assert doc["durable"]["state"] == "degraded"
    assert doc["durable"]["error"].startswith("read: RuntimeError: open: UmrsDown")
    assert len(_messages(events, "durable")) == 1
    assert events[0].kind is EventKind.RUN_STARTED  # the note follows run_started


async def test_a_umrs_read_failure_still_replays_the_local_journal(tmp_path, world):
    umrs = FakeUmrs()
    first = await _service(tmp_path, umrs).submit(_spec())
    await _drain(first)
    umrs.fail_after["records"] = 0
    world["script"] = {}
    second = await _service(tmp_path, umrs).resume(first.run_id)
    _events, doc = await _drain(second)
    assert world["brokers"][-1].calls == []
    assert doc["status"] == "success"
    assert doc["durable"]["state"] == "degraded"
    assert doc["durable"]["error"].startswith("read: UmrsDown")


async def test_a_required_run_fails_closed_when_a_record_cannot_be_written(tmp_path, world):
    umrs = FakeUmrs()
    umrs.fail_after["append"] = 1
    world["script"]["src"] = [{"zeta": 1, "alpha": "x"}]
    handle = await _service(tmp_path, umrs).submit(_spec(), durable=True)
    events, doc = await _drain(handle)
    assert {
        "status": doc["status"],
        "error": doc["error"].split(":")[:3],
        "durable": (doc["durable"]["state"], doc["durable"]["required"]),
        # src was written; the first map agent's write failed; nothing started after
        "live": len(world["brokers"][-1].calls),
        "refused": "durable_record_failed" in json.dumps(doc["steps"]),
        "local": len(_journal_keys(handle.run_dir)),  # the paid calls are kept locally
    } == {
        "status": "failed",
        "error": ["durable_record_failed", " write", " UmrsDown"],
        "durable": ("failed", True),
        "live": 2,
        "refused": True,
        "local": 2,
    }
    assert "the run requires its durable record" in _messages(events, "durable")[0]["text"]


async def test_a_required_run_is_refused_without_a_umrs_source(tmp_path, world):
    with pytest.raises(DurableRecordUnavailable, match="durable_required"):
        await _service(tmp_path, None).submit(_spec(), durable=True)
    assert world["brokers"][-1].calls == []


async def test_a_required_run_is_refused_when_its_attempt_cannot_open(tmp_path, world):
    umrs = FakeUmrs()
    umrs.fail_after["open"] = 0
    with pytest.raises(DurableRecordUnavailable, match="nothing ran"):
        await _service(tmp_path, umrs).submit(_spec(), durable=True)
    assert world["brokers"][-1].calls == []


async def test_a_resume_keeps_the_runs_required_choice(tmp_path, world):
    umrs = FakeUmrs()
    world["script"]["fin"] = None  # a failed call, so the resume runs it live
    first = await _service(tmp_path, umrs).submit(_spec(), durable=True)
    await _drain(first)
    umrs.fail_after["append"] = umrs.counts["append"]  # every further write fails
    world["script"] = {"fin": "done"}
    second = await _service(tmp_path, umrs).resume(first.run_id)
    _events, doc = await _drain(second)
    assert umrs.runs[first.run_id]["required"] is True
    assert doc["status"] == "failed"
    assert doc["error"].startswith("durable_record_failed")


async def test_an_explicit_local_run_writes_nothing_to_umrs(tmp_path, world):
    umrs = FakeUmrs()
    handle = await _service(tmp_path, umrs).submit(_spec(), durable=False)
    _events, doc = await _drain(handle)
    assert umrs.runs == {}
    assert doc["durable"]["home"] == "local"


# ── the HTTP adapter and the source check ────────────────────────────────────


class _Http:
    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.answers = answers or {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.answers.get(url.rsplit("/", 1)[-1], {})


def _http_client(http: _Http) -> Any:
    return SimpleNamespace(http=http, config=SimpleNamespace(gateway_url="https://gw.example/"))


def test_the_adapter_speaks_the_traces_familys_plan_run_routes():
    http = _Http({"records": {"records": [{"record": "{}"}, "junk"]}})
    api = UmrsPlanRecords(_http_client(http))
    api.open_attempt("r-1", attempt=2, definition={"plan": {}}, required=True)
    api.append("r-1", "att-1", '{"call_key": "k"}')
    rows = api.records("r-1")
    api.run("r-1")
    api.finish("r-1", "att-1", status="success", error="", summary={"n": 1})
    base = "https://gw.example/gw/traces/plan-runs/r-1"
    assert [(m, u) for m, u, _ in http.calls] == [
        ("POST", f"{base}/attempts"),
        ("POST", f"{base}/records"),
        ("GET", f"{base}/records"),
        ("GET", base),
        ("POST", f"{base}/attempts/att-1/finish"),
    ]
    assert http.calls[0][2]["json_body"] == {
        "attempt": 2,
        "definition": {"plan": {}},
        "required": True,
    }
    assert http.calls[1][2]["json_body"] == {"attempt_id": "att-1", "record": '{"call_key": "k"}'}
    assert all(kw["timeout"] == durable_records.TIMEOUT_SEC for _m, _u, kw in http.calls)
    assert rows == [{"record": "{}"}]


def test_a_run_id_is_one_quoted_path_segment():
    http = _Http()
    UmrsPlanRecords(_http_client(http)).run("../x")
    assert http.calls[0][1].endswith("/gw/traces/plan-runs/..%2Fx")


def test_the_record_text_keeps_the_records_key_order():
    http = _Http({"attempts": {"job_id": "j", "attempt_id": "a"}})
    records = DurableRunRecords(UmrsPlanRecords(_http_client(http)), "r-1")
    records.open(1, {})
    records.append({"call_key": "k", "zeta": 1, "alpha": 2})
    assert http.calls[-1][2]["json_body"]["record"] == '{"call_key": "k", "zeta": 1, "alpha": 2}'


def test_umrs_records_come_only_with_a_configured_umrs_source(monkeypatch):
    for name in ("R4_GATEWAY_URL", durable_records.ENV_DURABLE):
        monkeypatch.delenv(name, raising=False)
    client = _http_client(_Http())
    assert umrs_records_for(SimpleNamespace(config=SimpleNamespace(model="m"))) is None
    assert umrs_records_for(client) is None  # no platform source: local-only
    monkeypatch.setenv("R4_GATEWAY_URL", "https://gw.example")
    assert isinstance(umrs_records_for(client), UmrsPlanRecords)
    monkeypatch.setenv(durable_records.ENV_DURABLE, "off")
    assert umrs_records_for(client) is None


def test_an_active_platform_source_is_a_umrs_source(monkeypatch):
    monkeypatch.delenv("R4_GATEWAY_URL", raising=False)
    from r4 import source

    platform = SimpleNamespace(kind="platform")
    monkeypatch.setattr(source, "resolve_active_source", lambda *_a: platform)
    assert isinstance(umrs_records_for(_http_client(_Http())), UmrsPlanRecords)
    monkeypatch.setattr(source, "resolve_active_source", lambda *_a: SimpleNamespace(kind="llm"))
    assert umrs_records_for(_http_client(_Http())) is None


# ── surfaces ────────────────────────────────────────────────────────────────


def test_the_cli_passes_durable_to_run_and_resume(monkeypatch, tmp_path):
    from r4._cli import team_plan

    seen: list[tuple[str, Any]] = []

    class _Svc:
        def submit(self, spec, **kw):
            seen.append(("submit", kw.get("durable")))

        async def resume(self, run_id, **kw):
            seen.append(("resume", kw.get("durable")))

    monkeypatch.setattr(team_plan, "_service", lambda _c, web_access=None: _Svc())
    monkeypatch.setattr(team_plan, "_stream_to_exit", lambda start: asyncio.run(_call(start)))
    monkeypatch.setattr(team_plan, "_project_mismatch", lambda _p: None)
    import r4._cli.teamup as teamup_cli

    monkeypatch.setattr(teamup_cli, "_launch_project", lambda _a, _c: "proj")
    plan_file = tmp_path / "p.json"
    plan_file.write_text(json.dumps(_plan()))
    import argparse

    root = argparse.ArgumentParser()
    team_plan.build_team_plan_parser(root.add_subparsers())
    for argv in (["run", str(plan_file), "--durable"], ["resume", "r-1", "--durable"]):
        team_plan.cmd_team_plan(root.parse_args(["plan", *argv]), _client())
    team_plan.cmd_team_plan(root.parse_args(["plan", "run", str(plan_file)]), _client())
    assert seen == [("submit", True), ("resume", True), ("submit", None)]


async def _call(start):
    result = start()
    if asyncio.iscoroutine(result):
        await result
    return 0


def test_the_cli_names_where_the_records_are(tmp_path):
    from r4._cli.team_plan import _durable_line

    (tmp_path / "result.json").write_text(
        json.dumps({"durable": {"home": "umrs", "state": "degraded", "job_id": "j", "error": "x"}})
    )
    assert _durable_line(tmp_path) == "durable: umrs degraded (job j); x"
    (tmp_path / "result.json").write_text(json.dumps({"durable": {"home": "local"}}))
    assert _durable_line(tmp_path) == "durable: local-only (this run directory)"


def test_the_chat_tools_result_and_card_name_the_durable_record():
    from r4.companion import team_plan_tools

    view = team_plan_tools._result_view(
        {"status": "success", "durable": {"home": "umrs", "state": "degraded", "error": "e"}}
    )
    assert view["durable"]["state"] == "degraded"
    assert "durable" in team_plan_tools.RUN_ARGUMENTS
    assert team_plan_tools._durable_lines({"durable": True}) == [
        "  durable: required: its records must reach UMRS, or the run stops"
    ]
    assert team_plan_tools._durable_arg({"durable": True}) is True
    assert team_plan_tools._durable_arg({"durable": False}) is None
