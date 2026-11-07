"""Regression tests for the ``r4 workflow`` CLI ↔ SDK contract.

Audit 2026-04-27 (F-001/F-002/F-003): the CLI handlers in
``r4/src/r4/_cli/workflow.py`` were calling :class:`WorkflowClient` methods
with kwargs that the SDK rejects (``path``, ``workflow_name``, ``wait``).
These tests pin the CLI ↔ SDK signatures so a future drift fails loudly in
unit tests rather than silently breaking against a live deployment.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

from r4._cli.workflow import _cmd_run, _cmd_trigger, _cmd_upload


def test_cmd_upload_uses_workflow_path_kwarg():
    """F-001: CLI must call ``WorkflowClient.upload(workflow_path=...)``."""
    wf = MagicMock()
    wf.upload.return_value = MagicMock(
        workflow_id="wf-1", status="accepted", message=None, error=None, accepted=True
    )
    args = Namespace(path="/tmp/wf.zip", name=None, as_json=False)

    rc = _cmd_upload(wf, args)

    assert rc == 0
    wf.upload.assert_called_once()
    _, kwargs = wf.upload.call_args
    # The SDK's ``upload`` method only accepts ``workflow_path`` and
    # ``workflow_name`` — never ``path``.
    assert kwargs.get("workflow_path") == "/tmp/wf.zip"
    assert kwargs.get("workflow_name") is None
    assert "path" not in kwargs


def test_cmd_trigger_uses_workflow_id_kwarg():
    """F-002: CLI must call ``WorkflowClient.trigger(workflow_id=...)``."""
    wf = MagicMock()
    wf.trigger.return_value = MagicMock(execution_id="exec-1", status="queued", error=None)
    args = Namespace(
        workflow_name="literature_review",
        param=["topic=climate"],
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )

    rc = _cmd_trigger(wf, args)

    assert rc == 0
    wf.trigger.assert_called_once()
    _, kwargs = wf.trigger.call_args
    # The SDK's ``trigger`` method takes ``workflow_id`` (not ``workflow_name``).
    assert kwargs.get("workflow_id") == "literature_review"
    assert "workflow_name" not in kwargs


def test_cmd_trigger_wait_flag_is_forwarded_to_sdk(capsys):
    """``--wait`` is now forwarded to the SDK which polls executions."""
    wf = MagicMock()
    wf.trigger.return_value = MagicMock(execution_id="exec-1", status="succeeded", error=None)
    args = Namespace(
        workflow_name="literature_review",
        param=[],
        input_file=[],
        project_id=None,
        wait=True,
        as_json=False,
    )

    rc = _cmd_trigger(wf, args)

    assert rc == 0
    _, kwargs = wf.trigger.call_args
    # Gateway-backed status/results endpoints now exist, so wait=True is
    # forwarded and the SDK polls until terminal.
    assert kwargs.get("wait") is True
    captured = capsys.readouterr()
    assert "not supported" not in captured.err


def test_cmd_trigger_params_json_object_is_forwarded():
    """--params-json passes the same JSON object the Studio view sends."""
    wf = MagicMock()
    wf.trigger.return_value = MagicMock(execution_id="exec-2", status="queued", error=None)
    args = Namespace(
        workflow_name="literature_review",
        param=["limit=10"],
        params_json='{"topic": "climate", "limit": 5}',
        params_file=None,
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )

    rc = _cmd_trigger(wf, args)

    assert rc == 0
    _, kwargs = wf.trigger.call_args
    # JSON object first, then --param KEY=VALUE overrides on top.
    assert kwargs.get("parameters") == {"topic": "climate", "limit": "10"}


def test_cmd_trigger_params_file_is_loaded(tmp_path):
    wf = MagicMock()
    wf.trigger.return_value = MagicMock(execution_id="exec-3", status="queued", error=None)
    (tmp_path / "params.json").write_text('{"a": 1}', encoding="utf-8")
    args = Namespace(
        workflow_name="literature_review",
        param=[],
        params_json=None,
        params_file=str(tmp_path / "params.json"),
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )

    rc = _cmd_trigger(wf, args)

    assert rc == 0
    _, kwargs = wf.trigger.call_args
    assert kwargs.get("parameters") == {"a": 1}


def test_cmd_trigger_rejects_invalid_params_json(capsys):
    wf = MagicMock()
    args = Namespace(
        workflow_name="literature_review",
        param=[],
        params_json="not-json",
        params_file=None,
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )

    rc = _cmd_trigger(wf, args)

    assert rc == 2
    wf.trigger.assert_not_called()


def test_cmd_run_uploads_then_triggers_with_definition():
    """run = the CLI counterpart of the Studio runWorkflow, plus definition upload."""
    wf = MagicMock()
    wf.upload.return_value = MagicMock(
        workflow_id="wf-run", status="accepted", message=None, error=None, accepted=True
    )
    wf.trigger.return_value = MagicMock(execution_id="exec-4", status="running", error=None)
    args = Namespace(
        path="/tmp/wf_dir",
        name=None,
        param=[],
        params_json='{"threshold": 0.8}',
        params_file=None,
        input_file=["s3://bucket/scan.tif"],
        project_id="proj-1",
        wait=True,
        as_json=False,
    )

    rc = _cmd_run(wf, args)

    assert rc == 0
    _, up_kwargs = wf.upload.call_args
    assert up_kwargs.get("workflow_path") == "/tmp/wf_dir"
    assert up_kwargs.get("workflow_name") is None
    _, tr_kwargs = wf.trigger.call_args
    assert tr_kwargs.get("workflow_id") == "wf-run"
    assert tr_kwargs.get("parameters") == {"threshold": 0.8}
    assert tr_kwargs.get("input_files") == ["s3://bucket/scan.tif"]
    assert tr_kwargs.get("project_id") == "proj-1"
    assert tr_kwargs.get("wait") is True


def test_cmd_run_stops_when_upload_is_not_accepted(capsys):
    wf = MagicMock()
    wf.upload.return_value = MagicMock(
        workflow_id="wf-run",
        status="rejected",
        message="bad bundle",
        error="lint error",
        accepted=False,
    )
    args = Namespace(
        path="/tmp/wf_dir",
        name=None,
        param=[],
        params_json=None,
        params_file=None,
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )

    rc = _cmd_run(wf, args)

    assert rc == 1
    wf.trigger.assert_not_called()
    captured = capsys.readouterr()
    assert "lint error" in captured.err
