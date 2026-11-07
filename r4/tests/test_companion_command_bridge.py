from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.permissions import PermissionManager
from r4.companion.session_models import SessionMessage
from r4.companion.skills import SkillRegistry
from r4.companion.ui.command_bridge import (
    CompanionCommandApp,
    command_names,
    dispatch_shared_command,
)
from r4.tui.modes import Mode


def _make_session(client=None) -> SimpleNamespace:
    client = client or SimpleNamespace(
        config=SimpleNamespace(model="test-model", project_id="demo")
    )
    return SimpleNamespace(
        client=client,
        agent=SimpleNamespace(model=None),
        metadata={},
        messages=[],
        usage_summary=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
        skills=SkillRegistry(),
        context=SimpleNamespace(),
        permissions=PermissionManager(),
        send=MagicMock(return_value="ok"),
        export_markdown=MagicMock(return_value="# session"),
    )


def test_set_mode_clears_pending_plan_when_leaving_plan_mode() -> None:
    session = _make_session()
    session.metadata["interaction_mode"] = "plan"
    session.metadata["plan_mode_state"] = {"awaiting_confirmation": True, "pending_request": "do x"}
    renderer = MagicMock()
    app = CompanionCommandApp(session=session, store=MagicMock(), renderer=renderer)

    app.set_mode(Mode.AUTOPILOT)

    assert "plan_mode_state" not in session.metadata
    system_lines = [call.args[0] for call in renderer.system.call_args_list]
    assert any("pending plan cleared" in line for line in system_lines)


def test_companion_completion_includes_chat_and_companion_commands() -> None:
    names = set(command_names())

    # /memories omitted until implemented (C-03) — see command_bridge.py
    assert {"mcp", "rag", "up", "down", "skills", "templates"} <= names


def test_companion_dispatches_rag_docs_to_shared_command() -> None:
    client = SimpleNamespace(
        config=SimpleNamespace(model="test-model", project_id="demo"),
        rag=SimpleNamespace(
            list_documents=MagicMock(
                return_value=[
                    {
                        "document_id": "doc-1",
                        "filename": "paper.pdf",
                        "status": "indexed",
                        "chunks_created": 4,
                    }
                ]
            )
        ),
    )
    session = _make_session(client)
    renderer = MagicMock()

    dispatch_shared_command("/rag docs", session=session, store=MagicMock(), renderer=renderer)

    client.rag.list_documents.assert_called_once_with(project_id="demo")
    info_lines = [call.args[0] for call in renderer.info.call_args_list]
    assert any("indexed documents for project demo" in line for line in info_lines)
    assert any("doc-1" in line and "paper.pdf" in line for line in info_lines)


def test_companion_dispatches_mcp_command_without_unknown_error() -> None:
    session = _make_session()
    renderer = MagicMock()

    dispatch_shared_command("/mcp", session=session, store=MagicMock(), renderer=renderer)

    error_lines = [call.args[0] for call in renderer.error.call_args_list]
    assert not any("unknown command" in line.lower() for line in error_lines)
    assert renderer.info.called


def test_companion_shared_skills_accept_numeric_selection() -> None:
    session = _make_session()
    renderer = MagicMock()
    # Numbered selectors are 1-based over list_all(); order shifts as new
    # builtin skills land, so derive the expectation from the registry.
    target = session.skills.list_all()[1].name

    dispatch_shared_command("/skills 2", session=session, store=MagicMock(), renderer=renderer)

    assert target in {skill.name for skill in session.skills.list_active()}
    renderer.system.assert_called_with(f"skill activated: {target}")


def test_skill_alias_dispatches_like_skills() -> None:
    session = _make_session()
    renderer = MagicMock()

    dispatch_shared_command("/skill hpc", session=session, store=MagicMock(), renderer=renderer)

    assert "hpc" in {skill.name for skill in session.skills.list_active()}
    renderer.system.assert_called_with("skill activated: hpc")


def test_companion_dispatches_endpoint_command_family() -> None:
    client = SimpleNamespace(
        config=SimpleNamespace(model="test-model", project_id="demo"),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {
                        "instance_id": "ep-1",
                        "status": "ready",
                        "model": "qwen",
                        "backend": "slurm",
                    }
                ]
            }
        ),
    )
    session = _make_session(client)
    renderer = MagicMock()

    dispatch_shared_command("/endpoint list", session=session, store=MagicMock(), renderer=renderer)

    client.list_endpoints.assert_called_once_with(include_stopped=False, timeout=15.0)
    info_lines = [call.args[0] for call in renderer.info.call_args_list]
    assert any("ep-1" in line and "qwen" in line for line in info_lines)


def test_companion_endpoint_up_reuses_active_model_endpoint() -> None:
    client = SimpleNamespace(
        config=SimpleNamespace(model="test-model", project_id="demo"),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {
                        "instance_id": "ep-1",
                        "status": "starting",
                        "model": "qwen",
                        "backend": "slurm",
                    }
                ]
            }
        ),
        provision_endpoint=MagicMock(
            return_value={"instance_id": "ep-2", "status": "provisioning"}
        ),
    )
    session = _make_session(client)
    renderer = MagicMock()

    dispatch_shared_command(
        "/endpoint up qwen", session=session, store=MagicMock(), renderer=renderer
    )

    client.provision_endpoint.assert_not_called()
    system_lines = [call.args[0] for call in renderer.system.call_args_list]
    assert any("endpoint already active for qwen: ep-1" in line for line in system_lines)


def test_companion_endpoint_up_new_bypasses_reuse(monkeypatch) -> None:
    for var in (
        "R4_PROVISION_SUBMIT_TIMEOUT_SEC",
        "R4_TUI_COMMAND_TIMEOUT_SEC",
        "R4_COMPANION_COMMAND_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(var, raising=False)
    client = SimpleNamespace(
        config=SimpleNamespace(model="test-model", project_id="demo"),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {
                        "instance_id": "ep-1",
                        "status": "ready",
                        "model": "qwen",
                        "backend": "slurm",
                    }
                ]
            }
        ),
        provision_endpoint=MagicMock(
            return_value={"instance_id": "ep-2", "status": "provisioning"}
        ),
    )
    session = _make_session(client)
    renderer = MagicMock()

    dispatch_shared_command(
        "/endpoint up --new qwen", session=session, store=MagicMock(), renderer=renderer
    )

    # The provision submit keeps its own (capped) budget, not the 15s
    # interactive one, and stays under v3's 60s dispatch window (audit #15).
    client.provision_endpoint.assert_called_once()
    kwargs = client.provision_endpoint.call_args.kwargs
    assert kwargs["model"] == "qwen" and kwargs["wait"] is False
    assert 49.0 < kwargs["timeout"] <= 50.0


def _slow_reuse_check_client(monkeypatch, seconds: float) -> SimpleNamespace:
    """A client whose list_endpoints 'takes' ``seconds`` on time.monotonic."""
    import time

    real_monotonic = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + offset[0])

    def _slow_list_endpoints(**_kwargs):
        offset[0] += seconds
        return {"endpoints": []}

    return SimpleNamespace(
        config=SimpleNamespace(model="test-model", project_id="demo"),
        list_endpoints=MagicMock(side_effect=_slow_list_endpoints),
        provision_endpoint=MagicMock(
            return_value={"instance_id": "ep-2", "status": "provisioning"}
        ),
    )


def test_endpoint_up_submit_gets_what_the_reuse_check_left(monkeypatch) -> None:
    # v3's 60s window covers the whole /endpoint up, so a slow reuse check
    # shrinks the submit budget (review of audit #15): 50s - 20s = ~30s.
    for var in (
        "R4_PROVISION_SUBMIT_TIMEOUT_SEC",
        "R4_TUI_COMMAND_TIMEOUT_SEC",
        "R4_COMPANION_COMMAND_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(var, raising=False)
    client = _slow_reuse_check_client(monkeypatch, 20.0)
    session = _make_session(client)

    dispatch_shared_command(
        "/endpoint up qwen", session=session, store=MagicMock(), renderer=MagicMock()
    )

    client.list_endpoints.assert_called_once()
    timeout = client.provision_endpoint.call_args.kwargs["timeout"]
    assert 29.0 < timeout <= 30.0


def test_endpoint_up_submit_budget_keeps_the_interactive_floor(monkeypatch) -> None:
    for var in (
        "R4_PROVISION_SUBMIT_TIMEOUT_SEC",
        "R4_TUI_COMMAND_TIMEOUT_SEC",
        "R4_COMPANION_COMMAND_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(var, raising=False)
    client = _slow_reuse_check_client(monkeypatch, 45.0)
    session = _make_session(client)

    dispatch_shared_command(
        "/endpoint up qwen", session=session, store=MagicMock(), renderer=MagicMock()
    )

    assert client.provision_endpoint.call_args.kwargs["timeout"] == 15.0


def test_provision_submit_timeout_honours_env_within_bounds(monkeypatch) -> None:
    from r4.tui.commands import _provision_submit_timeout

    monkeypatch.delenv("R4_TUI_COMMAND_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("R4_COMPANION_COMMAND_TIMEOUT_SEC", raising=False)
    monkeypatch.setenv("R4_PROVISION_SUBMIT_TIMEOUT_SEC", "30")
    assert _provision_submit_timeout() == 30.0
    monkeypatch.setenv("R4_PROVISION_SUBMIT_TIMEOUT_SEC", "900")
    assert _provision_submit_timeout() == 50.0
    monkeypatch.setenv("R4_PROVISION_SUBMIT_TIMEOUT_SEC", "5")
    assert _provision_submit_timeout() == 15.0
    monkeypatch.setenv("R4_PROVISION_SUBMIT_TIMEOUT_SEC", "bogus")
    assert _provision_submit_timeout() == 50.0
    monkeypatch.setenv("R4_PROVISION_SUBMIT_TIMEOUT_SEC", "300")
    assert _provision_submit_timeout(elapsed=10.0) == 40.0
    assert _provision_submit_timeout(elapsed=48.0) == 15.0


def test_companion_history_falls_back_to_raw_messages() -> None:
    session = _make_session()
    session.messages = [
        SessionMessage(role="user", content="message without endpoint"),
        SessionMessage(role="system", content="API error: 503"),
    ]
    renderer = MagicMock()

    dispatch_shared_command("/history", session=session, store=MagicMock(), renderer=renderer)

    info_lines = [call.args[0] for call in renderer.info.call_args_list]
    assert any("raw message log" in line for line in info_lines)
    assert any("message without endpoint" in line for line in info_lines)


def test_companion_dispatches_code_status(tmp_path) -> None:
    (tmp_path / "main.f90").write_text("program demo\nend program demo\n")
    session = _make_session()
    renderer = MagicMock()

    dispatch_shared_command(
        f"/code status {tmp_path}", session=session, store=MagicMock(), renderer=renderer
    )

    info_lines = [call.args[0] for call in renderer.info.call_args_list]
    assert any('"primary_language": "fortran"' in line for line in info_lines)


def test_companion_dispatches_code_lsp_status(tmp_path) -> None:
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")
    session = _make_session()
    renderer = MagicMock()

    dispatch_shared_command(
        f"/code lsp {tmp_path}", session=session, store=MagicMock(), renderer=renderer
    )

    info_lines = [call.args[0] for call in renderer.info.call_args_list]
    assert any('"supported_languages": [' in line for line in info_lines)


def test_mock_store_never_leaks_magicmock_sqlite_into_cwd(tmp_path, monkeypatch) -> None:
    """Guard class d360e6a2, post-guard construction sites: the task-graph
    handlers built TaskGraph(str(self._store.db_path)) — str(mock.db_path)
    becomes the repr string and sqlite3 creates a file NAMED that repr in
    the working directory (the recurring '<MagicMock ...>' root strays).
    All sqlite construction must ride the guarded _sqlite_db_path."""
    monkeypatch.chdir(tmp_path)
    from r4.companion.ui.bridge_server import CompanionBridgeServer

    bridge = CompanionBridgeServer(client=MagicMock(), store=MagicMock())
    bridge.add_task({"title": "x", "notes": "n"})
    bridge.list_tasks({})

    stray = [p for p in tmp_path.iterdir() if p.name.startswith("<MagicMock")]
    assert stray == []
