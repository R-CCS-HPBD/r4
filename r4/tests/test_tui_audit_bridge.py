"""TUI audit (bridge lane, 2026-10-17): bridge RPCs match the routes they call.

Each test pins one confirmed audit finding (ids in the test names' docstrings):
the catalog exports Python aliases, rag.query and the admin verbs scope to
the session project, session.turns reads the companion turn index, the
assets patch lane reads the UMRS field names, and session state (plan card,
chat queue, file-change registry) stays honest across turns and resumes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion import tools
from r4.companion.permissions import PermissionManager
from r4.companion.session_models import UsageSummary
from r4.companion.skills import SkillRegistry
from r4.companion.ui import bridge_server
from r4.companion.ui.bridge_server import CompanionBridgeServer
from r4.jobs import Job, JobAttempt


class _FakeStore:
    def __init__(self, *sessions):
        self.sessions = {s.session_id: s for s in sessions}
        self.first = sessions[0]

    def create_session(self, client, name=None, *, model=None):
        self.first.client = client
        return self.first

    def load(self, session_id, client):
        session = self.sessions.get(session_id)
        if session is not None:
            session.client = client
        return session

    def save(self, session):
        return None

    def list_sessions(self, limit=50):
        return []


def _client(project_id: str = "proj-a") -> SimpleNamespace:
    config = SimpleNamespace(
        profile="research",
        gateway_url="http://gateway.test",
        project_id=project_id,
        model="config-model",
        endpoint_url=None,
        endpoint_provider=None,
    )
    return SimpleNamespace(
        config=config,
        _config=config,
        _api_prefix="/gw",
        http=MagicMock(),
        readiness=MagicMock(return_value={"ready": True}),
        list_endpoints=MagicMock(return_value={"endpoints": []}),
    )


def _session(session_id: str = "comp-1", **metadata) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        name="Demo",
        client=None,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        agent=SimpleNamespace(model="test-model"),
        messages=[],
        skills=SkillRegistry(),
        permissions=PermissionManager(),
        context=SimpleNamespace(_memory={}),
        usage_summary=UsageSummary(),
        active_endpoints=[],
        metadata=dict(metadata),
        _harness=None,
        send=MagicMock(return_value="answer"),
        request_turn_cancel=MagicMock(),
        usage_report=MagicMock(return_value={"total_tokens": 0}),
    )


def _bridge(*sessions, project_id: str = "proj-a") -> CompanionBridgeServer:
    client = _client(project_id)
    return CompanionBridgeServer(client=client, store=_FakeStore(*(sessions or (_session(),))))


# -- ids 0 / 28 / 36: Python aliases reach the v3 catalog -------------------


def _catalog() -> dict[str, dict]:
    rows = CompanionBridgeServer().list_slash_commands({})["commands"]
    return {row["name"]: row for row in rows}


def test_catalog_exports_registry_aliases():
    catalog = _catalog()
    assert {"status", "refresh"} <= set(catalog["dashboard"]["aliases"])
    assert catalog["memory"]["aliases"] == ["mem"]
    assert catalog["trust"]["aliases"] == ["yolo"]
    assert catalog["pipeline"]["aliases"] == ["pipelines"]
    assert catalog["endpoint"]["aliases"] == ["endpoints"]


def test_catalog_leaves_out_aliases_v3_owns_locally():
    exported = {a for row in _catalog().values() for a in row.get("aliases", [])}
    # v3 runs these rows (or their canonical command) itself; an exported
    # alias would land on the local row and drop the typed arguments.
    for owned in ("h", "?", "q", "quit", "model", "skill", "decision", "gov", "traces"):
        assert owned not in exported
    for shadowed in ("kb", "ckpt", "resume"):
        assert shadowed not in exported
    # an alias that is already its own catalog row is not repeated
    assert "templates" not in exported


_REPO = Path(__file__).resolve().parents[2]
_V3_MANIFEST = _REPO / "packages" / "r4-tui-v3" / "src" / "commands" / "catalog.manifest.json"
_REGEN = _REPO / "scripts" / "regen-slash-fixture.py"


def _v3_local_rows() -> tuple[set[str], set[str]]:
    """(every v3 row name and alias, the catalog rows v3 runs in every form),
    read from the slash manifest v3 dispatches by (plan H-5, 2026-10-18;
    this used to regex-parse slash.ts)."""
    rows = json.loads(_V3_MANIFEST.read_text(encoding="utf-8"))["rows"]
    owned = {name for row in rows for name in [row["name"], *row["aliases"]]}
    local = {row["name"] for row in rows if row["owner"] == "local" and row.get("slashRow", True)}
    return owned, local


def test_exported_aliases_never_land_on_a_v3_local_row():
    """The bridge reads the v3 manifest; a new v3 row or a new Python alias
    that collides is left out of the catalog, not routed to the v3 row."""
    owned, runs_locally = _v3_local_rows()
    assert {"help", "quit", "exit", "knowledge", "sessions", "admin"} <= owned
    catalog = _catalog()
    exported = {a for row in catalog.values() for a in row.get("aliases", [])}
    assert exported, "the catalog exports some aliases"
    assert not exported & owned, "alias resolves to a v3 local row"
    carriers = {name for name, row in catalog.items() if row.get("aliases")}
    assert not carriers & runs_locally, "v3 runs this row locally and drops the args"


def test_alias_guard_catches_an_unlisted_v3_owner(monkeypatch):
    monkeypatch.setattr(bridge_server, "V3_SLASH_NAMES", frozenset())
    monkeypatch.setattr(bridge_server, "V3_LOCAL_ROWS", frozenset())
    owned, runs_locally = _v3_local_rows()
    catalog = _catalog()
    exported = {a for row in catalog.values() for a in row.get("aliases", [])}
    carriers = {name for name, row in catalog.items() if row.get("aliases")}
    assert exported & owned or carriers & runs_locally


def test_bridge_ownership_is_the_v3_manifest():
    """Plan H-5: no hand-kept owner list in the bridge; it reads the copy of
    the v3 manifest the r4 wheel ships, byte-identical to the v3 file."""
    copy = Path(bridge_server.__file__).with_name("slash_catalog.manifest.json")
    assert copy.read_bytes() == _V3_MANIFEST.read_bytes()
    rows = json.loads(_V3_MANIFEST.read_text(encoding="utf-8"))["rows"]
    owned, runs_locally = _v3_local_rows()
    assert bridge_server.V3_SLASH_NAMES == owned
    assert bridge_server.V3_LOCAL_ROWS == runs_locally
    omitted = {row["name"] for row in rows if row.get("companionRow") == "omit"}
    assert (
        bridge_server.SURFACE_LOCAL_UI_COMMANDS == omitted == {"clear", "health", "palette", "quit"}
    )


def test_endpoints_alias_stays_a_companion_alias():
    """interceptAliases (v3 serves only the bare form) stay exported."""
    assert "endpoints" not in bridge_server.V3_SLASH_NAMES
    assert _catalog()["endpoint"]["aliases"] == ["endpoints"]


def test_slash_fixture_and_manifest_copy_are_current():
    """scripts/regen-slash-fixture.py --check: the v3 reachability fixture and
    the r4 copy of the manifest match the registry and the v3 manifest."""
    done = subprocess.run(
        [sys.executable, str(_REGEN), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr


# -- ids 48 / 80 / 87 (38 bridge half): rag.query defaults the project ------


def test_rag_query_defaults_project_to_session_profile():
    server = _bridge()
    server.session.client.http.post.return_value = {"answer": "ok"}
    server.rag_query({"query": "what is MOF-5", "top_k": 50})
    body = server.session.client.http.post.call_args.kwargs["json_body"]
    assert body["project_id"] == "proj-a"
    assert body["top_k"] == 20  # RAGQueryRequest caps top_k at 20


def test_rag_query_keeps_explicit_project():
    server = _bridge()
    server.session.client.http.post.return_value = {}
    server.rag_query({"query": "q", "project_id": "proj-b"})
    assert server.session.client.http.post.call_args.kwargs["json_body"]["project_id"] == "proj-b"


# -- ids 56 / 83 / 94: admin stats verbs report the session project only ----


def test_admin_rag_stats_refuses_another_project():
    server = _bridge()
    with pytest.raises(ValueError, match="proj-a"):
        server.admin_rag_stats({"project_id": "proj-b"})
    server.session.client.http.get.assert_not_called()


def test_admin_corpus_attrition_refuses_another_project():
    server = _bridge()
    with pytest.raises(ValueError, match="proj-a"):
        server.admin_corpus_attrition({"project_id": "proj-b"})
    server.session.client.http.post.assert_not_called()


def test_admin_corpus_attrition_unwraps_the_project_report():
    server = _bridge()
    inner = {"project_id": "proj-a", "rows_total": 3, "buckets": {"lt_30d": {"rows": 3}}}
    server.session.client.http.post.return_value = {
        "status": "ok",
        "dwell_rule": "read-only, never delete",
        "reports": {"projects": {"proj-a": inner}},
        "caller_notes": {},
    }
    out = server.admin_corpus_attrition({"project_id": "proj-a"})
    assert out == {"report": inner, "project_id": "proj-a"}


@pytest.mark.parametrize("verb", ["admin_rag_stats", "admin_corpus_attrition"])
def test_admin_report_refuses_a_named_project_without_a_session_project(verb):
    """No session project = no X-Project-ID; UMRS would answer for the
    token's project, shown under the typed label (review of #56/#94)."""
    server = _bridge(project_id="")
    with pytest.raises(ValueError, match="no session project"):
        getattr(server, verb)({"project_id": "proj-b"})
    server.session.client.http.get.assert_not_called()
    server.session.client.http.post.assert_not_called()


def test_admin_corpus_attrition_never_relabels_another_projects_report():
    server = _bridge()
    server.session.client.http.post.return_value = {
        "reports": {"projects": {"proj-z": {"project_id": "proj-z", "rows_total": 9}}},
    }
    with pytest.raises(ValueError, match="no entry for proj-a"):
        server.admin_corpus_attrition({})


# -- id 84: the retired rag-profiles router has no bridge verb ---------------


def test_admin_rag_profiles_verb_is_retired_from_the_bridge():
    from r4.companion.ui.bridge_server import (
        BRIDGE_RPC_NAMES,
        CompanionBridgeServer,
        _bridge_rpc_bindings,
    )

    assert "admin.rag_profiles" not in BRIDGE_RPC_NAMES
    bindings = _bridge_rpc_bindings(CompanionBridgeServer.__new__(CompanionBridgeServer))
    assert "admin.rag_profiles" not in bindings
    assert not hasattr(CompanionBridgeServer, "admin_rag_profiles")


# -- ids 58 / 66 / 75 / 86: session.turns reads the companion turn index ----


def test_client_session_turns_uses_companion_sessions_route():
    from r4.client import Client

    client = Client.__new__(Client)
    client._config = SimpleNamespace(gateway_url="http://gw.test")
    client._is_umrs_direct = False
    client._http_core = MagicMock()
    client.list_session_turns("s1")
    assert client.http.get.call_args.args[0] == "http://gw.test/gw/companion/sessions/s1/turns"
    client.list_session_turns("s1", "t2")
    assert client.http.get.call_args.args[0] == "http://gw.test/gw/companion/sessions/s1/turns/t2"


def test_session_turns_reads_entries_and_single_entry():
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = MagicMock()
    rows = [{"turn_id": "t1", "index": 0, "role": "user"}]
    server._session.client.list_session_turns.return_value = {"entries": rows, "count": 1}
    out = server.session_turns({"session_id": "s1"})
    assert out["rows"] == rows and out["count"] == 1
    server._session.client.list_session_turns.return_value = {"session_id": "s1", "entry": rows[0]}
    out = server.session_turns({"session_id": "s1", "turn_id": "t1"})
    assert out["rows"] == rows and out["count"] == 1


# -- ids 59 / 81: sweep.preview renders the shipped pack --------------------


@pytest.mark.parametrize("name", ["generic-mpi", "llm-bench"])
def test_sweep_preview_renders_every_shipped_template(name):
    out = _bridge().sweep_preview({"name": name})
    for param in out["required_params"]:
        assert f"<{param}>" in out["script"]


def test_sweep_preview_takes_caller_params_over_placeholders():
    out = _bridge().sweep_preview({"name": "llm-bench", "params": {"model": "qwen-7b"}})
    assert "qwen-7b" in out["script"] and "<model>" not in out["script"]


# -- id 70: keep_planning releases the plan card until the next turn --------


def test_keep_planning_hides_the_plan_card_until_the_next_message(monkeypatch):
    import r4.companion.ui.bridge_server as bridge_module

    monkeypatch.setattr(bridge_module, "ensure_model_ready", lambda session: None)
    plan = {"awaiting_confirmation": True, "pending_request": "fit the model"}
    session = _session(interaction_mode="plan", plan_mode_state=plan)
    server = _bridge(session)
    out = server.confirm_plan({"action": "keep_planning"})
    assert out["state"]["session"]["plan_awaiting_confirmation"] is False
    # the plan stays pending server-side: the next message revises it
    assert session.metadata["plan_mode_state"]["awaiting_confirmation"] is True
    seen = {}
    session.send.side_effect = lambda text, **kw: (
        seen.update(state=dict(session.metadata["plan_mode_state"])) or "revised"
    )
    server.send_message({"text": "also add a baseline"})
    assert "revising" not in seen["state"]


# -- id 71: the snapshot chat queue never stringifies server-lane rows -------


def test_snapshot_chat_queue_takes_content_of_server_rows():
    session = _session(
        chat_queue=[
            "plain",
            {"id": "q-1", "content": "run the fit", "client_id": "tui"},
            {"id": "q-2"},
        ]
    )
    state = _bridge(session).snapshot()
    assert state["session"]["chat_queue"] == ["plain", "run the fit"]


# -- id 73: trust_list unwraps the server's {rows, count} body --------------


def test_trust_list_unwraps_rows_and_count():
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._client = MagicMock()
    server._client.trust_list.return_value = {"rows": [{"tool": "bash"}], "count": 1}
    assert server.trust_list_rpc({}) == {"rows": [{"tool": "bash"}], "count": 1}


# -- ids 76 / 85: the assets patch lane reads UMRS field names, no slash ----


def test_client_assets_patch_posts_without_trailing_slash():
    from r4.client import Client

    client = Client.__new__(Client)
    client._config = SimpleNamespace(gateway_url="http://gw.test")
    client._is_umrs_direct = False
    client._http_core = MagicMock()
    client.assets_patch(base={"model:m1": "h"})
    assert client.http.post.call_args.args[0] == "http://gw.test/gw/assets/patch"


def test_assets_patch_maps_upserts_and_tombstones():
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = MagicMock()
    server._session.client.assets_patch.return_value = {
        "snapshot_hash": "sha-b",
        "upserts": [{"kind": "model", "asset_id": "m2"}],
        "tombstones": ["model:m1"],
        "same": False,
        "warnings": [],
        "count": 1,
    }
    out = server.assets_patch({"base": {"model:m1": "h"}})
    assert out == {
        "rows": [{"kind": "model", "asset_id": "m2"}],
        "tombstoned": ["model:m1"],
        "snapshot_hash": "sha-b",
    }


# -- id 67: get_job falls back to the durable UMRS job ----------------------


def _durable_job() -> Job:
    started = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
    attempt = JobAttempt(
        attempt_id="a1",
        job_id="j-umrs",
        attempt_no=1,
        backend="slurm",
        backend_ref="123",
        status="failed",
        started_at=started,
        ended_at=None,
        exit_reason="oom",
        log_uri=None,
        status_detail=None,
    )
    return Job(
        job_id="j-umrs",
        project_id="proj-a",
        user_id="u",
        job_type="workflow",
        spec={},
        status="running",
        priority=0,
        max_attempts=3,
        restart_policy="on_crash",
        created_at=started,
        updated_at=None,
        attempts=[attempt],
    )


def test_get_job_falls_back_to_umrs_when_local_table_misses():
    server = _bridge()
    server.session.client.jobs = MagicMock()
    server.session.client.jobs.get.return_value = _durable_job()
    out = server.get_job({"job_id": "j-umrs"})
    server.session.client.jobs.get.assert_called_once_with("j-umrs")
    job = out["job"]
    assert job["state"] == "running"
    assert job["attempts"][0]["status"] == "failed"
    assert job["attempts"][0]["started_at"] == "2026-10-17T09:00:00+00:00"
    assert job["attempts"][0]["detail"] == "oom"


def test_get_job_keeps_local_rows_and_reports_a_double_miss():
    server = _bridge()
    server.session.client.jobs = MagicMock()
    server._job_manager = MagicMock()
    server._job_manager.get_job.return_value = {"id": "local-1", "status": "running"}
    assert server.get_job({"job_id": "local-1"})["job"]["id"] == "local-1"
    server.session.client.jobs.get.assert_not_called()
    server._job_manager.get_job.return_value = None
    server.session.client.jobs.get.side_effect = RuntimeError("404")
    with pytest.raises(ValueError, match="job not found: nope"):
        server.get_job({"job_id": "nope"})


# -- id 69: a session switch starts the file-change registry clean ----------


def test_session_switch_resets_the_change_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools.reset_session_changes()
    first, second = _session("comp-1"), _session("comp-2")
    server = _bridge(first, second)
    server.initialize({})
    tools._file_write("a.txt", "from session one\n")
    assert len(tools.get_session_changes()) == 1
    server.load_session({"session_id": "comp-1"})  # same session: kept
    assert len(tools.get_session_changes()) == 1
    server.load_session({"session_id": "comp-2"})
    assert tools.get_session_changes() == []
    tools.reset_session_changes()


# -- id 43: loops.stop|pause|resume follow the resolved runtime -------------


def _local_running_loop(db_path: Path) -> str:
    from r4.runtime.api import LoopState
    from r4.runtime.local import LocalRuntime

    state = LoopState(
        loop_id="local-abc123",
        project_id="proj-a",
        research_goal="MOF-5 uptake",
        status="running",
        phase="planning",
        current_iteration=0,
        max_iterations=3,
    )
    LocalRuntime(db_path=db_path)._insert_loop(state)
    return state.loop_id


def test_loop_control_drives_the_local_runtime_in_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "local.db"))
    monkeypatch.setattr("r4.runtime.resolver._project_metadata", lambda _pid: None)
    loop_id = _local_running_loop(tmp_path / "local.db")
    server = _bridge()
    client = server.session.client
    client.pause_loop = MagicMock()
    client.resume_loop = MagicMock()
    client.stop_loop = MagicMock()

    paused = server.loops_pause({"loop_id": loop_id})
    assert paused["status"] == "paused" and paused["runtime"] == "local"
    assert server.loops_resume({"loop_id": loop_id})["status"] == "running"
    assert server.loops_stop({"loop_id": loop_id})["status"] == "stopped"
    client.pause_loop.assert_not_called()
    client.resume_loop.assert_not_called()
    client.stop_loop.assert_not_called()


@pytest.mark.parametrize("verb", ["stop", "pause", "resume"])
def test_loop_control_keeps_the_umrs_client_call(monkeypatch, verb):
    monkeypatch.delenv("R4_RUNTIME_MODE", raising=False)
    monkeypatch.setattr("r4.runtime.resolver._project_metadata", lambda _pid: None)
    server = _bridge()
    method = MagicMock(return_value={"loop_id": "L-1", "status": "accepted"})
    setattr(server.session.client, f"{verb}_loop", method)

    out = getattr(server, f"loops_{verb}")({"loop_id": "L-1"})

    assert out == {"loop_id": "L-1", "status": "accepted"}
    method.assert_called_once_with("L-1")
    with pytest.raises(ValueError, match=f"loops.{verb} requires loop_id"):
        getattr(server, f"loops_{verb}")({})


@pytest.mark.parametrize("verb", ["stop", "pause", "resume"])
def test_loop_control_unknown_runtime_mode_keeps_the_umrs_call(monkeypatch, verb):
    # A typo in R4_RUNTIME_MODE must not break loop control for a UMRS user:
    # these verbs reached the UMRS client before they became runtime-aware.
    monkeypatch.setenv("R4_RUNTIME_MODE", "umr")
    monkeypatch.setattr("r4.runtime.resolver._project_metadata", lambda _pid: None)
    server = _bridge()
    method = MagicMock(return_value={"loop_id": "L-1", "status": "accepted"})
    setattr(server.session.client, f"{verb}_loop", method)

    out = getattr(server, f"loops_{verb}")({"loop_id": "L-1"})

    assert out == {"loop_id": "L-1", "status": "accepted"}
    method.assert_called_once_with("L-1")
