"""Tests for Bun TUI project_* bridge RPCs and list_slash_commands."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from r4.companion.ui.bridge_server import CompanionBridgeServer
from r4.companion.ui.project_bridge import ProjectBridgeMixin

_UNSET = object()


@contextlib.contextmanager
def _clean_project_env() -> Iterator[None]:
    """Guarantee R4_PROJECT_ID is restored afterwards — the code under test
    SETS it in-process (tab-scoped selection), which monkeypatch.delenv can
    never unwind (a missing key records nothing to restore)."""
    old: object = os.environ.get("R4_PROJECT_ID", _UNSET)
    os.environ.pop("R4_PROJECT_ID", None)
    try:
        yield
    finally:
        os.environ.pop("R4_PROJECT_ID", None)
        if old is not _UNSET:
            os.environ["R4_PROJECT_ID"] = str(old)


def test_list_slash_commands_returns_catalog() -> None:
    bridge = CompanionBridgeServer()
    result = bridge.list_slash_commands({})
    assert "commands" in result
    names = {c["name"] for c in result["commands"]}
    assert "help" in names or "endpoint" in names or len(names) > 0


def test_project_catalog_never_invents_bun_lifecycle_subcommands() -> None:
    """Must not invent Bun lifecycle subcommands into shared metadata."""
    bridge = CompanionBridgeServer()
    result = bridge.list_slash_commands({})
    project = next((c for c in result["commands"] if c["name"] == "project"), None)
    if project is not None:
        subs = project.get("subcommands") or []
        assert "import" not in subs
        assert "init" not in subs


def test_project_list_rpc() -> None:
    bridge = CompanionBridgeServer()
    with patch("r4.companion.ui.project_bridge.get_project_manager") as gpm:
        pm = MagicMock()
        pm.list.return_value = []
        pm.get_current.return_value = None
        gpm.return_value = pm
        result = bridge.project_list({})
    assert result["projects"] == []
    assert result["current"] is None


def _pm_with_registry() -> MagicMock:
    """Project-manager mock whose create()/delete() drive get()."""
    pm = MagicMock()
    registry: dict[str, object] = {}
    pm.create.side_effect = lambda project: registry.setdefault(project.project_id, project)
    pm.get.side_effect = registry.get
    pm.delete.side_effect = lambda project_id: registry.pop(project_id, None) is not None
    return pm


def test_project_create_selects_and_rebuilds() -> None:
    bridge = CompanionBridgeServer()
    rebuild = MagicMock()
    bridge._rebuild_client_after_config_reload = rebuild  # type: ignore[method-assign]

    pm = _pm_with_registry()

    with _clean_project_env():
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            with patch("r4.companion.ui.project_bridge.reload_config"):
                result = bridge.project_create({"project_id": "demo-x", "name": "Demo"})

        assert result["selected"] is True
        assert result["config_changed"] is True
        assert result["project_id"] == "demo-x"
        pm.create.assert_called_once()
        # Tab-scoped selection (2026-10-01): env pin in THIS process, no global write.
        assert os.environ["R4_PROJECT_ID"] == "demo-x"
        pm.set_current.assert_not_called()
        rebuild.assert_called()


def test_project_switch_is_tab_scoped() -> None:
    """A switch in one chat tab must not rewrite the global current project."""
    bridge = CompanionBridgeServer()
    bridge._rebuild_client_after_config_reload = MagicMock()  # type: ignore[method-assign]

    pm = MagicMock()
    pm.get.return_value = MagicMock()  # project exists

    with _clean_project_env():
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            with patch("r4.companion.ui.project_bridge.reload_config"):
                result = bridge.project_switch({"project_id": "proj-a"})

        assert result["selected"] is True
        assert result["config_changed"] is True
        assert "note" not in result  # no previous selection in this process
        assert os.environ["R4_PROJECT_ID"] == "proj-a"
        pm.set_current.assert_not_called()


def test_project_switch_notes_previous_tab_selection() -> None:
    bridge = CompanionBridgeServer()
    bridge._rebuild_client_after_config_reload = MagicMock()  # type: ignore[method-assign]

    pm = MagicMock()
    pm.get.return_value = MagicMock()

    with _clean_project_env():
        os.environ["R4_PROJECT_ID"] = "proj-a"
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            with patch("r4.companion.ui.project_bridge.reload_config"):
                result = bridge.project_switch({"project_id": "proj-b"})

        assert os.environ["R4_PROJECT_ID"] == "proj-b"
        assert "tab-scoped" in result.get("note", "")
        assert "proj-a" in result.get("note", "")
        pm.set_current.assert_not_called()


def test_project_switch_unknown_project_errors() -> None:
    bridge = CompanionBridgeServer()
    pm = MagicMock()
    pm.get.return_value = None

    with _clean_project_env():
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            with pytest.raises(RuntimeError, match="Project not found"):
                bridge.project_switch({"project_id": "ghost"})
        assert "R4_PROJECT_ID" not in os.environ


def test_project_delete_clears_this_tabs_selection() -> None:
    """Deleting the tab's active project drops THIS process's env selection."""
    bridge = CompanionBridgeServer()
    bridge._rebuild_client_after_config_reload = MagicMock()  # type: ignore[method-assign]

    pm = _pm_with_registry()
    pm.get.side_effect = lambda pid: MagicMock() if pid == "demo-x" else None
    pm.delete.return_value = True

    with _clean_project_env():
        os.environ["R4_PROJECT_ID"] = "demo-x"
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            with patch("r4.companion.ui.project_bridge.reload_config"):
                result = bridge.project_delete({"project_id": "demo-x"})

        assert result["deleted"] is True
        assert result["was_selected"] is True
        assert result["config_changed"] is True
        assert "R4_PROJECT_ID" not in os.environ
        pm.delete.assert_called_once_with("demo-x")


def test_project_delete_leaves_other_tabs_selection_alone() -> None:
    """Deleting a project that is NOT this tab's never touches the env."""
    bridge = CompanionBridgeServer()
    bridge._rebuild_client_after_config_reload = MagicMock()  # type: ignore[method-assign]

    pm = MagicMock()
    pm.get.return_value = MagicMock()
    pm.delete.return_value = True

    with _clean_project_env():
        os.environ["R4_PROJECT_ID"] = "proj-a"
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            result = bridge.project_delete({"project_id": "proj-b"})

        assert result["deleted"] is True
        assert result["was_selected"] is False
        assert result["config_changed"] is False
        assert os.environ["R4_PROJECT_ID"] == "proj-a"


def test_project_delete_unknown_project_errors() -> None:
    bridge = CompanionBridgeServer()
    pm = MagicMock()
    pm.get.return_value = None

    with _clean_project_env():
        with patch("r4.companion.ui.project_bridge.get_project_manager", return_value=pm):
            with pytest.raises(RuntimeError, match="Project not found"):
                bridge.project_delete({"project_id": "ghost"})
        pm.delete.assert_not_called()


def test_bridge_death_during_project_job_marks_terminal() -> None:
    """If the worker dies unexpectedly, emit exactly one error terminal event."""
    events: list[dict] = []

    class Host(ProjectBridgeMixin):
        def _emit_line(self, payload):  # noqa: ANN001
            if "project_job_event" in payload:
                events.append(payload["project_job_event"])

    host = Host()
    host._project_jobs = {}

    class DeadProc:
        returncode = 1

        def communicate(self):
            raise BrokenPipeError("bridge/worker died")

        def poll(self):
            return 1

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def send_signal(self, _sig) -> None:  # noqa: ANN001
            pass

        def wait(self, timeout=None):  # noqa: ANN001
            return 1

    with patch("r4.companion.ui.project_bridge.subprocess.Popen", return_value=DeadProc()):
        with patch("r4.companion.ui.project_bridge._r4_project_argv", lambda a: ["x"] + a):
            started = host.project_init({"project_id": "demo"})
            job_id = started["jobId"]
            time.sleep(0.2)

    matching = [e for e in events if e.get("jobId") == job_id]
    terminals = [e for e in matching if e.get("phase") in {"done", "error", "cancelled"}]
    assert len(terminals) == 1
    assert terminals[0]["phase"] == "error"


def test_project_status_returns_structured_payload() -> None:
    """project_status now delegates to project_knowledge (live contract)."""
    bridge = CompanionBridgeServer()
    with patch(
        "r4._cli.project_knowledge.project_status",
        side_effect=lambda ns, pm: print('{"project_id": "x", "loops": 1}'),
    ) as pk_status:
        result = bridge.project_status({"project_id": "x"})
    assert result["status"]["project_id"] == "x"
    pk_status.assert_called_once()


def test_project_ingest_requires_graph_and_sources() -> None:
    """Validation is honest before any backend call is made."""
    bridge = CompanionBridgeServer()
    with pytest.raises(ValueError, match="usage"):
        bridge.project_ingest({"project_id": "p", "urls": ["https://x"]})  # no graph
    with pytest.raises(ValueError, match="usage"):
        bridge.project_ingest({"project_id": "p", "graph": "g"})  # no files/urls


class _JobEventHost(ProjectBridgeMixin):
    """Host that records project_job_event payloads."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_line(self, payload):  # noqa: ANN001
        if "project_job_event" in payload:
            self.events.append(payload["project_job_event"])


class _FakeInitProc:
    """Deterministic fake Popen for project_init round-trips."""

    def __init__(self) -> None:
        self._poll = None

    def poll(self) -> int | None:
        return self._poll

    def communicate(self):
        time.sleep(0.3)
        self._poll = 0
        return ('{"ok": true}', "")

    def terminate(self) -> None:
        self._poll = -15

    def kill(self) -> None:
        self._poll = -9

    def send_signal(self, _sig) -> None:  # noqa: ANN001
        self._poll = -15

    def wait(self, timeout=None):  # noqa: ANN001, ARG002
        return self._poll

    @property
    def returncode(self) -> int:
        return self._poll if self._poll is not None else 0


def test_project_job_emits_terminal_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _JobEventHost()

    fake = _FakeInitProc()
    monkeypatch.setattr(
        "r4.companion.ui.project_bridge.subprocess.Popen",
        lambda *a, **k: fake,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "r4.companion.ui.project_bridge._r4_project_argv",
        lambda args: ["echo"] + args,
    )

    started = host.project_init({"project_id": "demo"})
    job_id = started["jobId"]
    # Cancel before it finishes
    time.sleep(0.05)
    host.project_cancel_job({"jobId": job_id})
    time.sleep(0.4)

    phases = [e.get("phase") for e in host.events if e.get("jobId") == job_id]
    assert "started" in phases
    # Exactly one terminal among done/error/cancelled
    terminals = [p for p in phases if p in {"done", "error", "cancelled"}]
    assert len(terminals) == 1


def test_cancel_before_worker_start_never_spawns(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict] = []
    deferred: list[object] = []

    class Host(ProjectBridgeMixin):
        def _emit_line(self, payload):  # noqa: ANN001
            if "project_job_event" in payload:
                events.append(payload["project_job_event"])

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):  # noqa: ANN001
            deferred.append(target)

        def start(self) -> None:
            pass

    popen = MagicMock()
    monkeypatch.setattr("r4.companion.ui.project_bridge.threading.Thread", DeferredThread)
    monkeypatch.setattr("r4.companion.ui.project_bridge.subprocess.Popen", popen)

    host = Host()
    host._project_jobs = {}
    started = host.project_init({"project_id": "demo"})
    host.project_cancel_job({"jobId": started["jobId"]})
    deferred[0]()  # type: ignore[operator]

    popen.assert_not_called()
    terminals = [event for event in events if event.get("phase") in {"done", "error", "cancelled"}]
    assert [event["phase"] for event in terminals] == ["cancelled"]
    assert host._project_jobs == {}


def test_bridge_dispatch_includes_project_methods() -> None:
    bridge = CompanionBridgeServer()
    # unknown method still errors
    resp = bridge.handle_request({"id": 1, "method": "nope", "params": {}})
    assert resp["ok"] is False
    # list_slash_commands registered
    resp2 = bridge.handle_request({"id": 2, "method": "list_slash_commands", "params": {}})
    assert resp2["ok"] is True
    assert "commands" in resp2["result"]
