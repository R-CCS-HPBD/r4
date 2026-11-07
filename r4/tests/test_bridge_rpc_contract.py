"""Bridge RPC result contract (plan H-4.2/H-4.5).

Every verb in ``rpc_schema.RPC_RESULT_TYPES`` runs through its real handler
(``_bridge_rpc_bindings``, exactly as the JSONL dispatcher calls it) against a
real session store and session, with only the network faked; its output must
validate against the verb's exported JSON Schema. A handler that renames,
drops or retypes a key fails here before the TUI reads it.

The schema artifacts and the generated TS types have their own drift checks
in ``tests/test_bridge_rpc_schema_drift.py`` (repo root).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from bridge_rpc_harness import (
    VENUE_ROW,
    Case,
    Harness,
    Setup,
    case_ids,
    make_harness,
    plain,
    run_verb,
    schema_errors,
    uncovered,
    with_history,
)

from r4.companion.session_models import SessionMessage
from r4.companion.ui import rpc_schema
from r4.companion.ui.bridge_server import (
    BRIDGE_PROTOCOL_VERSION,
    BRIDGE_RPC_NAMES,
    _project_runtime,
    _venue_runtime_projection,
)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def _fake_turn(h: Harness) -> None:
    def send(text: str, **_kw: Any) -> str:
        h.session._current_turn_id = "t9"
        h.session.messages.append(SessionMessage(role="user", content=text))
        h.session.messages.append(SessionMessage(role="assistant", content="answer, see /help"))
        return "answer, see /help"

    h.session.send = send


def _go_remote(h: Harness) -> None:
    h.session.metadata["venue"] = "frontend"
    _project_runtime(h.session, _venue_runtime_projection(VENUE_ROW))
    h.client.answers["/queue"] = {
        "applied": True,
        "queue": [{"id": "q1", "content": "hi"}],
        "front": 1,
    }


def _venue_answers(h: Harness) -> None:
    h.client.answers["/venue"] = dict(VENUE_ROW)
    h.client.answers["/venue/start"] = dict(VENUE_ROW)
    h.client.answers["/venue/stop"] = dict(VENUE_ROW, state="stopped")
    h.client.answers[f"/sessions/{h.sid}"] = {"session_id": h.sid, "payload": {}}
    h.client.answers["/approvals"] = {"approvals": [{"approval_id": "a1", "status": "pending"}]}
    h.client.answers["/decision"] = {"approval": {"approval_id": "a1", "status": "approved"}}
    # ADR-099 D-8: remote ask_user questions.
    h.client.answers["/questions"] = {"questions": [{"question_id": "q1", "status": "pending"}]}
    h.client.answers["/answer"] = {"question": {"question_id": "q1", "status": "answered"}}


def _workflow_export(h: Harness) -> dict[str, Any]:
    """workflow.export into the harness tmp dir; the fake SDK writes a real zip."""

    def export(name: str, dest: Path, *, overwrite: bool = False) -> Path:
        assert not overwrite and not Path(dest).exists()
        manifest = {"workflow": name, "files": [{"path": "a.py"}], "excluded": [".env"]}
        with zipfile.ZipFile(dest, "w") as archive:
            archive.writestr("EXPORT.json", json.dumps(manifest))
        return Path(dest)

    h.client.workflows.export = export
    return {"workflow_name": "wf", "dest_dir": str(Path(h.store.db_path).parent)}


def _umrs_answers(h: Harness) -> None:
    h.client.http.answers["rag/stats"] = {"documents": 3}
    h.client.http.answers["knowledge/corpus-attrition-report"] = {
        "reports": {"projects": {"demo": {"dropped": 0}}}
    }
    h.client.http.answers["provenance/job-1"] = {
        "job_id": "job-1",
        "job_type": "workflow",
        "project_id": "demo",
        "user_id": "u1",
        "status": "completed",
        "attempts": [],
        "total_attempts": 0,
    }
    h.client.http.answers["provenance/job-1/lineage"] = {
        "job_id": "job-1",
        "execution_id": None,
        "root_ids": ["tr1"],
        "leaf_ids": ["tr1"],
        "nodes": [
            {
                "trace_id": "tr1",
                "step_name": "s",
                "step_type": "task",
                "status": "ok",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
            }
        ],
        "edges": [{"source": "tr1", "target": "tr1"}],
        "metrics": {"total_nodes": 1},
    }


class _ReadyBackend:
    """A sandbox backend that probes fine; nothing is ever run under it."""

    name = "bwrap"

    def probe(self) -> tuple[bool, str]:
        return True, ""

    def build_argv(self, command: Any, policy: Any, cwd: Any) -> list[str]:
        return list(command)


def _sandbox_ready(h: Harness) -> dict[str, Any]:
    from r4.companion.sandbox import SandboxContext

    context = SandboxContext(
        setting=h.session.sandbox_setting,
        web_access=h.session.web_access,
        backend=_ReadyBackend(),
    )
    context.check_available = lambda policy=None: None  # type: ignore[method-assign]
    h.session._sandbox_context = context
    return {"mode": "on"}


def _search_server_busy(h: Harness) -> dict[str, Any]:
    h.server._search_server_starting = True
    return {}


def _send_local(h: Harness) -> dict[str, Any]:
    _fake_turn(h)
    return {"text": "hello"}


def _send_remote(h: Harness) -> dict[str, Any]:
    _go_remote(h)
    return {"text": "hello"}


def _confirm_plan(h: Harness) -> dict[str, Any]:
    h.session.confirm_plan = lambda action, **_kw: f"plan {action}"
    return {"action": "approve"}


def _session_id(h: Harness) -> dict[str, Any]:
    return {"session_id": h.sid}


def _rename(h: Harness) -> dict[str, Any]:
    return {"session_id": h.sid, "name": "renamed"}


def _compact_changed(h: Harness) -> dict[str, Any]:
    with_history(h)
    h.session.context.compact = lambda payload, keep_recent: payload[-keep_recent:]
    return {"keep_recent": 2}


def _truncate(h: Harness) -> dict[str, Any]:
    with_history(h, turns=3)
    return {"turn_id": "t2"}


def _queued(h: Harness) -> dict[str, Any]:
    h.server.session_queue_append({"content": "later"})
    return {}


def _venue(h: Harness) -> dict[str, Any]:
    _venue_answers(h)
    return {}


def _venue_start(h: Harness) -> dict[str, Any]:
    _venue_answers(h)
    return {"source_refs": ["openai-main"], "web_policy": "public"}


def _venue_decide(h: Harness) -> dict[str, Any]:
    _venue_answers(h)
    return {"approval_id": "a1", "decision": "once"}


def _venue_answer(h: Harness) -> dict[str, Any]:
    _venue_answers(h)
    return {"question_id": "q1", "answer": "yes"}


# ADR-099: the preview route's body (P's GET …/venue/policy).
_POLICY_PREVIEW = {
    "session_id": "s",
    "project_id": "demo",
    "choice_required": True,
    "limits": {"web": {"allow": ["none", "allowlist"], "domains": [".arxiv.org"]}},
    "current": None,
}


def _venue_policy(h: Harness) -> dict[str, Any]:
    h.client.answers["/venue/policy"] = dict(_POLICY_PREVIEW)
    return {"project": "demo"}


def _umrs(params: dict[str, Any]) -> Setup:
    def setup(h: Harness) -> dict[str, Any]:
        _umrs_answers(h)
        return params

    return setup


def _first_sweep_template() -> str:
    from r4.sweep_templates import list_templates

    return list_templates()[0].name


def _one_event(h: Harness) -> dict[str, Any]:
    h.server._event_log.append(
        session_id=h.sid,
        event_type="probe",
        message="one",
        data={"k": 1},
        tool_name="probe_tool",
        tool_status="ok",
    )
    return {}


def _registered_agent(h: Harness) -> dict[str, Any]:
    agents = run_verb(h, "list_active_agents", plain({}))["agents"]
    return {"agent_id": agents[0]["agent_id"] if agents else "main"}


def _governance_ok(h: Harness) -> dict[str, Any]:
    h.client.governance_ownership = lambda: {"owned": ["m1"]}
    h.client.governance_quotas = lambda: {"quota": 1}
    h.client.audit_trace = lambda limit=0: {"rows": []}
    return {}


def _governance_error(h: Harness) -> dict[str, Any]:
    def _down() -> Any:
        raise RuntimeError("offline")

    h.client.governance_ownership = _down
    h.client.governance_quotas = _down
    h.client.audit_trace = _down
    return {}


def _science_iteration(h: Harness) -> dict[str, Any]:
    run_verb(h, "record_science_iteration", plain({"hypothesis": "h1", "reward_total": 0.5}))
    return {}


CASES: list[Case] = [
    ("get_state", "fresh", plain({})),
    ("get_state", "history", lambda h: (with_history(h), {})[1]),
    ("send_message", "local", _send_local),
    ("send_message", "remote", _send_remote),
    ("confirm_plan", "keep_planning", plain({"action": "keep_planning"})),
    ("confirm_plan", "approve", _confirm_plan),
    ("dispatch_slash_command", "sessions", plain({"command": "/sessions"})),
    ("dispatch_slash_command", "help", plain({"command": "/help"})),
    ("session.pin", "pin", _session_id),
    ("session.unpin", "unpin", _session_id),
    ("session.fork", "fork", _session_id),
    ("session.rename", "rename", _rename),
    ("session.archive", "archive", _session_id),
    ("session.compact", "noop", plain({})),
    ("session.compact", "changed", _compact_changed),
    ("session.truncate", "cut", _truncate),
    ("session.side_question", "btw", plain({"question": ""})),
    ("session.turns", "all", _session_id),
    ("session.turns", "one", lambda h: {"session_id": h.sid, "turn_id": "t1"}),
    ("session.queue_append", "append", plain({"content": "next"})),
    ("session.queue_append", "empty", plain({"content": ""})),
    ("session.queue_pop", "pop", _queued),
    ("session.queue_pop", "empty", plain({})),
    ("session.queue_clear", "clear", _queued),
    (
        "session.queue_remote",
        "append",
        lambda h: (_go_remote(h), {"action": "append", "content": "x"})[1],
    ),
    ("session.venue_start", "start", _venue_start),
    ("session.venue_status", "status", _venue),
    ("session.venue_stop", "stop", _venue),
    ("session.venue_sync", "sync", _venue),
    ("session.venue_approvals", "pending", _venue),
    ("session.venue_decide", "once", _venue_decide),
    ("session.venue_questions", "pending", _venue),
    ("session.venue_answer", "answer", _venue_answer),
    ("session.web_access_get", "unchosen", plain({})),
    (
        "session.web_access_set",
        "allowlist",
        plain({"mode": "allowlist", "domains": [".arxiv.org"]}),
    ),
    ("session.web_access_set", "off", plain({"mode": "off"})),
    ("session.web_access_set", "public", plain({"mode": "public"})),
    ("search_server.status", "absent", plain({})),
    ("search_server.up", "accepted", plain({})),
    ("search_server.up", "busy", _search_server_busy),
    ("search_server.down", "stopped", plain({})),
    ("session.sandbox_get", "local", plain({})),
    ("session.sandbox_get", "remote", lambda h: (_go_remote(h), {})[1]),
    ("session.sandbox_set", "off", plain({"mode": "off"})),
    ("session.sandbox_set", "on", _sandbox_ready),
    ("session.venue_policy", "preview", _venue_policy),
    ("session.venue_policy", "no_preview", plain({})),
    ("workflow.list", "list", plain({})),
    ("workflow.status", "status", plain({"execution_id": "ex-1"})),
    ("workflow.run", "run", plain({"workflow_name": "wf"})),
    ("workflow.cancel", "cancel", plain({"execution_id": "ex-1"})),
    ("workflow.results", "results", plain({"execution_id": "ex-1"})),
    ("workflow.detail", "detail", plain({"execution_id": "ex-1"})),
    ("workflow.approve_gate", "approve", plain({"execution_id": "ex-1", "step_name": "gate"})),
    ("workflow.package", "package", plain({"workflow_name": "wf"})),
    ("workflow.export", "saved", _workflow_export),
    ("workflow_describe", "describe", plain({"workflow_name": "wf"})),
    ("assets.snapshot", "snapshot", plain({})),
    ("assets.patch", "patch", plain({"base": {"dataset:d1": "x"}})),
    ("admin.rag_stats", "stats", _umrs({})),
    ("admin.corpus_attrition", "report", _umrs({"project_id": "demo"})),
    ("provenance.job", "job", _umrs({"job_id": "job-1"})),
    ("provenance.lineage", "lineage", _umrs({"job_id": "job-1"})),
    ("sweep.templates", "pack", plain({})),
    ("sweep.preview", "first", lambda _h: {"name": _first_sweep_template()}),
    ("trust_list", "rows", plain({})),
    ("trust_grant", "grant", plain({"tool_name": "bash", "scope": "project:demo"})),
    # ── get/status leftovers (H-4 final batch) ──────────────────────────────
    ("get_status", "degraded", plain({})),
    ("get_backend_dashboard", "snapshot", plain({})),
    ("get_code_context", "repo", plain({"path": "."})),
    ("get_welcome", "banner", plain({})),
    ("get_usage", "empty", plain({})),
    ("get_event_log", "empty", plain({})),
    ("get_event_log", "one", _one_event),
    ("get_governance_overview", "sections_error", _governance_error),
    ("get_governance_overview", "sections_ok", _governance_ok),
    ("get_resources", "snapshot", plain({})),
    ("get_artifacts", "empty", plain({})),
    ("list_active_agents", "main_agent", plain({})),
    ("get_agent_tree", "tree", plain({})),
    ("get_agent_status", "registered", _registered_agent),
    ("get_agent_status", "unknown", plain({"agent_id": "nope"})),
    ("get_science_loop", "empty", plain({})),
    ("get_science_loop", "trajectory", _science_iteration),
    ("record_science_iteration", "recorded", plain({"hypothesis": "h1", "reward_total": 0.5})),
    ("list_slash_commands", "catalog", plain({})),
]


@pytest.fixture(autouse=True)
def _stub_search_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic: no container engine is ever run, no start thread waits."""
    from r4.agent import searxng_local

    monkeypatch.setattr(searxng_local, "_run", lambda _command, _timeout: None)
    monkeypatch.setattr(searxng_local, "up", lambda progress=None: {"state": "running"})


@pytest.fixture(autouse=True)
def _stub_side_question(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.companion import side_question

    monkeypatch.setattr(side_question, "answer_side_question", lambda _s, _q: "no question")


@pytest.mark.parametrize(("method", "label", "setup"), CASES, ids=case_ids(CASES))
def test_handler_output_matches_schema(
    harness: Harness, method: str, label: str, setup: Setup
) -> None:
    _ = label
    assert schema_errors(harness, method, setup) == []


def test_every_core_verb_has_a_case_and_every_covered_verb_is_served() -> None:
    assert uncovered(rpc_schema.CORE_RESULT_TYPES, CASES) == set()
    assert set(rpc_schema.RPC_RESULT_TYPES) - BRIDGE_RPC_NAMES == set()


def test_schema_version_tracks_the_bridge_protocol() -> None:
    assert rpc_schema.RPC_SCHEMA_VERSION == BRIDGE_PROTOCOL_VERSION


def test_validator_reports_a_missing_key_and_a_wrong_type() -> None:
    errors = rpc_schema.validate_result("session.pin", {"session_id": 7})
    assert "$.session_id: expected string" in errors
    assert "$: missing key 'pinned'" in errors


def test_validator_follows_refs_into_nested_rows() -> None:
    bad = {
        "templates": [{"name": "x", "description": "", "required_params": "lattice", "params": []}],
        "count": 1,
    }
    assert rpc_schema.validate_result("sweep.templates", bad) == [
        "$.templates[0].required_params: expected array"
    ]


def test_absent_keys_are_optional_and_null_keys_required() -> None:
    schema = rpc_schema.result_schema("send_message")["$defs"]["SendMessageResult"]
    assert schema["required"] == ["response", "state"]
    pop = rpc_schema.result_schema("session.queue_pop")["$defs"]["SessionQueuePopResult"]
    assert pop["required"] == ["content", "remaining"]
    assert {"type": "null"} in pop["properties"]["content"]["anyOf"]


def test_wire_names_replace_python_keywords() -> None:
    props = rpc_schema.result_schema("session.compact")["$defs"]["SessionCompactResult"][
        "properties"
    ]
    assert "from" in props and "from_count" not in props
