"""Tests for the UI-independent vision UI flow logic (r4 vision ui)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.errors import R4Error
from r4.vision_ui import (
    _parse_config_json,
    _task_id_from_submit,
    fetch_previews,
    poll_until_terminal,
    run_inference_flow,
)


def _submit_response(outputs=None):
    return {
        "endpoint_id": "job-1",
        "endpoint_model": "hmae-pretrained",
        "adapter": "hmae",
        "task": {"task_id": "task-1", "status": "in_progress"},
        "outputs": outputs
        or [
            {
                "bucket": "vision-results",
                "key": "project-a/images/cell_recon.png",
                "uri": "s3://vision-results/project-a/images/cell_recon.png",
            },
            {
                "bucket": "vision-results",
                "key": "project-a/images/cell_recon_results.pkl",
                "uri": "s3://vision-results/project-a/images/cell_recon_results.pkl",
            },
        ],
    }


def test_task_id_from_submit_extracts_nested_task_id():
    assert _task_id_from_submit(_submit_response()) == "task-1"


def test_task_id_from_submit_raises_without_task():
    with pytest.raises(R4Error):
        _task_id_from_submit({"status": "ok"})


def test_poll_until_terminal_returns_on_success():
    client = MagicMock()
    client.status.side_effect = [
        {"status": "in_progress"},
        {"status": "success"},
    ]

    status = poll_until_terminal(client, "task-1", poll_interval=0.0, timeout=5.0)

    assert status == "success"
    assert client.status.call_count == 2


def test_poll_until_terminal_times_out():
    client = MagicMock()
    client.status.return_value = {"status": "in_progress"}

    status = poll_until_terminal(client, "task-1", poll_interval=0.0, timeout=0.0)

    assert status == "timeout"


def test_fetch_previews_downloads_only_image_artifacts(tmp_path):
    client = MagicMock()
    client.download_file.return_value = b"png-bytes"
    outputs = _submit_response()["outputs"]

    previews = fetch_previews(client, outputs, dest_dir=str(tmp_path))

    assert len(previews) == 1
    local_path, uri = previews[0]
    assert uri == "s3://vision-results/project-a/images/cell_recon.png"
    assert local_path.endswith("cell_recon.png")
    client.download_file.assert_called_once_with("project-a/images/cell_recon.png")


def test_fetch_previews_skips_missing_artifacts(tmp_path):
    client = MagicMock()
    client.download_file.side_effect = R4Error("not found")
    outputs = _submit_response()["outputs"]

    previews = fetch_previews(client, outputs, dest_dir=str(tmp_path))

    assert previews == []


def test_run_inference_flow_returns_s3_uris_and_previews(tmp_path, monkeypatch):
    client = MagicMock()
    client.submit.return_value = _submit_response()
    client.status.return_value = {"status": "success"}
    client.download_file.return_value = b"png-bytes"
    monkeypatch.setattr("r4.vision_ui.tempfile.mkdtemp", lambda prefix: str(tmp_path))

    outcome = run_inference_flow(
        client,
        "/tmp/cell.png",
        adapter="hmae",
        inference_config={"task": "reconstruction"},
        poll_interval=0.0,
        timeout=5.0,
    )

    assert outcome["task_id"] == "task-1"
    assert outcome["status"] == "success"
    assert outcome["output_uris"] == [
        "s3://vision-results/project-a/images/cell_recon.png",
        "s3://vision-results/project-a/images/cell_recon_results.pkl",
    ]
    assert len(outcome["previews"]) == 1
    client.submit.assert_called_once_with(
        "/tmp/cell.png",
        model=None,
        endpoint_id=None,
        adapter="hmae",
        inference_config={"task": "reconstruction"},
    )


def test_run_inference_flow_skips_previews_on_failure():
    client = MagicMock()
    client.submit.return_value = _submit_response()
    client.status.return_value = {"status": "failure"}

    outcome = run_inference_flow(
        client, "/tmp/cell.png", adapter="hmae", poll_interval=0.0, timeout=5.0
    )

    assert outcome["status"] == "failure"
    assert outcome["previews"] == []
    client.download_file.assert_not_called()


def test_parse_config_json_accepts_empty_and_objects():
    assert _parse_config_json("") == {}
    assert _parse_config_json('{"task": "mae"}') == {"task": "mae"}
    with pytest.raises(ValueError):
        _parse_config_json('["not", "an", "object"]')


def test_merge_ui_config_dedicated_fields_win():
    from r4.vision_ui import merge_ui_config

    merged = merge_ui_config(
        {"prompt": "old", "threshold": 0.5},
        prompt="cell",
        task="mae_reconstruction",
    )
    assert merged == {
        "prompt": "cell",
        "task": "mae_reconstruction",
        "threshold": 0.5,
    }


def test_merge_ui_config_blank_fields_leave_config_untouched():
    from r4.vision_ui import merge_ui_config

    original = {"task": "reconstruction"}
    merged = merge_ui_config(original, prompt="  ", task="")
    assert merged == {"task": "reconstruction"}
    assert merged is not original
