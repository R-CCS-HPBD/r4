"""Regressions from the 2026-10-17 TUI audit (py-commands lane).

Each test pins a defect the audit confirmed in ``r4/src/r4/tui/commands.py``
or ``r4/src/r4/_cli/agent_exec.py``; the finding ids are in the test names.

2026-10-19 addition: ``/workflow delete`` (new registry verb, not an audit
finding) — the four pins sit above test_12.
"""

from __future__ import annotations

import sys
import time
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import WorkflowStatus

from r4.companion.session_models import SessionMessage
from r4.errors import R4Error
from r4.tui import commands
from r4.tui.commands import get_slash_registry


class _Renderer:
    def __init__(self) -> None:
        self.info_lines: list[str] = []
        self.system_lines: list[str] = []
        self.error_lines: list[str] = []

    def info(self, text: str) -> None:
        self.info_lines.append(str(text))

    def system(self, text: str) -> None:
        self.system_lines.append(str(text))

    def error(self, text: str, *, correlation_id: str | None = None) -> None:
        self.error_lines.append(str(text))

    def all_lines(self) -> list[str]:
        return self.info_lines + self.system_lines + self.error_lines


def _app(**client_attrs) -> SimpleNamespace:
    client = SimpleNamespace(config=SimpleNamespace(project_id="demo"), **client_attrs)
    return SimpleNamespace(
        renderer=_Renderer(),
        commands=get_slash_registry(),
        client=client,
        context_manager=None,
    )


def _dispatch(app: SimpleNamespace, line: str) -> None:
    assert app.commands.dispatch(app, line)


# -- 2026-10-19: /workflow delete (the typed name IS the confirmation) -------


def test_workflow_delete_renders_the_server_message() -> None:
    calls: list[str] = []

    def delete(name: str) -> dict[str, str]:
        calls.append(name)
        return {"status": "ok", "message": f"Workflow '{name}' deleted"}

    app = _app(workflows=SimpleNamespace(delete=delete))
    _dispatch(app, "/workflow delete relax")
    assert calls == ["relax"]
    assert app.renderer.system_lines == ["Workflow 'relax' deleted"]
    assert app.renderer.error_lines == []


def test_workflow_delete_requires_a_name() -> None:
    delete = MagicMock()
    app = _app(workflows=SimpleNamespace(delete=delete))
    _dispatch(app, "/workflow delete")
    delete.assert_not_called()
    assert any("usage: /workflow delete" in line for line in app.renderer.info_lines)


def test_workflow_delete_surfaces_the_server_error() -> None:
    def fail(_name: str) -> None:
        raise R4Error("Workflow 'relax' is owned by another user")

    app = _app(workflows=SimpleNamespace(delete=fail))
    _dispatch(app, "/workflow delete relax")
    assert any("another user" in line for line in app.renderer.error_lines)
    assert app.renderer.system_lines == []


def test_workflow_delete_without_a_workflow_client_explains() -> None:
    app = _app()
    _dispatch(app, "/workflow delete relax")
    assert any("requires the workflow client" in line for line in app.renderer.info_lines)


# -- 12: /workflow cancel reads the returned status ---------------------------


@pytest.mark.parametrize(
    ("status", "expected", "absent"),
    [
        ("succeeded", "already succeeded", "workflow cancelled"),
        ("running", "cancel requested", "workflow cancelled"),
        ("cancelled", "workflow cancelled: exec-1", "already"),
    ],
)
def test_12_workflow_cancel_reports_the_returned_status(status, expected, absent) -> None:
    result = WorkflowStatus(execution_id="exec-1", workflow_name="wf", status=status)
    app = _app(workflows=SimpleNamespace(cancel=lambda _id: result))

    _dispatch(app, "/workflow cancel exec-1")

    lines = app.renderer.all_lines()
    assert any(expected in line for line in lines), lines
    assert not any(absent in line for line in lines), lines
    assert app.renderer.error_lines == []


# -- 10: /workflow trigger --wait is bounded under the bridge -----------------


@pytest.mark.parametrize("override", [None, "120"])
def test_10_workflow_trigger_wait_is_bounded(monkeypatch, override) -> None:
    # The cap holds even when the operator raises the command timeout: the
    # same value also bounds the trigger POST, and v3 gives up at 60 s.
    if override is not None:
        monkeypatch.setenv("R4_TUI_COMMAND_TIMEOUT_SEC", override)
    trigger = MagicMock(
        side_effect=R4Error("Timed out waiting for execution exec-7 (last status=running)")
    )
    app = _app(workflows=SimpleNamespace(trigger=trigger))

    _dispatch(app, "/workflow trigger wf-demo --wait")

    kwargs = trigger.call_args.kwargs
    assert kwargs["wait"] is True
    assert kwargs["timeout"] is not None and 2 * kwargs["timeout"] < 60
    lines = app.renderer.all_lines()
    assert any("/workflow status exec-7" in line for line in lines), lines
    assert app.renderer.error_lines == []


def test_10_workflow_trigger_without_wait_sends_no_timeout() -> None:
    trigger = MagicMock(return_value=SimpleNamespace(execution_id="exec-8", status="queued"))
    app = _app(workflows=SimpleNamespace(trigger=trigger))

    _dispatch(app, "/workflow trigger wf-demo")

    assert "timeout" not in trigger.call_args.kwargs
    assert any("exec-8" in line for line in app.renderer.system_lines)


# -- 13: a numeric instance ref is an id before it is a list index ------------


def test_13_endpoint_down_prefers_an_exact_numeric_instance_ref() -> None:
    stopped: list[str] = []
    endpoints = {
        "endpoints": [
            {"instance_id": "ep-a", "status": "ready", "model": "a"},
            {"job_id": "41234", "status": "provisioning", "model": "b"},
        ]
    }
    app = _app(
        list_endpoints=lambda include_stopped=False: endpoints,
        stop_endpoint=lambda instance_id, force=False: (
            stopped.append(instance_id) or {"instance_id": instance_id, "status": "stopping"}
        ),
    )

    _dispatch(app, "/endpoint down 41234")

    assert stopped == ["41234"]


# -- 16: /research start hint carries the loop id -----------------------------


def test_16_research_start_hint_names_the_loop(monkeypatch) -> None:
    runtime = SimpleNamespace(
        start_loop=lambda _spec: SimpleNamespace(loop_id="loop-42"),
        capabilities=lambda: SimpleNamespace(location="umrs"),
    )
    monkeypatch.setattr(commands, "_research_runtime_for", lambda _app: runtime)
    app = _app()

    _dispatch(app, "/research start map the band gap")

    hints = [line for line in app.renderer.info_lines if "watch iterations" in line]
    assert hints == ["watch iterations: /research status loop-42 · stop: /research stop loop-42"]


# -- 17/27: /artifact and /run output reaches the renderer, not stdout --------


def _printing_cli(ns: Namespace) -> None:
    print("ID      AGENT   STATUS")
    print("exec-1  qc      running")
    print("Error: A2L artifact service is not available", file=sys.stderr)
    sys.exit(1)


@pytest.mark.parametrize(
    ("module", "attr", "line"),
    [
        ("r4._cli.agent_exec", "cmd_agent_exec", "/run list"),
        ("r4._cli.artifact", "cmd_artifact", "/artifact list"),
    ],
)
def test_17_27_cli_output_is_rendered_not_printed(monkeypatch, capsys, module, attr, line) -> None:
    import importlib

    monkeypatch.setattr(importlib.import_module(module), attr, _printing_cli)
    app = _app()

    _dispatch(app, line)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "exec-1  qc      running" in app.renderer.info_lines
    assert app.renderer.error_lines == ["Error: A2L artifact service is not available"]


def test_17_27_other_threads_keep_the_real_stdout(monkeypatch, capsys) -> None:
    # A bridge worker thread that emits a protocol frame while /run is in its
    # HTTP call must reach the real stdout, not the command's capture buffer.
    import threading

    import r4._cli.agent_exec as agent_exec

    def _cli_with_a_bridge_thread(_ns) -> None:
        print("exec-1  qc      running")
        frame = threading.Thread(target=lambda: print('{"method": "session/update"}'))
        frame.start()
        frame.join()

    monkeypatch.setattr(agent_exec, "cmd_agent_exec", _cli_with_a_bridge_thread)
    app = _app()

    _dispatch(app, "/run list")

    assert capsys.readouterr().out == '{"method": "session/update"}\n'
    assert app.renderer.info_lines == ["exec-1  qc      running"]


# -- 18: the ledger has no artifact links or status/project filters -----------


def _agent_exec_args(**overrides) -> Namespace:
    values = {
        "exec_command": "list",
        "exec_id": "exec-1",
        "agent": None,
        "version": "",
        "model": "",
        "prompt_hash": "",
        "params": "{}",
        "inputs": [],
        "status": None,
        "outputs": [],
        "error": None,
        "trace": None,
        "project": None,
        "user": None,
        "limit": 50,
        "offset": 0,
        "as_json": False,
    }
    values.update(overrides)
    return Namespace(**values)


class _LedgerHTTP:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def get(self, url, **_kwargs):
        if url.endswith("/gw/a2l/health"):
            return {"ok": True}
        return {
            "executions": [
                {"id": "ex-1", "agent_name": "qc", "status": "failed", "project_id": "p1"},
                {"id": "ex-2", "agent_name": "qc", "status": "completed", "project_id": "p1"},
                {"id": "ex-3", "agent_name": "qc", "status": "failed", "project_id": "p2"},
            ]
        }

    def post(self, url, **_kwargs):
        self.writes.append(("POST", url))
        return {"id": "ex-new"}

    def request(self, method, url, **_kwargs):
        self.writes.append((method, url))
        return {"id": "ex-1"}


@pytest.fixture
def ledger_http(monkeypatch) -> _LedgerHTTP:
    import r4.client as client_module

    http = _LedgerHTTP()

    class FakeClient:
        def __init__(self) -> None:
            self.http = http
            self.config = SimpleNamespace(gateway_url="http://gateway.test", project_id=None)

    monkeypatch.setattr(client_module, "Client", FakeClient)
    return http


@pytest.mark.parametrize(
    "overrides",
    [
        {"exec_command": "create", "agent": "qc", "inputs": ["art-123"]},
        {"exec_command": "update", "status": "completed", "outputs": ["art-9"]},
    ],
)
def test_18_artifact_links_are_refused_not_dropped(ledger_http, capsys, overrides) -> None:
    from r4._cli.agent_exec import cmd_agent_exec

    with pytest.raises(SystemExit) as exc_info:
        cmd_agent_exec(_agent_exec_args(**overrides))

    assert exc_info.value.code == 2
    assert ledger_http.writes == []
    assert "artifact" in capsys.readouterr().err


def test_18_list_status_and_project_filters_apply(ledger_http, capsys) -> None:
    from r4._cli.agent_exec import cmd_agent_exec

    cmd_agent_exec(_agent_exec_args(status="failed", project="p1"))

    out = capsys.readouterr().out
    assert "ex-1" in out
    assert "ex-2" not in out
    assert "ex-3" not in out


# -- 21: /compact through the bridge reaches ContextManager.compact -----------


def test_21_compact_converts_session_messages_for_the_context_manager() -> None:
    received: list[list[dict]] = []

    def compact(messages):
        received.append(messages)
        return [{"role": "system", "content": "[Conversation summary]\nshort"}] + messages[-2:]

    messages = [
        SessionMessage(role="user" if i % 2 else "assistant", content=f"m{i}") for i in range(8)
    ]
    app = _app()
    app.session = SimpleNamespace(messages=messages)
    app.context_manager = SimpleNamespace(compact=compact)

    _dispatch(app, "/compact")

    assert received and received[0][0] == {"role": "assistant", "content": "m0"}
    assert len(app.session.messages) == 3
    assert not any("compacting" in line for line in app.renderer.info_lines)


# -- 22: the /run usage names /run ---------------------------------------------


def test_22_run_usage_names_the_registered_command() -> None:
    app = _app()

    _dispatch(app, "/run show")
    _dispatch(app, "/run help")

    assert app.renderer.info_lines
    assert not any("/agent-exec" in line for line in app.renderer.info_lines)
    assert any("usage: /run show <exec-id>" in line for line in app.renderer.info_lines)


# -- 23: /agent chat and /search use the bridge app's send_user_message --------


@pytest.mark.parametrize(
    ("line", "needle"),
    [("/agent chat hello there", "hello there"), ("/search perovskite band gap", "perovskite")],
)
def test_23_agent_and_search_send_a_turn(line, needle) -> None:
    sent: list[str] = []
    app = _app()
    app.send_user_message = sent.append

    _dispatch(app, line)

    assert app.renderer.error_lines == []
    assert len(sent) == 1 and needle in sent[0]
    assert not sent[0].startswith("/search")


# -- 26/91: /pipeline reads the bare JSON lists UMRS returns ------------------


# Row shapes as UMRS sends them: the registry to_dict()s in
# umrs/src/umrs/rag/registry.py. (Pipeline CRUD is retired, 2026-10-18.)
_PIPELINE_ROWS = {
    "/gw/components/chunkers": [
        {"chunker_type": "fixed_size", "name": "Fixed size", "is_builtin": True}
    ],
    "/gw/components/embedders": [
        {
            "embedder_type": "sentence_transformers",
            "model_name": "bge-m3",
            "display_name": "BGE M3",
            "dimension": 1024,
            "is_builtin": True,
        }
    ],
    "/gw/components/rerankers": [
        {
            "reranker_type": "cross_encoder",
            "model_name": "bge-reranker",
            "display_name": "BGE reranker",
            "is_builtin": False,
        }
    ],
}


@pytest.mark.parametrize(
    ("line", "cells"),
    [
        ("/pipeline components chunkers", ["fixed_size", "Fixed size", "True"]),
        ("/pipeline components embedders", ["sentence_transformers", "bge-m3", "1024"]),
        ("/pipeline components rerankers", ["cross_encoder", "bge-reranker", "False"]),
    ],
)
def test_26_91_pipeline_components_render_the_real_rows(line, cells) -> None:
    app = _app(get=lambda path, **_kw: _PIPELINE_ROWS[path])

    _dispatch(app, line)

    assert app.renderer.error_lines == []
    rows = app.renderer.info_lines[2:]
    assert len(rows) == 1, app.renderer.info_lines
    for cell in cells:
        assert cell in rows[0], (cell, rows[0])


def test_26_91_pipeline_rag_profiles_reports_retirement_without_a_call() -> None:
    get = MagicMock(side_effect=AssertionError("retired route must not be called"))
    app = _app(get=get)

    _dispatch(app, "/pipeline rag-profiles")

    assert app.renderer.error_lines == []
    assert any("retired" in line for line in app.renderer.all_lines())
    get.assert_not_called()


# H-10.1 follow-up (2026-10-18): UMRS retired pipeline definitions (they
# lived in process memory and no ingest or search read them) and the
# /gw/pipelines edge location is gone, so the CRUD subcommands say so and
# name the replacement without calling the gateway.
@pytest.mark.parametrize(
    "line",
    [
        "/pipeline list",
        "/pipeline show p-1",
        "/pipeline create --name demo",
        "/pipeline delete p-1",
        "/pipelines list",
    ],
)
def test_pipeline_crud_reports_retirement_without_a_call(line) -> None:
    refuse = MagicMock(side_effect=AssertionError("retired route must not be called"))
    app = _app(get=refuse, post=refuse, delete=refuse, patch=refuse)

    _dispatch(app, line)

    assert app.renderer.error_lines == []
    text = " ".join(app.renderer.all_lines())
    assert "retired" in text
    assert "UMRS_RAG_CHUNK_MAX_TOKENS" in text
    assert "/pipeline components" in text
    refuse.assert_not_called()


def test_pipeline_usage_lists_only_live_subcommands() -> None:
    app = _app()

    _dispatch(app, "/pipeline")

    usage = " ".join(app.renderer.all_lines())
    assert "/pipeline components" in usage
    assert "create --name" not in usage
    assert "<pipeline_id>" not in usage


# -- 29/92: /audit show matches the event id; summary is computed -------------


_AUDIT_EVENTS = {
    "events": [
        {"event_id": "evt-new", "event": "job.submit", "outcome": "success"},
        {"event_id": "evt-123", "event": "job.cancel", "outcome": "denied"},
        {"event_id": "evt-old", "event": "job.submit", "outcome": "success"},
    ],
    "count": 3,
    "persistent": True,
}


def test_29_92_audit_show_renders_the_matching_event() -> None:
    get = MagicMock(return_value=_AUDIT_EVENTS)
    app = _app(get=get)

    _dispatch(app, "/audit show evt-123")
    _dispatch(app, "/audit show evt-missing")

    assert "event_id" not in get.call_args.kwargs["params"]
    assert any("job.cancel" in line for line in app.renderer.info_lines)
    assert not any("evt-new" in line for line in app.renderer.info_lines)
    assert any("audit event not found: evt-missing" in line for line in app.renderer.info_lines)


def test_29_92_audit_summary_counts_events() -> None:
    get = MagicMock(return_value=_AUDIT_EVENTS)
    app = _app(get=get)

    _dispatch(app, "/audit summary")

    assert "summary" not in get.call_args.kwargs["params"]
    lines = app.renderer.info_lines
    assert any("job.submit: 2" in line for line in lines), lines
    assert any("denied: 1" in line for line in lines), lines


# -- 30: /governance orphans takes a resource type; --since takes durations ---


def test_30_governance_orphans_needs_a_resource_type() -> None:
    get = MagicMock(return_value={"orphans": []})
    app = _app(get=get)

    _dispatch(app, "/governance orphans")
    get.assert_not_called()
    assert any("orphans <resource_type>" in line for line in app.renderer.info_lines)

    _dispatch(app, "/governance orphans endpoint")
    assert get.call_args.kwargs["params"] == {"resource_type": "endpoint"}


def test_30_governance_audit_since_duration_becomes_a_timestamp() -> None:
    get = MagicMock(return_value={"events": []})
    app = _app(get=get)

    _dispatch(app, "/governance audit --since 1h")

    since = get.call_args.kwargs["params"]["since"]
    assert isinstance(since, float)
    assert abs((time.time() - 3600) - since) < 60
    assert app.renderer.error_lines == []


# -- 24/25: /memory and /secret are retired (no gateway location) --------------


@pytest.mark.parametrize("line", ["/memory list session", "/mem search x", "/secret list demo"])
def test_memory_and_secret_are_retired_and_call_nothing(line) -> None:
    http = MagicMock()
    app = _app(get=http, post=http, delete=http)
    _dispatch(app, line)
    http.assert_not_called()
    assert app.renderer.error_lines == []
    assert "no gateway location" in app.renderer.system_lines[-1]
    assert "retired 2026-10-17" in app.renderer.system_lines[-1]


def test_drift_retirements_keep_their_message() -> None:
    app = _app()
    _dispatch(app, "/vectors versions")
    assert "never mounted" in app.renderer.system_lines[-1]
