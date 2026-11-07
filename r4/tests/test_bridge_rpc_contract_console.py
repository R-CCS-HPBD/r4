"""Bridge RPC result contract, console family (plan H-4.5).

Each verb in ``rpc_schema_console.CONSOLE_RESULT_TYPES`` runs through its
real handler (``_bridge_rpc_bindings``) on the shared harness
(``bridge_rpc_harness``: real store and session, network faked); its output
must validate against the verb's exported JSON Schema.

Stubs are per-case and restored by the autouse fixture: the MCP facade and
durable-jobs facade on the client (``FakeClient`` only covers what the
workflow/trust families needed), ``r4.files.FilesClient`` and ``r4.source``'s
source store. ``get_source_store`` and ``FilesClient`` are imported inside
their handlers, so module-attribute replacement reaches them.
"""

from __future__ import annotations

import json
import os
import tempfile
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

from r4._cli.explain import _EXPLANATIONS
from r4.companion.skills import Skill
from r4.companion.ui.rpc_schema_console import CONSOLE_RESULT_TYPES

_ERROR_ID = next(iter(_EXPLANATIONS))
_ORIGINALS: dict[tuple[Any, str], Any] = {}


@pytest.fixture(autouse=True)
def _restore_replaced() -> Iterator[None]:
    yield
    for (module, attr), original in _ORIGINALS.items():
        setattr(module, attr, original)
    _ORIGINALS.clear()


def _replace(module: Any, attr: str, value: Any) -> None:
    """Attribute replacement restored by the autouse fixture."""
    _ORIGINALS.setdefault((module, attr), getattr(module, attr))
    setattr(module, attr, value)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    # list_up_models reads the model registry over HTTP; the case pins the
    # result's shape, not a live registry (a failed registry call now raises
    # instead of reading as an empty list — stabilization 2026-10-21, slash-a#0).
    monkeypatch.setattr(
        "r4.tui.commands._registry_model_options",
        lambda: [{"name": "reg-model", "source": "registry", "status": "available"}],
    )
    return make_harness(tmp_path, monkeypatch)


def _mcp(h: Harness, **methods: Any) -> None:
    """One facade covering every ``client.mcp`` surface the cases touch."""
    h.client.mcp = SimpleNamespace(**methods)


def _knowledge(h: Harness) -> dict[str, Any]:
    h.client.knowledge = SimpleNamespace(write_constraints=None)
    return {"mode": "pinned", "graphs": ["g1"]}


def _project_rule(h: Harness) -> dict[str, Any]:
    """One "always for this project" rule to remove (ADR-104 D-3)."""
    import json

    from r4.companion.permission_rules import user_project_rules_path

    path = user_project_rules_path(h.session.permission_project_id())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"allow": ["shell_exec"]}))
    h.session.reload_permission_rules()
    rows = h.server.permission_rules_list({})["rules"]
    return {"index": next(row["index"] for row in rows if row["removable"])}


def _skill(h: Harness) -> dict[str, Any]:
    h.session.skills.register(
        Skill(name="demo-skill", description="d", system_prompt="", source="user")
    )
    return {"name": "demo-skill"}


def _seed_mcp_config(h: Harness, name: str = "demo-mcp") -> None:
    """One declared user-config server under the scratch config home."""
    config = Path.home() / ".config" / "r4" / "mcp-servers.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {"type": "http", "url": "http://127.0.0.1:9/demo", "enabled": True}
                }
            }
        )
    )


def _mcp_toggle(h: Harness) -> dict[str, Any]:
    _seed_mcp_config(h)
    return {"name": "demo-mcp", "enabled": True}


def _durable_job(h: Harness) -> dict[str, Any]:
    row = SimpleNamespace(
        job_id="umrs-1", status="running", job_type="workflow", project_id="demo", attempts=[]
    )
    h.client.jobs = SimpleNamespace(get=lambda _id: row)
    return {"job_id": "umrs-1"}


def _seed_local_job(h: Harness) -> str:
    with h.server._job_manager._conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, session_id, pid, status, started_at, task_description)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("local-1", h.sid, 4242, "running", "2026-10-18T00:00:00", "probe"),
        )
        conn.commit()
    return "local-1"


def _local_job(h: Harness) -> dict[str, Any]:
    return {"job_id": _seed_local_job(h)}


def _files(h: Harness) -> type:
    class _StubFilesClient:
        def upload_file(self, path: str, key: Any = None, purpose: str = "general") -> dict:
            return {"key": key or "k/1", "bucket": "b1", "uri": "s3://b1/k/1", "size": 3}

        def download_file(self, key: str, dest: str = "") -> dict:
            return {"key": key, "dest": dest, "bytes": 3}

    import r4.files as files_mod

    _replace(files_mod, "FilesClient", _StubFilesClient)
    return _StubFilesClient


def _upload(h: Harness) -> dict[str, Any]:
    _files(h)
    return {"path": "/tmp/x.txt", "content": "abc"}


def _download(h: Harness) -> dict[str, Any]:
    _files(h)
    return {"key": "k/1", "dest": "/tmp/out.txt"}


def _pushable(h: Harness) -> dict[str, Any]:
    src = SimpleNamespace(
        name="pub-1",
        kind="public",
        url="https://api.example.org/v1",
        provider=None,
        api_key="sk-demo",
    )
    import r4.source as source_mod

    _replace(
        source_mod,
        "get_source_store",
        lambda: SimpleNamespace(get=lambda _name: src, list=lambda: [src]),
    )
    return {"name": "pub-1"}


def _recorded_change(h: Harness) -> dict[str, Any]:
    from r4.companion.tools import _record_file_change

    _record_file_change("demo-change.txt", "before", "after")
    return {}


def _revert_change(h: Harness) -> dict[str, Any]:
    import tempfile

    from r4.companion.tools import _record_file_change, reset_session_changes

    reset_session_changes()
    target = Path(tempfile.mkdtemp()) / "revert-me.txt"
    target.write_text("after")
    _record_file_change(str(target), "before", "after")
    return {"path": str(target)}


@pytest.fixture(autouse=True)
def _clear_changes() -> Iterator[None]:
    from r4.companion.tools import reset_session_changes

    yield
    reset_session_changes()


@pytest.fixture(autouse=True)
def _restore_cwd() -> Iterator[None]:
    """The worktree cases chdir (the bridge does); every test ends where it
    began, and no session is left in a worktree for the exit-time cleanup."""
    from r4.companion.session_worktree import current_controller

    here = os.getcwd()
    yield
    controller = current_controller()
    if controller is not None:
        controller.worktree = None
    os.chdir(here)


def _scratch_repo() -> Path:
    """A throwaway repository (hermetic git), entered as the bridge's cwd."""
    from git_hermetic import git_init, run_git

    root = Path(tempfile.mkdtemp(prefix="r4-wt-contract-"))
    git_init(root)
    (root / "a.txt").write_text("one\n")
    run_git(root, "add", "a.txt")
    run_git(root, "commit", "-q", "-m", "init")
    os.chdir(root)
    return root


def _wt_repo(h: Harness) -> dict[str, Any]:
    _scratch_repo()
    h.server.worktree_enter({"name": "listed"})
    return {}


def _wt_no_repo(h: Harness) -> dict[str, Any]:
    os.chdir(tempfile.mkdtemp(prefix="r4-wt-plain-"))
    return {}


def _wt_enter(h: Harness) -> dict[str, Any]:
    _scratch_repo()
    return {"name": "wt-a"}


def _wt_exit(h: Harness) -> dict[str, Any]:
    _scratch_repo()
    h.server.worktree_enter({"name": "wt-b"})
    return {"action": "remove"}


def _task(h: Harness) -> str:
    return str(run_verb(h, "add_task", plain({"title": "seed"}))["task"]["id"])


def _with_task(params: dict[str, Any]) -> Setup:
    return lambda h: {"task_id": _task(h), **params}


CASES: list[Case] = [
    ("initialize", "fresh", plain({})),
    ("load_session", "active", lambda h: {"session_id": h.sid}),
    ("export_session", "active", plain({})),
    ("replay_session", "empty", plain({})),
    ("summarize_session", "empty", plain({})),
    ("list_sessions", "local", plain({})),
    ("list_sessions", "no_remote", plain({"no_remote": True})),
    ("cancel_turn", "no_turn", plain({})),
    ("shutdown", "ok", plain({})),
    ("set_model", "switch", plain({"model": "gpt-y"})),
    ("permission_rules.list", "empty", plain({})),
    ("permission_rules.remove", "project_rule", _project_rule),
    ("activate_skill", "seeded", _skill),
    ("set_interaction_mode", "plan", plain({"mode": "plan"})),
    ("set_knowledge_acquisition", "pinned", _knowledge),
    ("set_workflow_armed", "armed", plain({"armed": True})),
    ("set_write_requires_plan", "on", plain({"enabled": True})),
    ("list_models", "options", plain({})),
    ("list_up_models", "options", plain({})),
    ("list_stoppable_endpoints", "one", plain({})),
    ("list_skills", "builtins", plain({})),
    (
        "list_mcp_servers",
        "registry",
        lambda h: (_mcp(h, list_servers=lambda: [{"name": "r1"}]), {})[1],
    ),
    ("set_mcp_server_enabled", "missing_entry", plain({"name": "no-such"})),
    ("set_mcp_server_enabled", "enabled", _mcp_toggle),
    (
        "list_mcp_tools",
        "all",
        lambda h: (
            _mcp(
                h,
                list_tools=lambda server=None: [
                    SimpleNamespace(
                        name="t1", description="d", server="r1", parameters={"type": "object"}
                    )
                ],
            ),
            {},
        )[1],
    ),
    (
        "mcp_server_health",
        "probe",
        lambda h: (
            _mcp(
                h,
                check_server_health=lambda server_id: {
                    "server": "r1",
                    "server_id": server_id,
                    "url": "http://x",
                    "healthy": True,
                    "status": "healthy",
                    "registry_status": "healthy",
                    "source": "probe",
                    "checked_at": "2026-10-18T00:00:00+00:00",
                },
            ),
            {"server_id": "r1"},
        )[1],
    ),
    ("get_session_changes", "empty", plain({})),
    ("get_session_changes", "recorded", _recorded_change),
    ("revert_session_change", "restores", _revert_change),
    ("get_session_plan", "empty", plain({})),
    ("get_session_plan", "with_task", lambda h: (_task(h), {})[1]),
    ("add_task", "created", plain({"title": "t1"})),
    ("get_task", "found", _with_task({})),
    ("update_task", "progress", _with_task({"progress": 40})),
    ("list_tasks", "one", lambda h: (_task(h), {})[1]),
    ("list_jobs", "empty", plain({})),
    ("list_jobs", "one_local", lambda h: (_seed_local_job(h), {})[1]),
    ("get_job", "local", _local_job),
    ("get_job", "durable", _durable_job),
    ("get_job_log", "empty_log", plain({"job_id": "local-1"})),
    ("stop_job", "not_running", plain({"job_id": "no-such"})),
    ("files.upload", "stored", _upload),
    ("files.download", "written", _download),
    ("explain", "found", plain({"error_id": _ERROR_ID})),
    ("explain", "miss", plain({"error_id": "no.such.id"})),
    ("explain.match", "hit", plain({"message": f"high-level failure {_ERROR_ID} happened"})),
    ("explain.match", "miss", plain({"message": "no dotted tokens here"})),
    ("source.list_remote", "rows", plain({})),
    ("source.push", "pushed", _pushable),
    ("source.unpush", "unpushed", plain({"name": "pub-1"})),
    ("worktree.list", "rows", _wt_repo),
    ("worktree.list", "no_repo", _wt_no_repo),
    ("worktree.enter", "created", _wt_enter),
    ("worktree.enter", "no_name", plain({})),
    ("worktree.exit", "removed", _wt_exit),
    ("worktree.exit", "not_in_one", plain({})),
    ("worktree.mode", "read", plain({})),
    ("worktree.mode", "set_auto", plain({"mode": "auto"})),
    ("worktree.mode", "bad", plain({"mode": "sometimes"})),
]


@pytest.mark.parametrize(("method", "label", "setup"), CASES, ids=case_ids(CASES))
def test_handler_output_matches_schema(
    harness: Harness, method: str, label: str, setup: Setup
) -> None:
    _ = label
    assert schema_errors(harness, method, setup) == []


def test_every_console_verb_has_a_case() -> None:
    assert uncovered(CONSOLE_RESULT_TYPES, CASES) == set()
