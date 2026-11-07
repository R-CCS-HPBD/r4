"""Bridge RPC result contract, research family (plan H-4.5).

Each verb in ``rpc_schema_research.RESEARCH_RESULT_TYPES`` runs through its
real handler (``_bridge_rpc_bindings``) on the shared harness
(``bridge_rpc_harness``: real store and session, network faked); its output
must validate against the verb's exported JSON Schema.

Lane pinning: ``R4_RUNTIME_MODE`` is set per case (restored by the autouse
fixture) — with no env or project metadata the resolver defaults to umrs.
The project lifecycle verbs spawn real ``r4 project …`` subprocesses; those
are the verb's own mechanism, kept hermetic by a fake ``Popen`` bound only
inside ``project_bridge`` (the reply — jobId/operation — is what the
contract covers).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from bridge_rpc_harness import (
    Case,
    Harness,
    Setup,
    case_ids,
    make_harness,
    plain,
    run_verb,
    schema_errors,
    uncovered,
)

from r4.companion.ui.rpc_schema_research import RESEARCH_RESULT_TYPES

_ORIGINALS: dict[tuple[Any, str], Any] = {}
_ENV_KEYS = ("R4_RUNTIME_MODE", "R4_PROJECT_ID")


@pytest.fixture(autouse=True)
def _restore_edits() -> Iterator[None]:
    saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    yield
    for (module, attr), original in _ORIGINALS.items():
        setattr(module, attr, original)
    _ORIGINALS.clear()
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _replace(module: Any, attr: str, value: Any) -> None:
    """Module-attribute replacement restored by the autouse fixture."""
    _ORIGINALS.setdefault((module, attr), getattr(module, attr))
    setattr(module, attr, value)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def _env(mode: str) -> None:
    os.environ["R4_RUNTIME_MODE"] = mode


def _trace_row() -> dict[str, Any]:
    """Full ``TraceRecord.to_dict`` (the route emits every key, None-able or not)."""
    row: dict[str, Any] = {
        "trace_id": "t1",
        "job_id": "j1",
        "step_name": "step",
        "step_type": "generic",
        "attempt_id": None,
        "execution_id": "ex-1",
        "parent_trace_id": None,
        "status": "succeeded",
        "progress": 1.0,
        "inputs_hash": None,
        "outputs_hash": None,
        "inputs_ref": None,
        "outputs_ref": None,
        "model_ref": None,
        "artifact_refs": [],
        "tool_calls": [],
        "retrieval_refs": [],
        "routing_decision": None,
        "error": None,
        "error_detail": None,
        "inputs_snapshot": None,
        "outputs_snapshot": None,
        "started_at": "2026-10-18T00:00:00+00:00",
        "finished_at": "2026-10-18T00:01:00+00:00",
        "created_at": "2026-10-18T00:00:00+00:00",
        "ttl_expires_at": None,
    }
    return row


def _loop_row() -> dict[str, Any]:
    return {
        "loop_id": "l1",
        "project_id": "demo",
        "research_goal": "goal",
        "status": "running",
        "phase": "hypothesis",
        "current_iteration": 2,
        "max_iterations": 10,
        "total_hypotheses_tested": 2,
        "total_experiments_run": 1,
        "total_findings": 1,
        "success_rate": 0.5,
        "started_at": "2026-10-18T00:00:00+00:00",
        "completed_at": "",
        "metadata": {},
    }


def _loop_wire_row() -> dict[str, Any]:
    """The UMRS `/loops` listing wire shape (5 keys; the T5 rail passes it through)."""
    return {
        "loop_id": "l1",
        "status": "running",
        "phase": "hypothesis",
        "companion_session_id": "",
        "project_id": "demo",
    }


def _umrs_loops(h: Harness) -> None:
    """All UMRS loop facades the loops.* cases ride."""
    h.client.get_loop_status = lambda _id, **k: dict(_loop_row())
    h.client.list_loop_iterations = lambda _id, **k: {"iterations": [], "count": 0}
    h.client.stop_loop = lambda _id, **k: {"loop_id": _id, "status": "stopped", "message": "m"}
    h.client.pause_loop = lambda _id, **k: {"loop_id": _id, "status": "paused", "message": "m"}
    h.client.resume_loop = lambda _id, **k: {
        "loop_id": _id,
        "project_id": "demo",
        "status": "running",
        "phase": "hypothesis",
        "message": "m",
        "workflow_name": "wf",
    }


def _loops_list_case(h: Harness) -> dict[str, Any]:
    _env("umrs")
    h.client.list_loops = lambda **k: {"loops": [_loop_wire_row()], "count": 1}
    return {}


def _umrs_runtime(h: Harness) -> None:
    """LoopState-mappable rows on the UMRS runtime facades."""
    h.client.list_loops = lambda **k: {"loops": [_loop_row()], "count": 1}
    _umrs_loops(h)


def _control_case(verb: str) -> Setup:
    def setup(h: Harness) -> dict[str, Any]:
        _env("umrs")
        _umrs_loops(h)
        return {"loop_id": "l1"}

    setup.__name__ = f"_{verb.replace('.', '_')}_case"
    return setup


class _NotFound404(Exception):
    status_code = 404


def _research_status_not_found(h: Harness) -> dict[str, Any]:
    _env("umrs")

    def _missing(_id: str, **k: Any) -> Any:
        raise _NotFound404("hidden")

    h.client.get_loop_status = _missing
    return {"loop_id": "hidden"}


def _research_status_found(h: Harness) -> dict[str, Any]:
    _env("umrs")
    _umrs_runtime(h)
    return {"loop_id": "l1"}


def _research_list(h: Harness) -> dict[str, Any]:
    return _loops_list_case(h)


def _knowledge_stub(h: Harness) -> None:
    h.client.knowledge = SimpleNamespace(
        get_graph=lambda _pid: {"nodes": [{"id": "n1"}], "edges": []},
        graph_ingest=lambda _g, project_id=None, files=None, urls=None: {"ingested": 1},
        write_constraints=None,
    )


def _session_view(h: Harness) -> dict[str, Any]:
    _env("umrs")
    _umrs_runtime(h)
    _knowledge_stub(h)
    h.client.list_research_sessions = lambda _pid: {
        "sessions": [{"session_id": "s1", "title": "t", "status": "active", "loops": []}]
    }
    return {}


def _session_view_empty(h: Harness) -> dict[str, Any]:
    _env("umrs")
    _umrs_runtime(h)
    return {}


def _knowledge_status(h: Harness) -> dict[str, Any]:
    _env("umrs")
    _umrs_runtime(h)
    _knowledge_stub(h)
    return {}


def _rag_answer(h: Harness) -> dict[str, Any]:
    h.client.http.answers["rag/query"] = {
        "answer": "Because attention.",
        "sources": [{"document_id": "d1", "content": "passage", "score": 0.9, "metadata": {}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "metadata": {"sufficiency": "sufficient"},
    }
    return {"query": "why attention?"}


def _script(suffix: str, payload: Any) -> Setup:
    return lambda h: (h.client.http.answers.__setitem__(suffix, payload), {})[1]


def _traces_get(h: Harness) -> dict[str, Any]:
    h.client.http.answers["traces/t1"] = _trace_row()
    return {"trace_id": "t1"}


def _trace_list(suffix: str, param: str) -> Setup:
    def setup(h: Harness) -> dict[str, Any]:
        h.client.http.answers[suffix] = {"traces": [_trace_row()], "count": 1}
        return {param: "x1"}

    return setup


def _project_seed(h: Harness, project_id: str = "demo-proj") -> str:
    run_verb(h, "project_create", plain({"project_id": project_id, "name": "Demo"}))
    return project_id


def _register_ok(h: Harness) -> dict[str, Any]:
    """POST /gw/projects answered: the row comes back owned by the caller."""
    h.client.create_platform_project = lambda project_id, name="": {  # type: ignore[attr-defined]
        "status": "ok",
        "project": {"project_id": project_id, "name": name, "owner_id": "alice"},
        "created": True,
    }
    return {"project_id": "p-reg", "name": "Registered"}


def _register_refused(h: Harness) -> dict[str, Any]:
    from r4.errors import APIError

    def refuse(project_id: str, name: str = "") -> dict[str, Any]:
        raise APIError("project id is taken", status_code=409)

    h.client.create_platform_project = refuse  # type: ignore[attr-defined]
    return {"project_id": "p-taken"}


def _propose_switch(h: Harness) -> dict[str, Any]:
    _project_seed(h, "proj-a")
    _project_seed(h, "proj-b")
    return {"project_id": "proj-a"}


def _delete_case(h: Harness) -> dict[str, Any]:
    return {"project_id": _project_seed(h)}


def _fake_popen(h: Harness) -> None:
    """Lifecycle verbs spawn `r4 project …` threads; keep them hermetic."""
    from r4.companion.ui import project_bridge

    class _Proc:
        returncode = 0

        def communicate(self) -> tuple[str, str]:
            return ('{"done": true}', "")

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float = 0.0) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    _replace(project_bridge, "subprocess", SimpleNamespace(Popen=lambda *a, **k: _Proc()))


def _job_case(cli_params: dict[str, Any]) -> Setup:
    def setup(h: Harness) -> dict[str, Any]:
        _fake_popen(h)
        return cli_params

    return setup


def _unknown_job(h: Harness) -> dict[str, Any]:
    return {"jobId": "no-such"}


def _pk_status(h: Harness) -> dict[str, Any]:
    import r4._cli.project_knowledge as pk

    _replace(pk, "project_status", lambda ns, pm: print('{"ready": true}'))
    return {"project_id": "demo"}


def _pk_findings(h: Harness) -> dict[str, Any]:
    import r4._cli.project_knowledge as pk

    _replace(pk, "project_findings", lambda ns, pm: print('{"findings": 2}'))
    return {"project_id": "demo"}


def _ingest_case(h: Harness) -> dict[str, Any]:
    _knowledge_stub(h)
    return {"project_id": "demo", "graph": "g1", "files": ["/tmp/a.txt"]}


def _session_create_case(h: Harness) -> dict[str, Any]:
    h.client.create_research_session = lambda pid, goal, title="": {
        "session": {"session_id": "s1", "project_id": pid, "goal": goal}
    }
    return {"project_id": "demo", "goal": "alloy X"}


def _graph_content_case(h: Harness) -> dict[str, Any]:
    h.client.knowledge = SimpleNamespace(
        graph_content=lambda _id, node_limit=0, edge_limit=0: {
            "graph_id": "g1",
            "name": "Graph",
            "kind": "research",
            "project_id": "demo",
            "owner_id": "u1",
            "nodes": [
                {
                    "id": "n1",
                    "node_id": "n1",
                    "name": "n",
                    "label": "n",
                    "type": "knowledge",
                    "project_id": "demo",
                    "graph_id": "g1",
                    "created_at": None,
                }
            ],
            "edges": [
                {
                    "from": "n1",
                    "to": "n2",
                    "label": "rel",
                    "type": "rel",
                    "graph_id": "g1",
                    "created_at": None,
                }
            ],
            "truncated": False,
        }
    )
    return {"graph_id": "g1"}


def _knowledge_populated(h: Harness) -> dict[str, Any]:
    run_verb(h, "add_pattern", plain({"title": "pat", "confidence": 0.7}))
    run_verb(h, "add_lesson", plain({"lesson": "less", "category": "lab"}))
    return {}


def _circles_case(h: Harness) -> dict[str, Any]:
    h.client.list_my_circles = lambda include_retired=False: {
        "circles": [
            {
                "project_id": "demo",
                "circle_id": "c1",
                "name": "cell",
                "owner_id": "u1",
                "status": "active",
                "retired_at": None,
                "created_at": "2026-10-18T00:00:00+00:00",
                "updated_at": None,
            }
        ],
        "count": 1,
    }
    return {}


def _members_case(h: Harness) -> dict[str, Any]:
    h.client.list_circle_members = lambda _pid, _cid: {
        "members": [{"user_id": "u2", "role": "member", "added_by": "u1", "added_at": None}]
    }
    return {"project_id": "demo", "circle_id": "c1"}


def _circle_row() -> dict[str, Any]:
    return {
        "project_id": "demo",
        "circle_id": "c1",
        "name": "cell",
        "owner_id": "u1",
        "status": "active",
        "retired_at": None,
        "created_at": "2026-10-19T00:00:00+00:00",
        "updated_at": None,
    }


def _circle_create_case(h: Harness) -> dict[str, Any]:
    h.client.create_circle = lambda _pid, _cid, name="": {
        "status": "ok",
        "circle": _circle_row(),
        "created": True,
    }
    return {"project_id": "demo", "circle_id": "c1", "name": "cell"}


def _member_add_case(h: Harness) -> dict[str, Any]:
    h.client.add_circle_member = lambda _pid, _cid, _uid, role="member": {
        "status": "ok",
        "circle": _circle_row(),
        "members": [{"user_id": "u2", "role": role, "added_by": "u1", "added_at": None}],
    }
    return {"project_id": "demo", "circle_id": "c1", "user_id": "u2"}


def _member_remove_case(h: Harness) -> dict[str, Any]:
    h.client.remove_circle_member = lambda _pid, _cid, _uid: {
        "status": "ok",
        "project_id": "demo",
        "circle_id": "c1",
        "members": [{"user_id": "u1", "role": "admin", "added_by": "u1", "added_at": None}],
    }
    return {"project_id": "demo", "circle_id": "c1", "user_id": "u2"}


def _grants_case(h: Harness) -> dict[str, Any]:
    h.client.list_my_grants = lambda: {
        "grants": [
            {
                "grant_id": "g1",
                "asset_kind": "model",
                "asset_id": "m1",
                "grantee_kind": "user",
                "grantee_id": "u1",
                "version_pin": None,
                "expires_at": None,
                "tombstoned_at": None,
                "created_by": "admin",
                "created_at": "2026-10-18T00:00:00+00:00",
            }
        ]
    }
    return {}


def _citation_open_case(h: Harness) -> dict[str, Any]:
    from r4_contracts.retrieval import RetrievedPassage

    from r4.companion.tool_evidence import ToolEvidence

    passage = RetrievedPassage(
        rank=1, source_id="node-1", artifact_id="art-1", source_name="a.pdf", text="text"
    )
    ToolEvidence(h.session).label([passage], project_id="demo")
    h.client.rag = SimpleNamespace(
        fetch_passage=lambda node_id, **_kw: {
            "source_id": node_id,
            "artifact_id": "art-1",
            "source_name": "a.pdf",
            "text": "before text after",
            "span": [0, 17],
            "window": 1,
            "chunks": [0, 2],
            "text_source": "artifact",
        }
    )
    return {"sid": "S1", "window": 1}


def _knowledge_save_case(h: Harness) -> dict[str, Any]:
    from bridge_rpc_harness import with_history

    with_history(h, turns=2)
    h.client.knowledge = SimpleNamespace(
        save_session_turns=lambda *_a, **_kw: {"job_id": "job-k16", "status": "queued"},
        write_constraints={"mode": "open", "graphs": []},
    )
    return {"turn": "t2", "part": "answer"}


def _evidence_case(h: Harness) -> dict[str, Any]:
    h.client.list_evidence = lambda status=None, limit=0: {
        "evidence": [
            {
                "evidence_id": "e1",
                "project_id": "demo",
                "claim_id": "c1",
                "source_type": "document",
                "source_id": "s1",
                "content": "text",
                "content_uri": None,
                "relevance_score": 0.8,
                "citation": None,
                "status": "active",
                "created_at": "2026-10-18T00:00:00+00:00",
                "updated_at": "2026-10-18T00:00:00+00:00",
            }
        ]
    }
    return {}


def _usage_case(h: Harness) -> dict[str, Any]:
    h.client.http.answers["usage/me"] = {
        "user_id": "u1",
        "rows": [{"model": "gpt-x", "tokens": 3}],
    }
    return {}


def _quota_case(h: Harness) -> dict[str, Any]:
    h.client.quota_units = lambda: {
        "user_id": "u1",
        "cpu_process": {"current": 1, "limit": 4},
        "disk_bytes": {"current": 10, "limit": 100},
    }
    return {}


def _checkpoint_seed(h: Harness) -> str:
    return str(run_verb(h, "save_checkpoint", plain({"state": {"step": 2}}))["checkpoint_id"])


CASES: list[Case] = [
    ("project_list", "rows", lambda h: (_project_seed(h), {})[1]),
    ("project_current", "selected", lambda h: (_project_seed(h), {})[1]),
    ("project_current", "unset", plain({})),
    ("project_create", "new", plain({"project_id": "p-new", "name": "New"})),
    ("project_register", "registered", _register_ok),
    ("project_register", "refused", _register_refused),
    ("project_switch", "changes", _propose_switch),
    ("project_delete", "deleted", _delete_case),
    ("project_import", "started", _job_case({"project_id": "demo", "paths": ["/tmp/a"]})),
    ("project_init", "started", _job_case({"project_id": "demo"})),
    ("project_verify", "started", _job_case({"project_id": "demo", "question": "q"})),
    ("project_readiness", "started", _job_case({"project_id": "demo"})),
    ("project_plan", "started", _job_case({"project_id": "demo", "research_goal": "g"})),
    ("project_run", "started", _job_case({"project_id": "demo", "simulate": True})),
    ("project_cancel_job", "unknown", _unknown_job),
    ("project_status", "summary", _pk_status),
    ("project_findings", "summary", _pk_findings),
    ("project_ingest", "files", _ingest_case),
    ("project_session_create", "created", _session_create_case),
    ("loops.list", "one", _loops_list_case),
    ("loops.status", "umrs", _control_case("loops.status")),
    ("loops.stop", "umrs", _control_case("loops.stop")),
    ("loops.pause", "umrs", _control_case("loops.pause")),
    ("loops.resume", "umrs", _control_case("loops.resume")),
    ("traces.get", "row", _traces_get),
    ("traces.job", "list", _trace_list("traces/job/j1", "job_id")),
    ("traces.execution", "list", _trace_list("traces/execution/e1", "execution_id")),
    ("research_loop.list", "one", _research_list),
    ("research_loop.status", "found", _research_status_found),
    ("research_loop.status", "not_found", _research_status_not_found),
    ("research_session.view", "full", _session_view),
    ("research_session.view", "empty", _session_view_empty),
    ("research_knowledge.status", "digest", _knowledge_status),
    ("rag.query", "answered", _rag_answer),
    ("get_knowledge", "empty", plain({})),
    ("get_knowledge", "populated", _knowledge_populated),
    ("add_pattern", "created", plain({"title": "pat", "confidence": 0.7})),
    ("add_anti_pattern", "created", plain({"title": "anti"})),
    ("add_lesson", "created", plain({"lesson": "less"})),
    ("knowledge_graph_content", "graph", _graph_content_case),
    ("circles.list", "mine", _circles_case),
    ("circles.members", "roster", _members_case),
    ("circles.create", "ensured", _circle_create_case),
    ("circles.member_add", "roster", _member_add_case),
    ("circles.member_remove", "roster", _member_remove_case),
    ("grants.mine", "one", _grants_case),
    ("usage.me", "rows", _usage_case),
    ("quota.units", "gauge", _quota_case),
    ("evidence.list", "rows", _evidence_case),
    ("citation.open", "passage", _citation_open_case),
    ("knowledge.save", "kept", _knowledge_save_case),
    ("save_checkpoint", "saved", plain({"state": {"step": 1}})),
    ("list_checkpoints", "one", lambda h: (_checkpoint_seed(h), {})[1]),
    ("load_latest_checkpoint", "state", lambda h: (_checkpoint_seed(h), {})[1]),
]


@pytest.mark.parametrize(("method", "label", "setup"), CASES, ids=case_ids(CASES))
def test_handler_output_matches_schema(
    harness: Harness, method: str, label: str, setup: Setup
) -> None:
    _ = label
    assert schema_errors(harness, method, setup) == []


def test_every_research_verb_has_a_case() -> None:
    assert uncovered(RESEARCH_RESULT_TYPES, CASES) == set()
