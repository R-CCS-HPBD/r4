"""ZMQ auth settings reach the transport on every r4 client path.

Defect 1 of defect-wave-2026-10-08 (SECURITY): the five sites that build a
transport passed an explicit config carrying only host, port and tunnel mode,
so ``UMRS_ZMQ_AUTH_TYPE`` and the ``UMRS_ZMQ_CURVE_*`` keys were never read.
Worse than ignored: with the variable exported, ``_apply_zmq_security``
skips its JWT session auto-detection, so asking for ``curve`` or ``session``
produced a plaintext connection.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4 import umrs_transport

_KEYS = {
    "UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY": "S" * 40,
    "UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY": "P" * 40,
    "UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY": "K" * 40,
}


class _Recorder:
    """Stands in for UMRSTransport and keeps the config it was handed."""

    built: list = []

    def __init__(self, config=None):
        self.config = config
        _Recorder.built.append(config)

    def connect(self, timeout_ms=None):
        return True

    def request_status_cmd(self, *_args):
        return {}


@pytest.fixture
def curve_env(monkeypatch):
    monkeypatch.setenv("UMRS_ZMQ_AUTH_TYPE", "curve")
    for name, value in _KEYS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(umrs_transport, "UMRSTransport", _Recorder)
    _Recorder.built = []
    return _Recorder.built


def _r4_config():
    return SimpleNamespace(
        use_umrs_transport=True,
        umrs_server_host="10.1.2.3",
        umrs_control_port=6555,
        umrs_tunnel_mode=False,
        umrs_username="scientist",
    )


def _assert_curve_survived(built):
    assert len(built) == 1, "the path built no transport"
    config = built[0]
    assert config.zmq_auth_type == "curve"
    assert config.curve_server_public_key == _KEYS["UMRS_ZMQ_CURVE_SERVER_PUBLIC_KEY"]
    assert config.curve_client_public_key == _KEYS["UMRS_ZMQ_CURVE_CLIENT_PUBLIC_KEY"]
    assert config.curve_client_secret_key == _KEYS["UMRS_ZMQ_CURVE_CLIENT_SECRET_KEY"]
    # The r4 profile still decides where to connect.
    assert (config.server_host, config.control_port, config.tunnel_mode) == (
        "10.1.2.3",
        6555,
        False,
    )


def test_client_transport(curve_env):
    from r4.client import Client

    client = Client.__new__(Client)
    client._umrs_transport = None
    client._config = _r4_config()
    _ = client.transport
    _assert_curve_survived(curve_env)


def test_data_client_transport(curve_env):
    from r4.data import DataClient

    data = DataClient.__new__(DataClient)
    data._umrs_transport = None
    data._parent = SimpleNamespace(config=_r4_config())
    _ = data._transport
    _assert_curve_survived(curve_env)


def test_model_client_transport(curve_env):
    from r4.models import ModelClient

    models = ModelClient(config=_r4_config())
    _ = models._transport
    _assert_curve_survived(curve_env)


def test_diagnostics_probe(curve_env):
    from r4._cli.diagnostics import _probe_umrs_zmq

    assert _probe_umrs_zmq(_r4_config())["ok"] is True
    _assert_curve_survived(curve_env)


def test_tool_availability_probe(curve_env):
    from r4.companion.tool_availability import _upload_transport_available

    _upload_transport_available(SimpleNamespace(config=_r4_config()))
    _assert_curve_survived(curve_env)


def test_for_server_keeps_env_auth_and_overrides_the_endpoint(monkeypatch):
    monkeypatch.setenv("UMRS_ZMQ_AUTH_TYPE", "session")
    monkeypatch.setenv("UMRS_TIMEOUT_MS", "9000")
    config = umrs_transport.UMRSTransportConfig.for_server(
        server_host="h", control_port=7000, tunnel_mode=True
    )
    assert (config.zmq_auth_type, config.timeout_ms) == ("session", 9000)
    assert (config.server_host, config.control_port, config.tunnel_mode) == ("h", 7000, True)


def test_curve_keys_reach_the_socket(monkeypatch):
    """End to end on one path: the socket is configured for CURVE, not left plaintext."""
    from r4.client import Client

    monkeypatch.setenv("UMRS_ZMQ_AUTH_TYPE", "curve")
    for name, value in _KEYS.items():
        monkeypatch.setenv(name, value)
    client = Client.__new__(Client)
    client._umrs_transport = None
    client._config = _r4_config()
    socket = SimpleNamespace()
    client.transport._apply_zmq_security(socket)
    assert socket.curve_serverkey == b"S" * 40
    assert socket.curve_publickey == b"P" * 40
    assert socket.curve_secretkey == b"K" * 40
