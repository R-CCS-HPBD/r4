"""r4 never enrols a key for a shared site account (ADR-112 D-1, wave A, 2026-11-08).

Before, ``r4 login`` (both entry points) and the ``r4 onboard`` health check
read ``/health`` ``site_capabilities``; when a site advertised
``slurm_delegation: per_user_ssh`` they offered to append the UMRS service key
to ``~/.ssh/authorized_keys`` with a forced command, promising that "jobs will
use the shared site account if you skip". ADR-112 retires that mode: the
client only reads capabilities and never touches the user's SSH files.
"""

from __future__ import annotations

import io
import json
import threading
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

KEY = "opaque-gateway-key-0123456789"
_ENV = ("R4_API_KEY", "R4_GATEWAY_URL", "R4_PROJECT_ID", "R4_PROFILE", "R4_SOURCE", "UMRS_CONFIG")
_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq1c2VydmljZWtleWZvcnRlc3RzMDAwMDAwMDAw umrs"


def _legacy_caps(tmp_path: Path) -> dict[str, Any]:
    """What a pre-ADR-112 site advertised, with real files behind the paths."""
    pubkey = tmp_path / "umrs_service.pub"
    pubkey.write_text(_PUBKEY + "\n")
    proxy = tmp_path / "slurm-proxy.sh"
    proxy.write_text("#!/bin/sh\n")
    return {
        "scheduler_kind": "slurm-remote",
        "site_name": "legacy-site",
        "slurm_delegation": "per_user_ssh",
        "service_pubkey_path": str(pubkey),
        "slurm_proxy_path": str(proxy),
    }


def _gateway(caps: dict[str, Any]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body: dict[str, Any] = {"status": "ok", "site_capabilities": caps}
            if self.path.endswith("/usage/me"):
                body = {"user_id": "alice"}
            payload = json.dumps(body).encode()
            self.send_response(200)
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
def home(tmp_path, monkeypatch) -> Path:
    """A scratch HOME and config dir; every interactive question answers yes."""
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    import r4.auth
    import r4.config

    monkeypatch.setattr(r4.config, "_config", None)
    monkeypatch.setattr(r4.auth, "_auth_manager", None)
    return home_dir


@pytest.fixture
def prompts(monkeypatch) -> list[str]:
    asked: list[str] = []

    def _answer_yes(msg: str = "") -> str:
        asked.append(msg)
        return "y"

    monkeypatch.setattr("builtins.input", _answer_yes)
    return asked


def _quiet_login(monkeypatch) -> None:
    from r4._cli import login

    for name in (
        "_sync_umrs_client_profile",
        "_ensure_umrs_user_config",
        "_try_zmq_session_bootstrap_with_profile",
        "_sync_projects_from_server",
    ):
        monkeypatch.setattr(login, name, lambda *a, **k: None)


def _auth_login(monkeypatch, url: str) -> None:
    from r4._cli.auth import AuthCommand
    from r4._cli.login import LoginCommand

    parser = ArgumentParser(prog="r4")
    AuthCommand().register(parser.add_subparsers(dest="command"))
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY))
    args = parser.parse_args(["auth", "login", "--api-key-stdin", "--gateway-url", url])
    assert LoginCommand().handle(args) == 0


def _cmd_login(monkeypatch, url: str) -> None:
    from r4._cli.login import LoginCommand, cmd_login

    parser = ArgumentParser(prog="r4")
    LoginCommand().register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["login", "--gateway-url", url])
    args.api_key = KEY
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    cmd_login(args)


def _assert_ssh_untouched(home_dir: Path, text: str, asked: list[str]) -> None:
    assert not (home_dir / ".ssh" / "authorized_keys").exists()
    assert not (home_dir / ".ssh").exists()
    assert "shared site account" not in text
    assert "authorized_keys" not in text
    assert not [q for q in asked if "SSH" in q or "key" in q.lower()], asked


@pytest.mark.parametrize("entry", [_auth_login, _cmd_login], ids=["auth-login", "cmd-login"])
def test_login_never_enrols_the_service_key(entry, home, prompts, tmp_path, monkeypatch, capsys):
    _quiet_login(monkeypatch)
    server = _gateway(_legacy_caps(tmp_path))
    try:
        entry(monkeypatch, f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
    out = capsys.readouterr()
    assert "Identity verified: alice" in out.out  # the login itself went through
    _assert_ssh_untouched(home, out.out + out.err, prompts)


class _LegacySiteClient:
    """A client whose site still advertises ``per_user_ssh``."""

    caps: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def readiness(self) -> dict[str, Any]:
        return {"ready": True}

    def get(self, path: str, **kwargs: Any) -> Any:
        return [] if path.endswith("/mcp/servers") else {"documents": 0}

    def site_capabilities(self) -> dict[str, Any]:
        return dict(self.caps)


def test_onboard_health_check_never_enrols_the_service_key(
    home, prompts, tmp_path, monkeypatch, capsys
):
    import r4.client
    from r4._cli import onboard

    monkeypatch.setattr(_LegacySiteClient, "caps", _legacy_caps(tmp_path))
    monkeypatch.setattr(r4.client, "Client", _LegacySiteClient)
    onboard._maybe_run_onboard_health_check("default", "proj")
    out = capsys.readouterr()
    assert "Gateway is ready" in out.out  # the health check itself ran
    _assert_ssh_untouched(home, out.out + out.err, prompts)


@pytest.mark.parametrize("module", ["r4._cli.onboard", "r4._cli.login"])
def test_no_hpc_enrolment_entry_point_remains(module):
    import importlib

    mod = importlib.import_module(module)
    for name in (
        "_maybe_enroll_hpc",
        "_maybe_enroll_hpc_after_login",
        "_enroll_hpc_ssh",
        "_append_authorized_key",
        "_service_key_fingerprint",
        "_print_service_key_fingerprint",
    ):
        assert not hasattr(mod, name), f"{module}.{name}"
    leftovers = [n for n in dir(mod) if "enroll" in n.lower() or "authorized_key" in n.lower()]
    assert leftovers == []
