"""Unit tests for the R4 workflow client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class _WorkflowParent:
    def __init__(self, config, transport=None, http=None):
        self.config = config
        self.transport = transport
        self._http = http

    def _ensure_transport_connected(self) -> bool:
        return self.transport is not None

    def _get_username(self) -> str:
        return self.config.umrs_username or "alice"

    def _build_headers(self):
        return {
            "X-Project-ID": self.config.project_id,
            "X-User-ID": self._get_username(),
        }

    def _handle_response_errors(self, response):
        response.raise_for_status()

    @property
    def http(self):
        """Lazy HTTPCore, mirrors the real :class:`r4.Client`."""
        if self._http is None:
            from r4._http import HTTPCore

            self._http = HTTPCore(self.config)
        return self._http


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


class TestWorkflowClient:
    def test_upload_directory_via_zmq_uses_workflow_upload_command(self, tmp_path):
        from r4.config import Config, TransportMode
        from r4.workflows import WorkflowClient

        workflow_dir = tmp_path / "ct-analysis"
        workflow_dir.mkdir()
        (workflow_dir / "workflow.json").write_text(
            '{"name": "ct-analysis", "version": "1.0", '
            '"description": "Segments CT slices.", '
            '"source": {"type": "local_path", "path": "."}, '
            '"steps": [{"name": "s1", "entrypoint": "steps:StepOne", "depends_on": []}]}',
            encoding="utf-8",
        )
        (workflow_dir / "steps.py").write_text("class StepOne: pass\n", encoding="utf-8")

        transport = MagicMock()
        transport.request_workflow_upload.return_value = {
            "status": "ok",
            "workflow_name": "ct-analysis",
            "accepted": True,
            "source_path": "/tmp/user_workflows/alice/ct-analysis",
            "message": "uploaded",
        }
        parent = _WorkflowParent(
            Config(
                project_id="project-123",
                gateway_url="http://gateway:8000",
                umrs_username="alice",
                transport_mode=TransportMode.DIRECT,
            ),
            transport=transport,
        )
        client = WorkflowClient(parent)

        result = client.upload(str(workflow_dir))

        transport.request_workflow_upload.assert_called_once()
        call_args = transport.request_workflow_upload.call_args.args
        assert call_args[0] == "alice"
        assert call_args[1] == "ct-analysis"
        assert Path(call_args[2]).suffix == ".zip"
        assert not Path(call_args[2]).exists()
        assert result.workflow_id == "ct-analysis"
        assert result.accepted is True

    def test_upload_zip_via_http_uses_gateway_upload_route(self, tmp_path):
        from r4.config import Config
        from r4.workflows import WorkflowClient

        archive_path = tmp_path / "video-analysis.zip"
        archive_path.write_bytes(b"zip-bytes")

        response = MagicMock(status_code=202, headers={})
        response.json.return_value = {
            "status": "ok",
            "workflow_name": "video-analysis",
            "accepted": True,
            "source_path": "/tmp/user_workflows/alice/video-analysis",
            "message": "uploaded",
        }

        parent = _WorkflowParent(
            Config(
                gateway_url="http://custom:8080",
                project_id="project-123",
                umrs_username="alice",
                verify_ssl=False,
            ),
            transport=None,
        )
        client = WorkflowClient(parent)

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            result = client.upload(str(archive_path))

        assert mock_http.post.call_args.args[0] == "http://custom:8080/gw/workflows/upload"
        assert mock_http.post.call_args.kwargs["data"] == {"workflow_name": "video-analysis"}
        sent_file = mock_http.post.call_args.kwargs["files"]["file"]
        assert sent_file[0] == "video-analysis.zip"
        assert sent_file[2] == "application/zip"
        assert result.workflow_id == "video-analysis"
        assert result.source_path == "/tmp/user_workflows/alice/video-analysis"

    def test_upload_rejects_missing_workflow_manifest(self, tmp_path):
        from r4.config import Config
        from r4.errors import R4Error
        from r4.workflows import WorkflowClient

        workflow_dir = tmp_path / "broken-workflow"
        workflow_dir.mkdir()

        client = WorkflowClient(
            _WorkflowParent(Config(gateway_url="http://custom:8080", project_id="project-123"))
        )

        with pytest.raises(R4Error, match="No workflow.json found"):
            client.upload(str(workflow_dir))

    def test_trigger_via_zmq_uses_umrs_workflow_contract(self):
        from r4.config import Config, TransportMode
        from r4.workflows import WorkflowClient

        transport = MagicMock()
        transport.request_workflow_run.return_value = {
            "status": "ok",
            "workflow_name": "ct-analysis",
            "source_path": "/tmp/workflows/ct-analysis",
            "steps": 3,
            "params": {
                "project_id": "project-123",
                "input_files": ["scan.tif"],
                "model": "example-model",
            },
        }
        parent = _WorkflowParent(
            Config(
                project_id="project-123",
                gateway_url="http://gateway:8000",
                umrs_username="alice",
                transport_mode=TransportMode.DIRECT,
            ),
            transport=transport,
        )
        client = WorkflowClient(parent)

        result = client.trigger(
            "ct-analysis",
            input_files=["scan.tif"],
            parameters={"model": "example-model"},
        )

        transport.request_workflow_run.assert_called_once_with(
            "alice",
            {
                "workflow_name": "ct-analysis",
                "params": {
                    "model": "example-model",
                    "input_files": ["scan.tif"],
                    "project_id": "project-123",
                },
            },
        )
        assert result.workflow_id == "ct-analysis"
        assert result.status == "ok"
        assert result.outputs["steps"] == 3

    def test_trigger_via_http_uses_gateway_workflow_route(self):
        from r4.config import Config
        from r4.workflows import WorkflowClient

        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {
            "status": "ok",
            "workflow_name": "video-analysis",
            "params": {
                "project_id": "project-123",
                "input_files": ["video.mp4"],
                "analysis_type": "event-detection",
            },
            "steps": 2,
        }

        parent = _WorkflowParent(
            Config(
                gateway_url="http://custom:8080",
                project_id="project-123",
                umrs_username="alice",
                verify_ssl=False,
            ),
            transport=None,
        )
        client = WorkflowClient(parent)

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            result = client.trigger(
                "video-analysis",
                input_files=["video.mp4"],
                parameters={"analysis_type": "event-detection"},
            )

        assert (
            mock_http.post.call_args.args[0]
            == "http://custom:8080/gw/workflows/video-analysis/trigger"
        )
        assert mock_http.post.call_args.kwargs["json"] == {
            "parameters": {"analysis_type": "event-detection"},
            "input_files": ["video.mp4"],
            "project_id": "project-123",
        }
        assert result.workflow_id == "video-analysis"
        assert result.outputs["steps"] == 2

    def test_status_and_waiting_now_call_gateway_endpoints(self):
        from r4.config import Config
        from r4.workflows import WorkflowClient

        parent = _WorkflowParent(
            Config(gateway_url="http://gateway:8000", project_id="project-123"),
        )
        # Stub the lazy HTTPCore so we don't need a real socket.
        parent._http = MagicMock()
        parent._http.get.side_effect = [
            {
                "status": "ok",
                "execution": {
                    "execution_id": "exec-1",
                    "workflow_name": "wf",
                    "status": "running",
                    "progress": 0.4,
                    "started_at": 1.0,
                    "finished_at": None,
                    "error": None,
                },
            },
            {
                "status": "ok",
                "execution": {
                    "execution_id": "exec-1",
                    "workflow_name": "wf",
                    "status": "succeeded",
                    "progress": 1.0,
                    "started_at": 1.0,
                    "finished_at": 2.0,
                    "error": None,
                },
            },
            {
                "status": "ok",
                "execution_id": "exec-1",
                "execution_status": "succeeded",
                "results": {"final": {"extra": {"answer": 42}}},
                "artifacts": [{"key": "wf-runner/x.json", "size_bytes": 12}],
                "error": None,
                "progress": 1.0,
            },
        ]
        client = WorkflowClient(parent)

        status = client.status("exec-1")
        assert status.status == "running"
        assert status.progress == pytest.approx(0.4)

        result = client.wait_for_completion("exec-1", poll_interval=0.0)
        assert result.status == "succeeded"
        assert result.outputs["final"]["extra"]["answer"] == 42
        assert result.artifacts == ["wf-runner/x.json"]

    def test_trigger_now_supports_wait(self):
        from r4.config import Config
        from r4.workflows import WorkflowClient

        parent = _WorkflowParent(
            Config(gateway_url="http://gateway:8000", project_id="project-123"),
        )
        parent._http = MagicMock()
        # First call: trigger POST returns execution_id; subsequent calls
        # are the status + results polling.
        parent._http.post.return_value = {
            "execution_id": "exec-9",
            "status": "submitted",
            "workflow_name": "wf",
        }
        parent._http.get.side_effect = [
            {
                "status": "ok",
                "execution": {
                    "execution_id": "exec-9",
                    "workflow_name": "wf",
                    "status": "succeeded",
                    "progress": 1.0,
                },
            },
            {
                "status": "ok",
                "execution_id": "exec-9",
                "execution_status": "succeeded",
                "results": {"final": {"extra": {"ok": True}}},
                "artifacts": [],
            },
        ]
        client = WorkflowClient(parent)

        result = client.trigger("ct-analysis", wait=True)
        assert result.status == "succeeded"
        assert result.execution_id == "exec-9"


class TestAnalysisHelpers:
    """The CT / video helpers carry no built-in model or analysis default."""

    @staticmethod
    def _client():
        from r4.config import Config
        from r4.workflows import WorkflowClient

        client = WorkflowClient(_WorkflowParent(Config(project_id="p")))
        client.trigger = MagicMock(return_value="triggered")
        return client

    @pytest.mark.parametrize("helper", ["trigger_ct_analysis", "trigger_video_analysis"])
    def test_helper_requires_its_choice(self, helper):
        with pytest.raises(TypeError):
            getattr(self._client(), helper)(["in.dat"])

    def test_ct_helper_forwards_model(self):
        client = self._client()
        client.trigger_ct_analysis(["scan.tif"], model="example-model")
        client.trigger.assert_called_once_with(
            "ct-analysis",
            input_files=["scan.tif"],
            parameters={"model": "example-model"},
            project_id=None,
            wait=False,
        )

    def test_video_helper_forwards_analysis_type(self):
        client = self._client()
        client.trigger_video_analysis(["v.mp4"], analysis_type="event-detection")
        client.trigger.assert_called_once_with(
            "video-analysis",
            input_files=["v.mp4"],
            parameters={"analysis_type": "event-detection"},
            project_id=None,
            wait=False,
        )


class TestRunDefinitions:
    """CLI/client surface for persisted run templates (replay without LLM)."""

    def _make_client(self):
        from r4.config import Config
        from r4.workflows import WorkflowClient

        parent = _WorkflowParent(
            Config(gateway_url="http://gateway:8000", project_id="pid", umrs_username="alice"),
            transport=None,
            http=MagicMock(),
        )
        return WorkflowClient(parent), parent.http

    def test_save_run_definition_puts_body(self):
        client, http = self._make_client()
        client.save_run_definition(
            "nightly", "ct-analysis", params={"bucket": "b"}, description="d"
        )

        http.request.assert_called_once()
        method, url = http.request.call_args.args
        assert method == "PUT"
        assert url.endswith("/gw/workflows/run-definitions")
        body = http.request.call_args.kwargs["json_body"]
        assert body == {
            "name": "nightly",
            "workflow_name": "ct-analysis",
            "params": {"bucket": "b"},
            "description": "d",
        }

    def test_trigger_run_definition_replays_named_workflow(self):
        client, http = self._make_client()
        http.get.return_value = {
            "definition": {"name": "nightly", "workflow_name": "ct-analysis", "params": {"a": 1}}
        }
        http.post.return_value = {
            "status": "ok",
            "execution_id": "exec-1",
            "workflow_name": "ct-analysis",
            "execution_status": "submitted",
        }

        result = client.trigger_run_definition("nightly")

        # GET first (validates existence + resolves the workflow name), then POST trigger.
        assert http.get.call_args.args[0].endswith("/gw/workflows/run-definitions/nightly")
        method_url = http.post.call_args.args[0]
        assert method_url.endswith("/gw/workflows/run-definitions/nightly/trigger")
        assert result.execution_id == "exec-1"

    def test_trigger_run_definition_surfaces_error_status(self):
        client, http = self._make_client()
        http.get.return_value = {
            "definition": {"name": "nightly", "workflow_name": "ct-analysis", "params": {}}
        }
        http.post.return_value = {"status": "error", "message": "workflow missing"}

        from r4.errors import R4Error

        with pytest.raises(R4Error, match="workflow missing"):
            client.trigger_run_definition("nightly")

    def test_delete_run_definition_calls_gateway(self):
        client, http = self._make_client()
        client.delete_run_definition("nightly")
        url = http.delete.call_args.args[0]
        assert url.endswith("/gw/workflows/run-definitions/nightly")
