"""``r4 auth login`` with a key the gateway rejects fails (cycle-2 security lane, 2026-10-21).

Before, a key the gateway answered 401 to printed "✓ Credentials configured"
and exited 0, so scripts and the console treated a typo'd or revoked key as a
working login. The key is still saved (offline login is by design), but the
command says the gateway rejected it and exits 1. An unreachable gateway is
not a rejection: the login succeeds and says it is unverified.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

KEY = "opaque-gateway-key-0123456789"
_ENV = ("R4_API_KEY", "R4_GATEWAY_URL", "R4_PROJECT_ID", "R4_PROFILE", "R4_SOURCE", "UMRS_CONFIG")


def _gateway(status: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            code, body = (200, {"status": "ok"})
            if self.path.endswith("/usage/me"):
                code, body = (status, {"user_id": "alice"} if status == 200 else {})
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    import r4.auth
    import r4.config
    from r4._cli import login

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    for name in (
        "_sync_umrs_client_profile",
        "_ensure_umrs_user_config",
        "_try_zmq_session_bootstrap_with_profile",
        "_maybe_enroll_hpc_after_login",
        "_sync_projects_from_server",
    ):
        monkeypatch.setattr(login, name, lambda *a, **k: None)
    return root


def _login(monkeypatch, url: str) -> int:
    import argparse

    from r4._cli.auth import AuthCommand
    from r4._cli.login import LoginCommand

    parser = argparse.ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY))
    args = parser.parse_args(["auth", "login", "--api-key-stdin", "--gateway-url", url])
    return LoginCommand().handle(args)


def _saved() -> str | None:
    from r4.auth import APIKeyManager

    return APIKeyManager().get_api_key("default")


def test_a_rejected_key_is_saved_but_the_login_fails(xdg, monkeypatch, capsys):
    server = _gateway(401)
    try:
        rc = _login(monkeypatch, f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
    out = capsys.readouterr()
    assert rc == 1
    assert "rejected" in out.err and "Credentials configured" not in out.out
    assert _saved() == KEY


def test_an_accepted_key_is_verified(xdg, monkeypatch, capsys):
    server = _gateway(200)
    try:
        rc = _login(monkeypatch, f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert rc == 0 and "Identity verified: alice" in out and "not verified" not in out


def test_an_unreachable_gateway_saves_an_unverified_login(xdg, monkeypatch, capsys):
    assert _login(monkeypatch, "http://127.0.0.1:9") == 0
    out = capsys.readouterr().out
    assert "Credentials configured" in out and "not verified" in out
    assert _saved() == KEY
