"""Without a valid JWT, the ZMQ transport never opens a plaintext socket to a remote server.

c2-transport Q1-a (2026-10-21): with UMRS_ZMQ_AUTH_TYPE unset, an expired JWT
or an opaque API key made auto-detection fall through to ``none``, so the
transport opened a NULL (plaintext) socket. Over an SSH tunnel whose ssh -L had
died, any user on the shared login node listening on the tunnel port received
the commands and the upload bytes in clear. A remote target (tunnel mode or a
non-loopback host) now refuses; an explicit UMRS_ZMQ_AUTH_TYPE=none and a
loopback server keep the plaintext path.
"""

from __future__ import annotations

import base64
import json
import threading
import time

import pytest

zmq = pytest.importorskip("zmq")

from r4 import umrs_transport  # noqa: E402
from r4.auth import AuthManager, TokenInfo  # noqa: E402
from r4.errors import R4Error  # noqa: E402
from r4.umrs_transport import UMRSTransport, UMRSTransportConfig  # noqa: E402


def _jwt(exp_in: int) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    payload = {"sub": "alice", "exp": int(time.time()) + exp_in}
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.c2ln"


class _PlaintextListener:
    """A rogue NULL-security ROUTER on 127.0.0.1 that records every frame it gets."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.ROUTER)
        self._sock.setsockopt(zmq.LINGER, 0)
        self.port = self._sock.bind_to_random_port("tcp://127.0.0.1")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            if not self._sock.poll(50):
                continue
            frames = self._sock.recv_multipart()
            self.received.append(frames[-1].decode("utf-8", "replace"))
            self._sock.send_multipart([frames[0], b"READY!"])

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self._sock.close(0)
        self._ctx.term()


_OPEN: list[UMRSTransport] = []


@pytest.fixture
def listener(monkeypatch):
    monkeypatch.delenv("UMRS_ZMQ_AUTH_TYPE", raising=False)
    rogue = _PlaintextListener()
    yield rogue
    while _OPEN:
        _OPEN.pop().disconnect()
    rogue.close()


def _transport(port: int, *, tunnel: bool, host: str = "127.0.0.1") -> UMRSTransport:
    cfg = UMRSTransportConfig(
        server_host=host, control_port=port, tunnel_mode=tunnel, timeout_ms=600, session_bound=False
    )
    transport = UMRSTransport(cfg)
    _OPEN.append(transport)
    return transport


def _store_expired_jwt() -> None:
    AuthManager(profile="default").save_token(
        TokenInfo(access_token=_jwt(-5), expires_at=time.time() - 10)
    )


def test_expired_jwt_through_a_tunnel_refuses_and_sends_nothing(listener):
    _store_expired_jwt()
    transport = _transport(listener.port, tunnel=True)

    assert transport.connect() is False
    time.sleep(0.2)
    assert listener.received == []
    assert isinstance(transport.last_connect_error, R4Error)
    assert "r4 login" in str(transport.last_connect_error)
    assert "UMRS_ZMQ_AUTH_TYPE=none" in str(transport.last_connect_error)


def test_opaque_api_key_through_a_tunnel_refuses_and_sends_nothing(listener):
    AuthManager(profile="default").save_api_key("r4k_opaque_test_key_not_a_jwt")
    transport = _transport(listener.port, tunnel=True)

    assert transport.connect() is False
    time.sleep(0.2)
    assert listener.received == []
    assert isinstance(transport.last_connect_error, R4Error)


def test_expired_jwt_to_a_routable_host_refuses_before_connecting(listener):
    _store_expired_jwt()
    transport = _transport(listener.port, tunnel=False, host="10.255.255.1")

    with pytest.raises(R4Error, match="no valid JWT"):
        transport._apply_zmq_security(object())


def test_explicit_none_still_connects_in_plaintext(listener, monkeypatch):
    monkeypatch.setenv("UMRS_ZMQ_AUTH_TYPE", "none")
    _store_expired_jwt()
    transport = _transport(listener.port, tunnel=True)

    assert transport.connect() is True
    assert listener.received[:1] == ["HAND_CHECK"]


def test_loopback_without_tunnel_keeps_plaintext(listener):
    transport = _transport(listener.port, tunnel=False)

    assert transport.connect() is True
    assert listener.received[:1] == ["HAND_CHECK"]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("127.0.1.1", True),
        ("10.1.2.3", False),
        ("data0.example.org", False),
        ("", False),
    ],
)
def test_loopback_host_detection(host, expected):
    assert umrs_transport._is_loopback_host(host) is expected
