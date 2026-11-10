"""ADR-113 WV3 (2026-11-08): workflow versions in the r4 SDK and CLI.

Codes against the UMRS REST contract of WV1/WV2: ``version`` in the
trigger/plan/run-definition body, ``?version=`` on describe/delete/package/
export, ``?all_versions=true`` on the list, ``PUT /{name}/triggers
{"version"}``, ``POST /workflows/adopt``; catalog rows, describe, upload and
execution records carry ``version``/``versions``/``workflow_version`` and
``source_sha256``. The server is mocked here.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import (
    WorkflowCatalogItem,
    WorkflowResult,
    WorkflowStatus,
    WorkflowUploadResult,
)

from r4._cli import workflow as cli
from r4.errors import R4Error
from r4.workflows import WorkflowClient, _export_target, parse_workflow_ref

GW = "https://gw.example"
DIGEST = "0123456789abcdef" * 4


class _Router:
    def __init__(self, lane: str = "http") -> None:
        self.lane = lane

    def execute(self, _op, *, http_fn, zmq_fn):
        return http_fn() if self.lane == "http" else zmq_fn()


def _client(lane: str = "http", **http_returns) -> tuple[WorkflowClient, MagicMock]:
    http = MagicMock()
    for method, value in http_returns.items():
        getattr(http, method).return_value = value
    parent = SimpleNamespace(
        config=SimpleNamespace(gateway_url=GW, timeout=30, project_id=None),
        http=http,
        router=_Router(lane),
        _api_prefix="/gw",
    )
    return WorkflowClient(parent), http


# ── SDK ───────────────────────────────────────────────────────────────────


def test_sdk_reexports_the_parser():
    assert parse_workflow_ref("seg@1.2.0") == ("seg", "1.2.0")


def test_trigger_sends_the_version_in_the_body():
    wf, http = _client(post={"execution_id": "e1", "status": "submitted"})
    wf.trigger("seg@1.2.0", parameters={"n": 1})
    url = http.post.call_args.args[0]
    body = http.post.call_args.kwargs["json_body"]
    assert url == f"{GW}/gw/workflows/seg/trigger"
    assert body["version"] == "1.2.0"
    assert body["parameters"] == {"n": 1}


def test_trigger_without_a_version_sends_none():
    wf, http = _client(post={"execution_id": "e1"})
    wf.trigger("seg")
    assert "version" not in http.post.call_args.kwargs["json_body"]


def test_trigger_refuses_two_versions_that_disagree():
    wf, http = _client(post={})
    with pytest.raises(R4Error, match="1.0.0"):
        wf.trigger("seg@1.0.0", version="2.0.0")
    http.post.assert_not_called()


def test_trigger_via_zmq_puts_the_version_in_the_run_spec():
    wf, _http = _client("zmq")
    transport = MagicMock()
    transport.request_workflow_run.return_value = {
        "execution_id": "e1",
        "workflow_version": "1.2.0",
        "source_sha256": DIGEST,
    }
    wf._parent.transport = transport
    wf._parent._ensure_transport_connected = lambda: True
    wf._parent._get_username = lambda: "alice"
    result = wf.trigger("seg", version="1.2.0")
    run_spec = transport.request_workflow_run.call_args.args[1]
    assert run_spec["workflow_name"] == "seg"
    assert run_spec["version"] == "1.2.0"
    assert result.workflow_version == "1.2.0"
    assert result.source_sha256 == DIGEST


def test_plan_sends_the_version():
    wf, http = _client(post={"digest": "d"})
    wf.plan("seg@1.2.0", parameters={})
    assert http.post.call_args.args[0] == f"{GW}/gw/workflows/seg/plan"
    assert http.post.call_args.kwargs["json_body"]["version"] == "1.2.0"


@pytest.mark.parametrize(
    ("method", "verb", "suffix"),
    [("describe", "get", ""), ("definition_package", "get", "/package"), ("delete", "delete", "")],
)
def test_query_verbs_send_version_as_a_query(method, verb, suffix):
    wf, http = _client(get={}, delete={})
    getattr(wf, method)("seg", version="1.0.0")
    call = getattr(http, verb).call_args
    assert call.args[0] == f"{GW}/gw/workflows/seg{suffix}"
    assert call.kwargs["params"] == {"version": "1.0.0"}
    getattr(wf, method)("seg")
    assert getattr(http, verb).call_args.kwargs["params"] is None


def test_list_all_versions_and_row_mapping():
    row = {
        "name": "seg",
        "version": "1.2.0",
        "versions": ["1.2.0", "1.0.0"],
        "source_sha256": DIGEST,
        "user_id": "alice",
        "scope": "user",
    }
    wf, http = _client(get={"workflows": [row]})
    (item,) = wf.list_workflows(all_versions=True)
    assert http.get.call_args.kwargs["params"]["all_versions"] == "true"
    assert (item.version, item.versions, item.source_sha256) == (
        "1.2.0",
        ["1.2.0", "1.0.0"],
        DIGEST,
    )
    wf.list_workflows()
    assert "all_versions" not in http.get.call_args.kwargs["params"]


def test_adopt_and_trigger_pin():
    wf, http = _client(post={"name": "seg", "version": "0.2.9"}, put={})
    wf.adopt("seg-v0.2.9", "seg", "0.2.9")
    assert http.post.call_args.args[0] == f"{GW}/gw/workflows/adopt"
    assert http.post.call_args.kwargs["json_body"] == {
        "from_name": "seg-v0.2.9",
        "name": "seg",
        "version": "0.2.9",
    }
    wf.set_trigger_version("seg", "1.0.0")
    assert http.put.call_args.args[0] == f"{GW}/gw/workflows/seg/triggers"
    assert http.put.call_args.kwargs["json_body"] == {"version": "1.0.0"}
    wf.set_trigger_version("seg", None)
    assert http.put.call_args.kwargs["json_body"] == {"version": None}


def test_save_run_definition_pins_the_version():
    wf, http = _client(request={})
    wf.save_run_definition("nightly", "seg@1.2.0", params={"n": 1})
    body = http.request.call_args.kwargs["json_body"]
    assert body["workflow_name"] == "seg"
    assert body["version"] == "1.2.0"


def test_status_and_results_map_the_run_pin():
    execution = {"execution_id": "e1", "workflow_name": "seg", "status": "running"}
    execution.update(workflow_version="1.2.0", source_sha256=DIGEST)
    wf, http = _client(get={"execution": execution})
    status = wf.status("e1")
    assert (status.workflow_version, status.source_sha256) == ("1.2.0", DIGEST)
    http.get.return_value = {
        "execution_id": "e1",
        "execution_status": "succeeded",
        "workflow_name": "seg",
        "workflow_version": "1.2.0",
        "source_sha256": DIGEST,
    }
    result = wf.get_results("e1")
    assert (result.workflow_name, result.workflow_version) == ("seg", "1.2.0")


def test_upload_result_carries_version_warnings_and_unchanged_is_accepted():
    wf, _http = _client()
    result = wf._workflow_upload_result_from_response(
        {
            "workflow_name": "seg",
            "status": "unchanged",
            "version": "1.2.0",
            "source_sha256": DIGEST,
            "warnings": ["name ends in a version"],
        },
        "seg",
    )
    assert result.accepted is True
    assert (result.version, result.source_sha256) == ("1.2.0", DIGEST)
    assert result.warnings == ["name ends in a version"]


def test_export_target_names_the_version(tmp_path):
    assert _export_target("seg", tmp_path, "1.2.0") == tmp_path / "seg@1.2.0.zip"
    assert _export_target("seg", tmp_path) == tmp_path / "seg.zip"


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse(*argv: str) -> Namespace:
    parser = ArgumentParser()
    cli.WorkflowCommand().register(parser.add_subparsers(dest="command"))
    return parser.parse_args(["workflow", *argv])


def _trigger_args(name: str, version: str | None = None) -> Namespace:
    return Namespace(
        workflow_name=name,
        version=version,
        param=[],
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )


def test_parser_accepts_version_flags():
    assert _parse("trigger", "seg", "--version", "1.0.0").version == "1.0.0"
    assert _parse("show", "seg@1.0.0").workflow_name == "seg@1.0.0"
    assert _parse("list", "--all-versions").all_versions is True
    adopt = _parse("adopt", "seg-v1", "--as", "seg", "--version", "1.0.0")
    assert (adopt.from_name, adopt.new_name, adopt.version) == ("seg-v1", "seg", "1.0.0")
    assert _parse("triggers", "seg", "--pin", "1.0.0").pin == "1.0.0"
    assert _parse("definition", "save", "n", "--workflow", "seg", "--version", "1").version == "1"


def test_cli_trigger_splits_the_reference(capsys):
    wf = MagicMock()
    wf.trigger.return_value = WorkflowResult(
        execution_id="e1", workflow_name="seg", status="submitted", workflow_version="1.2.0"
    )
    assert cli._cmd_trigger(wf, _trigger_args("seg@1.2.0")) == 0
    kwargs = wf.trigger.call_args.kwargs
    assert (kwargs["workflow_id"], kwargs["version"]) == ("seg", "1.2.0")
    assert "workflow     : seg@1.2.0" in capsys.readouterr().out


def test_cli_trigger_conflict_is_refused_by_name(capsys):
    wf = MagicMock()
    assert cli._cmd_trigger(wf, _trigger_args("seg@1.0.0", "2.0.0")) == 2
    wf.trigger.assert_not_called()
    assert "1.0.0" in capsys.readouterr().err


def test_cli_list_shows_version_columns(capsys):
    wf = MagicMock()
    wf.list_workflows.return_value = [
        WorkflowCatalogItem(
            workflow_name="seg",
            name="seg",
            description="segments",
            version="1.2.0",
            versions=["1.2.0", "1.1.0", "1.0.0"],
        )
    ]
    args = Namespace(all_users=False, all_versions=False, name=None, as_json=False)
    assert cli._cmd_list(wf, args) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:4] == ["NAME", "VERSION", "VERSIONS", "DESCRIPTION"]
    assert out[2].split()[:4] == ["seg", "1.2.0", "3", "segments"]
    wf.list_workflows.assert_called_once_with(all_users=False)


def test_cli_list_all_versions(capsys):
    wf = MagicMock()
    wf.list_workflows.return_value = []
    args = Namespace(all_users=False, all_versions=True, name=None, as_json=False)
    cli._cmd_list(wf, args)
    wf.list_workflows.assert_called_once_with(all_users=False, all_versions=True)


def test_cli_list_json_carries_versions(capsys):
    import json

    wf = MagicMock()
    wf.list_workflows.return_value = [
        WorkflowCatalogItem(workflow_name="seg", name="seg", version="1.0.0", versions=["1.0.0"])
    ]
    cli._cmd_list(wf, Namespace(all_users=False, name=None, as_json=True))
    (row,) = json.loads(capsys.readouterr().out)
    assert (row["version"], row["versions"]) == ("1.0.0", ["1.0.0"])


def test_cli_run_triggers_exactly_the_uploaded_version(capsys):
    wf = MagicMock()
    wf.upload.return_value = WorkflowUploadResult(
        workflow_name="seg", status="ok", accepted=True, version="2.0.0", warnings=["w1"]
    )
    wf.trigger.return_value = WorkflowResult(execution_id="e1", workflow_name="seg", status="ok")
    args = Namespace(
        path="/tmp/seg",
        name=None,
        param=[],
        input_file=[],
        project_id=None,
        wait=False,
        as_json=False,
    )
    assert cli._cmd_run(wf, args) == 0
    assert wf.trigger.call_args.kwargs["version"] == "2.0.0"
    captured = capsys.readouterr()
    assert "upload accepted: seg@2.0.0" in captured.out
    assert "w1" in captured.err


def test_cli_upload_prints_version_digest_and_warnings(capsys):
    wf = MagicMock()
    wf.upload.return_value = WorkflowUploadResult(
        workflow_name="seg-v1.0.0",
        status="ok",
        accepted=True,
        version="1.0.0",
        source_sha256=DIGEST,
        warnings=["name ends in a version; use the version field"],
    )
    assert cli._cmd_upload(wf, Namespace(path="/tmp/x", name=None, as_json=False)) == 0
    captured = capsys.readouterr()
    assert "version     : 1.0.0" in captured.out
    assert f"digest      : {DIGEST[:12]}" in captured.out
    assert "use the version field" in captured.err


def test_cli_status_and_results_print_name_at_version(capsys):
    wf = MagicMock()
    wf.status.return_value = WorkflowStatus(
        execution_id="e1",
        workflow_name="seg",
        status="running",
        workflow_version="1.2.0",
        source_sha256=DIGEST,
    )
    cli._cmd_status(wf, Namespace(execution_id="e1", as_json=False))
    out = capsys.readouterr().out
    assert "workflow     : seg@1.2.0" in out
    assert f"digest       : {DIGEST[:12]}" in out
    wf.get_results.return_value = WorkflowResult(
        execution_id="e1", workflow_name="seg", status="succeeded", workflow_version="1.2.0"
    )
    cli._cmd_results(wf, Namespace(execution_id="e1", as_json=False))
    assert "workflow     : seg@1.2.0" in capsys.readouterr().out


def test_watch_shows_the_pin_and_describes_the_pinned_version():
    wf = MagicMock()
    wf.describe.return_value = {"steps": []}
    detail = {
        "status": "running",
        "workflow_name": "seg",
        "workflow_version": "1.0.0",
        "source_sha256": DIGEST,
    }
    frame = cli._watch_frame(wf, detail, "e1")
    assert "workflow: seg@1.0.0" in frame
    assert f"digest: {DIGEST[:12]}" in frame
    wf.describe.assert_called_once_with("seg", version="1.0.0")


def test_cli_show_passes_the_version_and_prints_versions(capsys):
    wf = MagicMock()
    wf.describe.return_value = {
        "workflow_name": "seg",
        "version": "1.0.0",
        "versions": ["1.2.0", "1.0.0"],
        "source_sha256": DIGEST,
        "steps": [],
    }
    wf.list_workflows.return_value = []
    args = Namespace(
        workflow_name="seg", version="1.0.0", graph=False, mermaid=False, as_json=False
    )
    assert cli._cmd_show(wf, args) == 0
    wf.describe.assert_called_once_with("seg", version="1.0.0")
    out = capsys.readouterr().out
    assert "versions    : 1.2.0, 1.0.0" in out
    assert f"digest      : {DIGEST[:12]}" in out


def test_cli_versions_marks_the_default(capsys):
    wf = MagicMock()
    wf.list_workflows.return_value = [
        WorkflowCatalogItem(workflow_name="seg", name="seg", version="1.2.0", source_sha256=DIGEST),
        WorkflowCatalogItem(workflow_name="seg", name="seg", version="1.0.0"),
        WorkflowCatalogItem(workflow_name="other", name="other", version="9.0.0"),
    ]
    wf.describe.return_value = {"version": "1.2.0"}
    assert cli._cmd_versions(wf, Namespace(workflow_name="seg", as_json=False)) == 0
    wf.list_workflows.assert_called_once_with(all_versions=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("seg: 2 version(s)")
    assert lines[2].split()[:3] == ["*", "1.2.0", DIGEST[:12]]
    assert lines[3].split()[0] == "1.0.0"
    assert all("9.0.0" not in line for line in lines)


def test_cli_versions_unknown_name_fails(capsys):
    wf = MagicMock()
    wf.list_workflows.return_value = []
    assert cli._cmd_versions(wf, Namespace(workflow_name="nope", as_json=False)) == 1


def test_cli_adopt(capsys):
    wf = MagicMock()
    wf.adopt.return_value = {"name": "seg", "version": "0.2.9", "source_sha256": DIGEST}
    args = Namespace(from_name="seg-v0.2.9", new_name="seg", version="0.2.9", as_json=False)
    assert cli._cmd_adopt(wf, args) == 0
    wf.adopt.assert_called_once_with("seg-v0.2.9", "seg", "0.2.9")
    assert "adopted seg-v0.2.9 -> seg@0.2.9" in capsys.readouterr().out


def test_cli_triggers_pin_unpin_and_show(capsys):
    wf = MagicMock()
    wf.set_trigger_version.return_value = {"trigger_version": "1.0.0"}
    args = Namespace(workflow_name="seg", pin="1.0.0", unpin=False, as_json=False)
    assert cli._cmd_triggers(wf, args) == 0
    wf.set_trigger_version.assert_called_once_with("seg", "1.0.0", owner=None)
    wf.set_trigger_version.return_value = {"trigger_version": None}
    args = Namespace(workflow_name="seg", pin=None, unpin=True, as_json=False)
    assert cli._cmd_triggers(wf, args) == 0
    assert wf.set_trigger_version.call_args.args == ("seg", None)
    assert "default (highest) version" in capsys.readouterr().out
    wf.list_workflow_triggers.return_value = {
        "triggers": [{"on": "ingest"}],
        "trigger_version": "1.0.0",
    }
    args = Namespace(workflow_name="seg", pin=None, unpin=False, as_json=False)
    cli._cmd_triggers(wf, args)
    assert "fires: seg@1.0.0 (pinned" in capsys.readouterr().out


def test_access_refuses_a_version(capsys):
    wf = MagicMock()
    args = Namespace(workflow_name="seg@1.0.0", private=False, access_set=None)
    assert cli._cmd_access(wf, args) == 2
    assert "seg@1.0.0" in capsys.readouterr().err
    wf._parent.get_workflow_access.assert_not_called()


def test_triggers_of_one_version_and_disabled_old_name(capsys):
    wf = MagicMock()
    wf.list_workflow_triggers.return_value = {
        "version": "1.0.0",
        "triggers": [{"on": "ingest"}],
        "triggers_disabled": True,
        "adopted_as": "seg@0.2.9",
    }
    args = Namespace(workflow_name="seg@1.0.0", version=None, pin=None, unpin=False, as_json=False)
    assert cli._cmd_triggers(wf, args) == 0
    wf.list_workflow_triggers.assert_called_once_with("seg", version="1.0.0")
    out = capsys.readouterr().out
    assert "seg@1.0.0: 1 trigger(s) declared" in out
    assert "disabled: adopted as seg@0.2.9" in out


def test_triggers_pin_with_a_version_reference_is_refused(capsys):
    wf = MagicMock()
    args = Namespace(workflow_name="seg@1.0.0", version=None, pin="2.0.0", unpin=False)
    assert cli._cmd_triggers(wf, args) == 2
    wf.set_trigger_version.assert_not_called()


def test_triggers_pin_for_another_owner():
    wf = MagicMock()
    wf.set_trigger_version.return_value = {"trigger_version": "1.0.0"}
    args = Namespace(workflow_name="seg", pin="1.0.0", unpin=False, owner="bob", as_json=False)
    assert cli._cmd_triggers(wf, args) == 0
    wf.set_trigger_version.assert_called_once_with("seg", "1.0.0", owner="bob")


def test_sdk_trigger_list_and_pin_owner():
    wf, http = _client(get={"triggers": []}, put={})
    wf.list_workflow_triggers("seg@1.0.0")
    assert http.get.call_args.args[0] == f"{GW}/gw/workflows/seg/triggers"
    assert http.get.call_args.kwargs["params"] == {"version": "1.0.0"}
    wf.set_trigger_version("seg", "1.0.0", owner="bob")
    assert http.put.call_args.kwargs["params"] == {"owner": "bob"}


def test_delete_prints_removed_versions_and_deferred_files(capsys):
    wf = MagicMock()
    wf.delete.return_value = {"versions": ["1.0.0", "0.9.0"], "files_deferred": True}
    args = Namespace(workflow_name="seg", version=None, yes=True, as_json=False)
    assert cli._cmd_delete(wf, args) == 0
    out = capsys.readouterr().out
    assert "versions removed: 1.0.0, 0.9.0" in out
    assert "files are kept until" in out


def test_version_error_codes_print_a_hint(capsys):
    wf = MagicMock()
    wf.describe.side_effect = R4Error(
        "Workflow 'seg' has no version 9.9.9", {"code": "workflows.version_not_found"}
    )
    args = Namespace(workflow_name="seg@9.9.9", version=None, graph=False, mermaid=False)
    assert cli._cmd_show(wf, args) == 1
    assert "r4 workflow versions NAME" in capsys.readouterr().err


def test_show_marks_unavailable_code(capsys):
    wf = MagicMock()
    wf.describe.return_value = {"workflow_name": "seg", "version": "0.1", "code": "unavailable"}
    wf.list_workflows.return_value = []
    args = Namespace(
        workflow_name="seg@0.1", version=None, graph=False, mermaid=False, as_json=False
    )
    assert cli._cmd_show(wf, args) == 0
    assert "code        : unavailable" in capsys.readouterr().out


def test_definition_save_reads_the_top_level_pin(capsys):
    wf = MagicMock()
    wf.save_run_definition.return_value = {"status": "ok", "workflow_version": "1.3.0"}
    args = Namespace(
        name="nightly", workflow="seg", version=None, param=[], description="", as_json=False
    )
    assert cli._cmd_def_save(wf, args) == 0
    assert "workflow=seg@1.3.0" in capsys.readouterr().out


def test_adopt_reports_unchanged(capsys):
    wf = MagicMock()
    wf.adopt.return_value = {"name": "seg", "version": "0.2.9", "unchanged": True}
    args = Namespace(from_name="seg-v0.2.9", new_name="seg", version="0.2.9", as_json=False)
    assert cli._cmd_adopt(wf, args) == 0
    assert "unchanged" in capsys.readouterr().out


def test_cli_delete_one_version(capsys):
    wf = MagicMock()
    wf.delete.return_value = {}
    args = Namespace(workflow_name="seg@1.0.0", version=None, yes=True, as_json=False)
    assert cli._cmd_delete(wf, args) == 0
    wf.delete.assert_called_once_with("seg", version="1.0.0")
    assert "version 1.0.0 of workflow 'seg' deleted" in capsys.readouterr().out


def test_cli_definition_save_pins_the_version(capsys):
    wf = MagicMock()
    wf.save_run_definition.return_value = {"definition": {"workflow_version": "1.2.0"}}
    args = Namespace(
        name="nightly",
        workflow="seg@1.2.0",
        version=None,
        param=[],
        description="",
        as_json=False,
    )
    assert cli._cmd_def_save(wf, args) == 0
    assert wf.save_run_definition.call_args.args == ("nightly", "seg")
    assert wf.save_run_definition.call_args.kwargs["version"] == "1.2.0"
    assert "workflow=seg@1.2.0" in capsys.readouterr().out
