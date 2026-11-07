"""CURVE session file hygiene (cycle-2 security lane, 2026-10-21).

- A live session file was reused for ANY credential: after ``r4 login`` as
  bob, bob's commands rode alice's session and the server bound them to
  alice (an admin's grants included).
- A malformed file (``[]``, a string expiry, a short key) raised on every
  connect until someone deleted it by hand.
- The post-OIDC-login bootstrap POSTed the fresh token to ``UMRS_BASE_URL``
  or ``127.0.0.1:8082`` instead of the gateway the user logged in to.

Repro: $C2/security/zmq/repro_session.py (sections A, C, D).
"""

from __future__ import annotations

import base64
import json

import pytest

GW = "https://gw.example"


def _jwt(sub: str, nonce: str = "1") -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return ".".join((part({"alg": "none"}), part({"sub": sub, "iss": "gw", "n": nonce}), "sig"))


@pytest.fixture
def posts(tmp_path, monkeypatch):
    from r4.curve_session import CurveSessionManager

    monkeypatch.delenv("UMRS_BASE_URL", raising=False)
    calls: list[str] = []

    def fake_post(url, *, jwt_token, body):
        calls.append(url)
        return {"session_id": f"s{len(calls)}", "server_public_key": "S" * 40, "expires_at": 4.1e9}

    monkeypatch.setattr(CurveSessionManager, "_http_post", staticmethod(fake_post))
    return calls


def _manager(tmp_path):
    from r4.curve_session import CurveSessionManager

    return CurveSessionManager(umrs_base_url=GW, config_dir=tmp_path)


def test_a_session_is_not_reused_under_another_identity(tmp_path, posts):
    first = _manager(tmp_path).ensure_session(_jwt("alice"))
    second = _manager(tmp_path).ensure_session(_jwt("bob"))
    assert len(posts) == 2
    assert second.session_id != first.session_id


def test_a_refreshed_token_of_the_same_identity_reuses_it(tmp_path, posts):
    first = _manager(tmp_path).ensure_session(_jwt("alice", "1"))
    again = _manager(tmp_path).ensure_session(_jwt("alice", "2"))
    assert len(posts) == 1 and again.session_id == first.session_id


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "null",
        '{"session_id": "s", "server_public_key": "%s", "client_public_key": "%s", '
        '"client_secret_key": "%s", "expires_at": "soon"}' % ("k" * 40, "k" * 40, "k" * 40),
        '{"session_id": "s", "server_public_key": "short", "client_public_key": "%s", '
        '"client_secret_key": "%s", "expires_at": 4100000000}' % ("k" * 40, "k" * 40),
    ],
)
def test_a_malformed_session_file_is_discarded(tmp_path, content):
    path = tmp_path / "curve-session.json"
    path.write_text(content)
    assert _manager(tmp_path).load_session() is None
    assert not path.exists()


def test_oidc_bootstrap_registers_at_the_login_gateway(tmp_path, monkeypatch, posts):
    from r4._cli._helpers import _try_zmq_session_bootstrap

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("R4_DEPLOY_MODE", "R4_TRANSPORT_MODE"):
        monkeypatch.delenv(name, raising=False)
    _try_zmq_session_bootstrap(_jwt("alice"), GW)
    assert posts == [f"{GW}/umrs/zmq/session"]
