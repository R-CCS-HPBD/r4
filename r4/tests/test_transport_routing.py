"""
Tests for transport routing logic (DeployMode / TransportMode).

Validates that Config.should_use_zmq() correctly routes operations
to the appropriate transport based on deployment and transport mode settings.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from r4.config import Config, DeployMode, TransportMode

# =============================================================================
# DeployMode / TransportMode enum tests
# =============================================================================


class TestDeployModeEnum:
    """Tests for DeployMode enum values."""

    def test_cloud_value(self):
        assert DeployMode.CLOUD.value == "cloud"

    def test_hpc_local_value(self):
        assert DeployMode.HPC_LOCAL.value == "hpc-local"

    def test_hybrid_value(self):
        assert DeployMode.HYBRID.value == "hybrid"


class TestTransportModeEnum:
    """Tests for TransportMode enum values."""

    def test_gateway_value(self):
        assert TransportMode.GATEWAY.value == "gateway"

    def test_direct_value(self):
        assert TransportMode.DIRECT.value == "direct"

    def test_auto_value(self):
        assert TransportMode.AUTO.value == "auto"


# =============================================================================
# Config defaults for deploy/transport mode
# =============================================================================


class TestConfigDefaults:
    """Tests for default deploy/transport mode values."""

    def test_default_deploy_mode(self):
        config = Config()
        assert config.deploy_mode == DeployMode.HYBRID

    def test_default_transport_mode(self):
        config = Config()
        assert config.transport_mode == TransportMode.AUTO


# =============================================================================
# Config.from_env() loading
# =============================================================================


class TestConfigFromEnvModes:
    """Tests for loading deploy/transport modes from environment."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def test_from_env_cloud_mode(self):
        with patch.dict(os.environ, {"R4_DEPLOY_MODE": "cloud"}, clear=False):
            config = Config.from_env()
            assert config.deploy_mode == DeployMode.CLOUD

    def test_from_env_hpc_local_mode(self):
        with patch.dict(os.environ, {"R4_DEPLOY_MODE": "hpc-local"}, clear=False):
            config = Config.from_env()
            assert config.deploy_mode == DeployMode.HPC_LOCAL

    def test_from_env_hpc_local_mode_underscore(self):
        with patch.dict(os.environ, {"R4_DEPLOY_MODE": "hpc_local"}, clear=False):
            config = Config.from_env()
            assert config.deploy_mode == DeployMode.HPC_LOCAL

    def test_from_env_hybrid_mode(self):
        with patch.dict(os.environ, {"R4_DEPLOY_MODE": "hybrid"}, clear=False):
            config = Config.from_env()
            assert config.deploy_mode == DeployMode.HYBRID

    def test_from_env_transport_gateway(self):
        with patch.dict(os.environ, {"R4_TRANSPORT_MODE": "gateway"}, clear=False):
            config = Config.from_env()
            assert config.transport_mode == TransportMode.GATEWAY

    def test_from_env_transport_direct(self):
        with patch.dict(os.environ, {"R4_TRANSPORT_MODE": "direct"}, clear=False):
            config = Config.from_env()
            assert config.transport_mode == TransportMode.DIRECT

    def test_from_env_transport_auto(self):
        with patch.dict(os.environ, {"R4_TRANSPORT_MODE": "auto"}, clear=False):
            config = Config.from_env()
            assert config.transport_mode == TransportMode.AUTO

    def test_from_env_unknown_deploy_mode_defaults_hybrid(self):
        with patch.dict(os.environ, {"R4_DEPLOY_MODE": "invalid"}, clear=False):
            config = Config.from_env()
            assert config.deploy_mode == DeployMode.HYBRID


# =============================================================================
# should_use_zmq() — TransportMode overrides
# =============================================================================


class TestShouldUseZmqOverrides:
    """Tests for explicit TransportMode override behavior."""

    def test_gateway_mode_blocks_all_zmq(self):
        config = Config(transport_mode=TransportMode.GATEWAY)
        assert config.should_use_zmq("job_list") is False
        assert config.should_use_zmq("model_list") is False
        assert config.should_use_zmq("file_upload") is False

    def test_direct_mode_allows_eligible_ops(self):
        config = Config(transport_mode=TransportMode.DIRECT)
        assert config.should_use_zmq("job_list") is True
        assert config.should_use_zmq("model_list") is True
        assert config.should_use_zmq("file_upload") is True

    def test_direct_mode_blocks_gateway_only_ops(self):
        config = Config(transport_mode=TransportMode.DIRECT)
        assert config.should_use_zmq("chat") is False
        assert config.should_use_zmq("mcp_call_tool") is False

    def test_disabled_transport_blocks_all(self):
        config = Config(use_umrs_transport=False, transport_mode=TransportMode.DIRECT)
        assert config.should_use_zmq("job_list") is False
        assert config.should_use_zmq("file_upload") is False


# =============================================================================
# should_use_zmq() — Cloud mode (AUTO)
# =============================================================================


class TestShouldUseZmqCloudMode:
    """Tests for Cloud deploy mode with AUTO transport."""

    def _cloud_config(self):
        return Config(
            deploy_mode=DeployMode.CLOUD,
            transport_mode=TransportMode.AUTO,
        )

    def test_cloud_allows_file_upload_zmq(self):
        assert self._cloud_config().should_use_zmq("file_upload") is True

    def test_cloud_allows_file_download_zmq(self):
        assert self._cloud_config().should_use_zmq("file_download") is True

    def test_cloud_allows_export_embeddings_zmq(self):
        assert self._cloud_config().should_use_zmq("export_embeddings") is True

    def test_cloud_blocks_job_list_zmq(self):
        assert self._cloud_config().should_use_zmq("job_list") is False

    def test_cloud_blocks_model_list_zmq(self):
        assert self._cloud_config().should_use_zmq("model_list") is False

    def test_cloud_blocks_workflow_run_zmq(self):
        assert self._cloud_config().should_use_zmq("workflow_run") is False

    def test_cloud_blocks_chat_zmq(self):
        assert self._cloud_config().should_use_zmq("chat") is False


# =============================================================================
# should_use_zmq() — HPC-Local mode (AUTO)
# =============================================================================


class TestShouldUseZmqHpcLocalMode:
    """Tests for HPC-Local deploy mode with AUTO transport."""

    def _hpc_config(self):
        return Config(
            deploy_mode=DeployMode.HPC_LOCAL,
            transport_mode=TransportMode.AUTO,
        )

    def test_hpc_allows_job_list_zmq(self):
        assert self._hpc_config().should_use_zmq("job_list") is True

    def test_hpc_allows_model_list_zmq(self):
        assert self._hpc_config().should_use_zmq("model_list") is True

    def test_hpc_allows_model_register_zmq(self):
        assert self._hpc_config().should_use_zmq("model_register") is True

    def test_hpc_allows_model_download_zmq(self):
        assert self._hpc_config().should_use_zmq("model_download") is True

    def test_hpc_allows_job_submit_zmq(self):
        assert self._hpc_config().should_use_zmq("job_submit") is True

    def test_hpc_allows_job_cancel_zmq(self):
        assert self._hpc_config().should_use_zmq("job_cancel") is True

    def test_hpc_allows_workflow_list_zmq(self):
        assert self._hpc_config().should_use_zmq("workflow_list") is True

    def test_hpc_allows_workflow_run_zmq(self):
        assert self._hpc_config().should_use_zmq("workflow_run") is True

    def test_hpc_allows_workflow_upload_zmq(self):
        assert self._hpc_config().should_use_zmq("workflow_upload") is True

    def test_hpc_allows_file_upload_zmq(self):
        assert self._hpc_config().should_use_zmq("file_upload") is True

    def test_hpc_allows_status_zmq(self):
        assert self._hpc_config().should_use_zmq("status") is True

    def test_hpc_allows_export_embeddings_zmq(self):
        assert self._hpc_config().should_use_zmq("export_embeddings") is True

    def test_hpc_blocks_chat_zmq(self):
        assert self._hpc_config().should_use_zmq("chat") is False

    def test_hpc_blocks_mcp_zmq(self):
        assert self._hpc_config().should_use_zmq("mcp_list_tools") is False


# =============================================================================
# should_use_zmq() — Hybrid mode (AUTO)
# =============================================================================


class TestShouldUseZmqHybridMode:
    """Tests for Hybrid deploy mode with AUTO transport."""

    def _hybrid_config(self):
        return Config(
            deploy_mode=DeployMode.HYBRID,
            transport_mode=TransportMode.AUTO,
        )

    def test_hybrid_allows_file_upload_zmq(self):
        assert self._hybrid_config().should_use_zmq("file_upload") is True

    def test_hybrid_allows_file_download_zmq(self):
        assert self._hybrid_config().should_use_zmq("file_download") is True

    def test_hybrid_allows_status_zmq(self):
        assert self._hybrid_config().should_use_zmq("status") is True

    def test_hybrid_allows_export_embeddings_zmq(self):
        assert self._hybrid_config().should_use_zmq("export_embeddings") is True

    def test_hybrid_blocks_job_list_zmq(self):
        assert self._hybrid_config().should_use_zmq("job_list") is False

    def test_hybrid_blocks_model_list_zmq(self):
        assert self._hybrid_config().should_use_zmq("model_list") is False

    def test_hybrid_blocks_workflow_run_zmq(self):
        assert self._hybrid_config().should_use_zmq("workflow_run") is False

    def test_hybrid_blocks_workflow_upload_zmq(self):
        assert self._hybrid_config().should_use_zmq("workflow_upload") is False

    def test_hybrid_blocks_chat_zmq(self):
        assert self._hybrid_config().should_use_zmq("chat") is False


# =============================================================================
# UMRSTransport command methods (mocked ZMQ)
# =============================================================================


class TestUMRSTransportCommands:
    """Tests for UMRSTransport ZMQ command methods with mocked sockets."""

    @pytest.fixture
    def transport(self):
        """Create a transport with mocked ZMQ connection."""
        from r4.umrs_transport import ZEROMQ_AVAILABLE, UMRSTransport, UMRSTransportConfig

        if not ZEROMQ_AVAILABLE:
            pytest.skip("ZeroMQ not installed")

        t = UMRSTransport(UMRSTransportConfig())
        # Mock internal state as connected
        t._connected = True
        t._context = MagicMock()
        t._control_socket = MagicMock()
        # Prevent real CURVE key negotiation during tests
        t._apply_zmq_security = MagicMock()
        return t

    def test_send_command_builds_wire_format(self, transport):
        """Test that _send_command sends the correct wire format."""
        import json

        # Mock recv response
        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.return_value = [
            json.dumps({"status": "ok", "jobs": []}).encode()
        ]

        result = transport._send_command("JOB_LIST", "testuser", '{"limit": 10}')
        parsed = json.loads(result)

        assert parsed["status"] == "ok"
        # Verify send was called with correct wire format
        transport._control_socket.send_string.assert_called_once()
        sent = transport._control_socket.send_string.call_args[0][0]
        assert sent.startswith("JOB_LIST:testuser:")

    def test_send_command_json_returns_dict(self, transport):
        """Test that _send_command_json returns parsed dict."""
        import json

        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.return_value = [
            json.dumps({"status": "ok", "models": [{"name": "test"}]}).encode()
        ]

        result = transport._send_command_json("MODEL_LIST", "testuser", {"limit": 10})
        assert isinstance(result, dict)
        assert result["status"] == "ok"

    def test_request_model_list(self, transport):
        """Test request_model_list sends MODEL_LIST command."""
        import json

        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.return_value = [
            json.dumps({"status": "ok", "models": []}).encode()
        ]

        result = transport.request_model_list("user1", {"scope": "system"})
        assert result["status"] == "ok"

    def test_request_job_submit(self, transport):
        """Test request_job_submit sends JOB_SUBMIT command."""
        import json

        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.return_value = [
            json.dumps({"status": "ok", "job_id": "j-123"}).encode()
        ]

        result = transport.request_job_submit(
            "user1",
            {
                "job_type": "index_build",
                "project_id": "proj-1",
            },
        )
        assert result["status"] == "ok"
        assert result["job_id"] == "j-123"

    def test_request_workflow_list(self, transport):
        """Test request_workflow_list sends LIST_WORKFLOWS command."""
        import json

        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.return_value = [
            json.dumps({"status": "ok", "workflows": []}).encode()
        ]

        result = transport.request_workflow_list("user1")
        assert result["status"] == "ok"

    def test_request_workflow_upload(self, transport, tmp_path):
        """Test request_workflow_upload negotiates data channel and streams archive."""
        archive = tmp_path / "workflow.zip"
        archive.write_bytes(b"zip-content")

        # Mock data socket returned by _create_data_channel
        mock_data_socket = MagicMock()
        transport._context.socket.return_value = mock_data_socket

        # First recv_multipart: CREATE_DATA_QUEUE reply (tcp://host:port)
        # Second recv_multipart: UPLOAD_WORKFLOW ACKNOWLEDGE
        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.side_effect = [
            [b"tcp://127.0.0.1:6000"],  # CREATE_DATA_QUEUE reply
            [b"ACKNOWLEDGE"],  # UPLOAD_WORKFLOW reply
        ]

        result = transport.request_workflow_upload("user1", "wf-one", str(archive))

        assert result["status"] == "ok"
        # Verify UPLOAD_WORKFLOW command was sent on control
        send_calls = transport._control_socket.send_string.call_args_list
        assert any("UPLOAD_WORKFLOW:user1:wf-one" in str(c) for c in send_calls)
        # Verify data was sent via negotiated data socket
        sent_payloads = [call.args[0] for call in mock_data_socket.send.call_args_list]
        assert sent_payloads[-1] == b"__END_OF_FILE__"

    def test_send_command_not_connected_raises(self):
        """Test that _send_command raises when not connected."""
        from r4.errors import R4Error
        from r4.umrs_transport import ZEROMQ_AVAILABLE, UMRSTransport, UMRSTransportConfig

        if not ZEROMQ_AVAILABLE:
            pytest.skip("ZeroMQ not installed")

        t = UMRSTransport(UMRSTransportConfig())
        with pytest.raises(R4Error, match="Not connected"):
            t._send_command("STATUS", "user1")

    def test_client_id_sanitized(self, transport):
        """Test that colons in client_id are replaced."""
        import json

        transport._control_socket.getsockopt.return_value = 8000
        transport._control_socket.recv_multipart.return_value = [
            json.dumps({"status": "ok"}).encode()
        ]

        transport._send_command("STATUS", "user:with:colons", "test")
        sent = transport._control_socket.send_string.call_args[0][0]
        # Colons in client_id should be replaced with underscores
        assert "user_with_colons" in sent
