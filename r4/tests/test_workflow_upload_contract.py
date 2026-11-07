"""The workflow upload contract on the client (2026-10-02).

`r4 workflow upload` refuses before the round trip what UMRS would refuse —
r4_contracts.workflow_package: a description and every step's depends_on
are mandatory — and `r4 workflow lint <bundle>` lists the same problems
with line numbers (warnings never fail it).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from r4.errors import R4Error
from r4.workflow_lint import format_diagnostics, lint_json_file
from r4.workflows import WorkflowClient

_GOOD = {
    "name": "seg",
    "version": "1.0",
    "description": "Segments an image, then measures each region.",
    "source": {"type": "local_path", "path": "."},
    "steps": [
        {"name": "segment", "entrypoint": "steps:Segment", "depends_on": []},
        {"name": "measure", "entrypoint": "steps:Measure", "depends_on": ["segment"]},
    ],
}


def _off_contract() -> dict:
    wf = json.loads(json.dumps(_GOOD))
    wf["description"] = ""
    del wf["steps"][1]["depends_on"]
    return wf


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def _bundle(tmp_path: Path, manifest: dict, name: str = "seg") -> Path:
    bundle = tmp_path / name
    bundle.mkdir()
    (bundle / "workflow.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (bundle / "steps.py").write_text("class Segment: pass\nclass Measure: pass\n", encoding="utf-8")
    return bundle


def _client():
    from r4.config import Config, TransportMode

    transport = MagicMock()
    transport.request_workflow_upload.return_value = {
        "status": "ok",
        "workflow_name": "seg",
        "accepted": True,
        "source_path": "/srv/seg",
        "message": "uploaded",
    }
    parent = MagicMock()
    parent.config = Config(
        project_id="p",
        gateway_url="http://gw:8000",
        umrs_username="alice",
        transport_mode=TransportMode.DIRECT,
    )
    parent.transport = transport
    parent._ensure_transport_connected.return_value = True
    parent._get_username.return_value = "alice"
    return WorkflowClient(parent), transport


def test_an_off_contract_bundle_is_refused_before_anything_is_sent(tmp_path):
    client, transport = _client()
    bundle = _bundle(tmp_path, _off_contract())
    with pytest.raises(R4Error) as exc:
        client.upload(str(bundle))
    message = str(exc.value)
    assert "does not meet the upload contract" in message
    assert "(1) description: missing" in message
    assert '(2) steps[1] "measure".depends_on: missing' in message
    assert f"r4 workflow lint {bundle}" in message
    transport.request_workflow_upload.assert_not_called()


def test_an_off_contract_zip_is_refused_too(tmp_path):
    client, transport = _client()
    archive = tmp_path / "seg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "seg/workflow.json", json.dumps(_off_contract())
        )  # one level deep, as UMRS reads it
    with pytest.raises(R4Error, match="description: missing"):
        client.upload(str(archive))
    transport.request_workflow_upload.assert_not_called()


def test_a_bundle_meeting_the_contract_is_packed_for_upload(tmp_path):
    client, _transport = _client()
    archive, name, cleanup = client._prepare_upload_archive(str(_bundle(tmp_path, _GOOD)), None)
    try:
        assert name == "seg" and cleanup
        with zipfile.ZipFile(archive) as zf:
            assert json.loads(zf.read("workflow.json")) == _GOOD
    finally:
        Path(archive).unlink()


def test_lint_lists_the_problems_with_lines(tmp_path):
    bundle = _bundle(tmp_path, _off_contract())
    diagnostics = lint_json_file(str(bundle))
    assert [d.code for d in diagnostics] == ["missing_description", "missing_depends_on"]
    assert all(d.severity == "error" for d in diagnostics)
    text = (bundle / "workflow.json").read_text().splitlines()
    assert '"name": "measure"' in text[diagnostics[1].line - 1]
    assert "error: [missing_depends_on]" in format_diagnostics(diagnostics)


def test_lint_reports_advice_as_warnings_only(tmp_path):
    wf = json.loads(json.dumps(_GOOD))
    wf["steps"][0].update(
        entrypoint="umrs.pipeline_sdk.templates.llm_step:LLMStep",
        config={"model": "{{model}}", "prompt_template": "Answer {{ params.question }}"},
    )
    diagnostics = lint_json_file(str(_bundle(tmp_path, wf) / "workflow.json"))
    assert {d.code for d in diagnostics} == {"unfilled_placeholder", "undeclared_params"}
    assert all(d.severity == "warning" for d in diagnostics)


def test_lint_still_checks_knowledge_drafts_as_drafts(tmp_path):
    draft = tmp_path / "draft.json"
    draft.write_text(
        json.dumps({"workflow_id": "not-a-uuid", "name": "d", "steps": []}), encoding="utf-8"
    )
    codes = {d.code for d in lint_json_file(str(draft))}
    assert codes and not codes & {"missing_description", "missing_depends_on"}


def test_the_workflow_ops_reference_example_meets_the_contract():
    """The bundle agents copy (workflow_ops/references/workflow-bundle.md)
    passes the upload contract with no error and no advice."""
    import re

    from r4_contracts.workflow_package import workflow_package_problems

    reference = (
        Path(__file__).resolve().parents[1]
        / "src/r4/companion/builtin/workflow_ops/references/workflow-bundle.md"
    )
    block = re.search(
        r"## workflow\.json \(annotated\)\s+```json\n(.*?)```", reference.read_text(), re.S
    )
    assert block, "annotated workflow.json block missing"
    assert workflow_package_problems(json.loads(block.group(1))) == []
