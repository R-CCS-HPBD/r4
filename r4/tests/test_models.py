"""Unit tests for the R4 model client."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    """Keep model client tests isolated from host config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


class TestModelClient:
    """Tests for Gateway-backed model operations."""

    def test_build_headers_include_gateway_context(self):
        from r4.config import Config
        from r4.models import ModelClient

        client = ModelClient(
            config=Config(
                gateway_url="http://custom:8080",
                project_id="project-123",
                umrs_username="alice",
            )
        )

        headers = client._build_headers()

        assert headers["X-Project-ID"] == "project-123"
        assert headers["X-User-ID"] == "alice"

    def test_build_headers_use_stored_profile_auth(self):
        from r4.auth import get_auth_manager
        from r4.config import Config
        from r4.models import ModelClient

        get_auth_manager("research").save_api_key("stored-key")

        client = ModelClient(
            config=Config(
                profile="research",
                gateway_url="http://custom:8080",
            )
        )

        headers = client._build_headers()

        assert headers["Authorization"] == "Bearer stored-key"

    def test_stage_model_uses_gateway_models_adapter(self):
        from r4.config import Config
        from r4.models import ModelClient, ModelScope, StageRequest

        response = MagicMock(status_code=202, headers={})
        response.json.return_value = {"job_id": "job-123"}

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.post.return_value = response

            client = ModelClient(
                config=Config(
                    gateway_url="http://custom:8080",
                    project_id="project-123",
                    umrs_username="alice",
                    verify_ssl=False,
                )
            )
            job_id = client.stage_model(
                StageRequest(
                    source_id="Qwen/Qwen2.5-7B-Instruct",
                    model_name="qwen-7b",
                    scope=ModelScope.USER,
                )
            )

        assert job_id == "job-123"
        assert mock_http.post.call_args.args[0] == "http://custom:8080/gw/models/stage"
        assert mock_http.post.call_args.kwargs["headers"]["X-Project-ID"] == "project-123"
        assert mock_http.post.call_args.kwargs["headers"]["X-User-ID"] == "alice"

    def test_get_download_status_uses_gateway_jobs_adapter(self):
        from r4.config import Config
        from r4.models import DownloadJobStatus, ModelClient

        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {
            "job_id": "job-123",
            "project_id": "project-123",
            "user_id": "alice",
            "job_type": "model_download",
            "spec": {
                "model_name": "qwen-7b",
                "target_path": "/shared/users/alice/models/qwen-7b",
            },
            "status": "queued",
            "priority": 0,
            "desired_max_attempts": 2,
            "restart_policy": "on_crash",
            "next_run_at": None,
            "active_attempt_id": None,
            "commit_attempt_id": None,
            "created_at": "2026-04-09T00:00:00Z",
            "updated_at": "2026-04-09T00:00:00Z",
            "last_state_change_at": "2026-04-09T00:00:00Z",
            "attempts": [],
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.get.return_value = response

            client = ModelClient(
                config=Config(
                    gateway_url="http://custom:8080",
                    project_id="project-123",
                    umrs_username="alice",
                    verify_ssl=False,
                )
            )
            status = client.get_download_status("job-123")

        assert mock_http.get.call_args.args[0] == "http://custom:8080/gw/jobs/job-123"
        assert mock_http.get.call_args.kwargs["headers"]["X-Project-ID"] == "project-123"
        assert mock_http.get.call_args.kwargs["headers"]["X-User-ID"] == "alice"
        assert status.status == DownloadJobStatus.PENDING
        assert status.model_name == "qwen-7b"
        assert status.target_path == "/shared/users/alice/models/qwen-7b"
        assert status.message == "Job queued"

    def test_supports_download_tracking_accepts_durable_model_job_ids(self):
        from r4.config import Config
        from r4.models import ModelClient

        client = ModelClient(config=Config(gateway_url="http://custom:8080"))

        assert client.supports_download_tracking("job-copy-12345678") is True

    def test_get_download_status_maps_progress_percent_metrics(self):
        from r4.config import Config
        from r4.models import DownloadJobStatus, ModelClient

        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {
            "job_id": "job-789",
            "project_id": "project-123",
            "user_id": "alice",
            "job_type": "model_download",
            "spec": {
                "model_name": "qwen-7b",
                "target_path": "/shared/users/alice/models/qwen-7b",
            },
            "status": "running",
            "attempts": [
                {
                    "attempt_id": "att-1",
                    "metrics": {
                        "progress_percent": 55.0,
                    },
                }
            ],
        }

        with patch("httpx.Client") as mock_http_client:
            mock_http = MagicMock()
            mock_http_client.return_value.__enter__.return_value = mock_http
            mock_http.get.return_value = response

            client = ModelClient(
                config=Config(
                    gateway_url="http://custom:8080",
                    project_id="project-123",
                    umrs_username="alice",
                    verify_ssl=False,
                )
            )
            status = client.get_download_status("job-789")

        assert status.status == DownloadJobStatus.RUNNING
        assert status.progress == pytest.approx(0.55)

    def test_list_models_via_zmq_normalizes_compact_model_records(self):
        from r4.config import Config, DeployMode, TransportMode
        from r4.models import ModelClient, ModelStatus, ModelType

        transport = MagicMock()
        transport.is_connected = True
        transport.request_model_list.return_value = {
            "status": "ok",
            "models": [
                {
                    "name": "qwen3-8b",
                    "storage_ref": "/models/qwen3-8b",
                    "model_type": "llm",
                    "scope": "system",
                    "description": "Downloaded from Qwen/Qwen3-8B",
                    "registered_at": "2026-04-10T23:25:13.290517",
                }
            ],
        }

        client = ModelClient(
            config=Config(
                deploy_mode=DeployMode.HPC_LOCAL,
                transport_mode=TransportMode.DIRECT,
                umrs_username="user1",
            )
        )
        client._umrs_transport = transport

        models = client.list_models(limit=5)

        assert len(models) == 1
        assert models[0].model_id == "qwen3-8b"
        assert models[0].name == "qwen3-8b"
        assert models[0].storage_path == "/models/qwen3-8b"
        assert models[0].model_type == ModelType.LLM
        assert models[0].status == ModelStatus.AVAILABLE
        transport.request_model_list.assert_called_once_with("user1", {"limit": 5, "offset": 0})
