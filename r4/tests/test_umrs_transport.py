"""
Tests for UMRS ZeroMQ Transport.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# Import config first since transport depends on it
from r4.config import Config
from r4.umrs_transport import (
    ZEROMQ_AVAILABLE,
    UMRSTransport,
    UMRSTransportConfig,
    UploadStatus,
    quick_upload,
)


class TestUMRSTransportConfig:
    """Tests for UMRSTransportConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = UMRSTransportConfig()
        assert config.server_host == "127.0.0.1"
        assert config.control_port == 5555
        assert config.tunnel_mode is False
        assert config.timeout_ms == 8000

    def test_config_from_env(self):
        """Test configuration from environment variables."""
        with patch.dict(
            os.environ,
            {
                "UMRS_SERVER_HOST": "192.168.1.100",
                "UMRS_CONTROL_PORT": "6666",
                "UMRS_TUNNEL_MODE": "true",
                "UMRS_TIMEOUT_MS": "5000",
            },
        ):
            config = UMRSTransportConfig.from_env()
            assert config.server_host == "192.168.1.100"
            assert config.control_port == 6666
            assert config.tunnel_mode is True
            assert config.timeout_ms == 5000

    def test_config_from_env_includes_zmq_curve(self):
        """Test CURVE auth configuration from environment variables."""
        with patch.dict(
            os.environ,
            {
                "UMRS_ZMQ_AUTH_TYPE": "curve",
                "UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY": "A" * 40,
                "UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY": "B" * 40,
                "UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY": "C" * 40,
            },
        ):
            config = UMRSTransportConfig.from_env()
            assert config.zmq_auth_type == "curve"
            assert config.curve_server_public_key == "A" * 40
            assert config.curve_client_public_key == "B" * 40
            assert config.curve_client_secret_key == "C" * 40


class TestUMRSTransport:
    """Tests for UMRSTransport."""

    def test_is_available(self):
        """Test ZeroMQ availability check."""
        transport = UMRSTransport()
        assert transport.is_available == ZEROMQ_AVAILABLE

    def test_not_connected_by_default(self):
        """Test transport is not connected by default."""
        transport = UMRSTransport()
        assert transport.is_connected is False

    @pytest.mark.skipif(not ZEROMQ_AVAILABLE, reason="ZeroMQ not installed")
    def test_connect_requires_server(self):
        """Test that connect fails gracefully without server."""
        config = UMRSTransportConfig(
            server_host="127.0.0.1",
            control_port=59999,  # Unlikely to be in use
            timeout_ms=500,
        )
        transport = UMRSTransport(config)

        # Should return False when no server is running
        result = transport.connect(timeout_ms=500)
        assert result is False
        assert transport.is_connected is False

    def test_upload_without_connection(self):
        """Test upload fails when not connected."""
        transport = UMRSTransport()
        result = transport.upload_file("/tmp/test.txt", "test-bucket", "testuser")
        assert result.success is False
        assert "Not connected" in (result.error or "")

    def test_upload_file_not_found(self):
        """Test upload fails for non-existent file."""
        transport = UMRSTransport()
        transport._connected = True  # Force connected state for this test

        result = transport.upload_file("/nonexistent/file.txt", "test-bucket", "testuser")
        assert result.success is False
        assert "not found" in (result.error or "").lower()

    def test_apply_zmq_curve_security_to_client_socket(self):
        """CURVE client key material should be applied to direct ZMQ sockets."""
        config = UMRSTransportConfig(
            zmq_auth_type="curve",
            curve_server_public_key="A" * 40,
            curve_client_public_key="B" * 40,
            curve_client_secret_key="C" * 40,
        )
        transport = UMRSTransport(config)
        socket = MagicMock()

        transport._apply_zmq_security(socket)

        assert socket.curve_serverkey == b"A" * 40
        assert socket.curve_publickey == b"B" * 40
        assert socket.curve_secretkey == b"C" * 40

    def test_apply_zmq_curve_security_rejects_missing_keys(self):
        """CURVE auth should fail closed when key material is incomplete."""
        config = UMRSTransportConfig(zmq_auth_type="curve")
        transport = UMRSTransport(config)

        with pytest.raises(Exception, match="UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY"):
            transport._apply_zmq_security(MagicMock())


class TestUploadStatus:
    """Tests for UploadStatus dataclass."""

    def test_success_status(self):
        """Test successful upload status."""
        status = UploadStatus(
            success=True,
            s3_key="bucket/path/file.txt",
            chunks_sent=10,
        )
        assert status.success is True
        assert status.s3_key == "bucket/path/file.txt"
        assert status.chunks_sent == 10
        assert status.error is None

    def test_failure_status(self):
        """Test failed upload status."""
        status = UploadStatus(
            success=False,
            error="Connection timeout",
        )
        assert status.success is False
        assert status.error == "Connection timeout"
        assert status.s3_key is None


class TestRAGClientUMRSIntegration:
    """Tests for RAGClient UMRS transport integration."""

    def test_config_includes_umrs_settings(self):
        """Test that Config includes UMRS settings."""
        config = Config()
        assert hasattr(config, "use_umrs_transport")
        assert hasattr(config, "umrs_server_host")
        assert hasattr(config, "umrs_control_port")
        assert hasattr(config, "umrs_tunnel_mode")

    def test_config_from_env_includes_umrs(self):
        """Test Config.from_env() includes UMRS settings."""
        with patch.dict(
            os.environ,
            {
                "R4_USE_UMRS_TRANSPORT": "false",
                "UMRS_SERVER_HOST": "10.0.0.1",
                "UMRS_USERNAME": "testuser",
            },
        ):
            config = Config.from_env()
            assert config.use_umrs_transport is False
            assert config.umrs_server_host == "10.0.0.1"
            assert config.umrs_username == "testuser"


class TestQuickUpload:
    """Tests for quick_upload convenience function."""

    def test_quick_upload_file_not_found(self):
        """Test quick_upload with non-existent file."""
        result = quick_upload(
            "/nonexistent/file.txt",
            "test-bucket",
            "testuser",
        )
        # Should fail either due to connection or file not found
        assert result.success is False


# Integration tests (require running UMRS server)
@pytest.mark.integration
class TestUMRSTransportIntegration:
    """Integration tests requiring a running UMRS server."""

    @pytest.fixture
    def transport(self):
        """Create connected transport."""
        config = UMRSTransportConfig.from_env()
        transport = UMRSTransport(config)
        if transport.connect():
            yield transport
            transport.disconnect()
        else:
            pytest.skip("UMRS server not available")

    def test_ping(self, transport):
        """Test ping to UMRS server."""
        assert transport.is_connected is True

    def test_upload_small_file(self, transport, tmp_path):
        """Test uploading a small file."""
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, UMRS!")

        result = transport.upload_file(
            str(test_file),
            "test-bucket",
            "testuser",
            metadata={"type": "test"},
        )

        assert result.success is True
        assert result.chunks_sent > 0

    def test_get_status(self, transport):
        """Test getting UMRS status."""
        status = transport.get_status("testuser")
        assert "status" in status or "summaries" in status


class _FakeControlSocket:
    """Scripted control socket: pops one encoded frame per recv."""

    def __init__(self, replies):
        self._replies = [str(r).encode() for r in replies]
        self.sent = []

    def send_string(self, text):
        self.sent.append(text)

    def recv_multipart(self):
        import zmq

        if not self._replies:
            raise zmq.Again()
        return [self._replies.pop(0)]


class TestVersionedPing:
    """D-04: _ping adopts the shared handshake parser — versioned
    v1.0 replies pass (the bare-compare regression), pre-1.0 compat
    unchanged, garbage falls to the READY? retry honestly."""

    def _transport(self, replies):
        transport = object.__new__(UMRSTransport)
        transport._control_socket = _FakeControlSocket(replies)
        transport._reset_control_socket = lambda: None
        return transport

    def test_versioned_reply_passes(self):
        assert self._transport(["READY!:v1.0"])._ping() is True

    def test_bare_reply_still_passes(self):
        assert self._transport(["READY!"])._ping() is True

    def test_garbage_falls_to_retry_then_fails(self):
        transport = self._transport(["NOT_READY", "ALSO_NOT"])
        assert transport._ping() is False
        assert transport._control_socket.sent == ["HAND_CHECK", "READY?"]

    def test_retry_accepts_versioned_reply(self):
        assert self._transport(["junk", "READY!:v1.0"])._ping() is True
