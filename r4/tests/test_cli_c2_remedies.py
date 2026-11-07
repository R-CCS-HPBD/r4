"""Honest CLI errors and remedies (production-quality cycle 2, cli lane, 2026-10-21).

Each case was found by running ``python -m r4`` against a fake gateway or
provider; each test fails on the pre-fix code.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4._cli import _helpers
from r4.errors import APIError, AuthenticationError, R4ConnectionError


def _config():
    return SimpleNamespace(gateway_url="http://gw.test:8080", bound_source=None)


def _preflight_output(monkeypatch, capsys, exc: Exception) -> str:
    class _Client:
        def __init__(self, config=None):
            pass

        def readiness(self):
            raise exc

    monkeypatch.setattr("r4.client.Client", _Client)
    with pytest.raises(SystemExit):
        _helpers._preflight_gateway(_config(), action="job")
    captured = capsys.readouterr()
    return captured.out + captured.err


@pytest.mark.parametrize(
    "exc",
    [
        AuthenticationError(status_code=401),
        AuthenticationError("Access denied (403)", status_code=403),
        APIError("API error: 500: boom", status_code=500),
    ],
)
def test_preflight_does_not_call_an_answering_gateway_unreachable(monkeypatch, capsys, exc):
    text = _preflight_output(monkeypatch, capsys, exc)

    assert "unreachable" not in text
    assert "SSH tunnel" not in text


def test_preflight_rejected_credential_points_at_login(monkeypatch, capsys):
    text = _preflight_output(monkeypatch, capsys, AuthenticationError(status_code=401))

    assert "rejected the credential" in text
    assert "r4 auth login" in text


def test_preflight_transport_failure_still_says_unreachable(monkeypatch, capsys):
    text = _preflight_output(monkeypatch, capsys, R4ConnectionError("Connection refused"))

    assert "Gateway unreachable" in text
    assert "SSH tunnel" in text


def test_403_hint_does_not_send_users_to_the_deprecated_init():
    hint = AuthenticationError("Access denied (403)", status_code=403).hint or ""

    assert "r4 init" not in hint
    assert "r4 config context --project" in hint


# ---- knowledge: no traceback on a gateway error; a project is required ----


def _knowledge(argv: list[str]):
    from r4 import cli

    parser = cli._build_parser()
    args = parser.parse_args(["knowledge", *argv])
    return cli._load_dispatch_registry().get("knowledge").handle(args)


class _FailingKnowledgeClient:
    calls: list[str] = []

    def __init__(self, *a, **kw):
        failing = self

        class _Plane:
            def __getattr__(self, name):
                def _call(*args, **kwargs):
                    failing.calls.append(name)
                    raise APIError("API error: 500: boom", status_code=500)

                return _call

        self.knowledge = _Plane()
        self.data_catalog = _Plane()


@pytest.fixture
def failing_client(monkeypatch):
    _FailingKnowledgeClient.calls = []
    monkeypatch.setattr("r4.client.Client", _FailingKnowledgeClient)
    return _FailingKnowledgeClient.calls


@pytest.mark.parametrize("verb", ["graphs", "inventory", "health"])
def test_knowledge_gateway_error_is_one_line_not_a_traceback(
    monkeypatch, capsys, caplog, failing_client, verb
):
    monkeypatch.setattr("r4._cli.knowledge._current_project_id", lambda: "p1")
    caplog.set_level("WARNING")

    rc = _knowledge([verb])

    assert rc == 1
    assert "API error: 500: boom" in capsys.readouterr().err
    # logger.exception reached the user as a full traceback (lastResort handler).
    assert [r for r in caplog.records if r.levelno >= 30] == []


@pytest.mark.parametrize("verb", ["inventory", "health"])
def test_knowledge_without_a_project_says_so(monkeypatch, capsys, failing_client, verb):
    monkeypatch.setattr("r4._cli.knowledge._current_project_id", lambda: None)

    rc = _knowledge([verb])

    assert rc == 1
    assert "No project specified" in capsys.readouterr().err
    assert failing_client == [], "a request was sent with project None"


# ---- bare groups, `r4 ask`, A2L wording ----


def _run_cli(monkeypatch, argv: list[str]) -> int:
    import sys

    from r4 import cli

    monkeypatch.setattr(sys, "argv", ["r4", *argv])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    code = exc_info.value.code
    return code if isinstance(code, int) else 0


def test_bare_course_prints_usage_not_an_attribute_error(monkeypatch, capsys):
    code = _run_cli(monkeypatch, ["course"])
    err = capsys.readouterr().err

    assert code == 2
    assert "Usage: r4 course create" in err
    assert "Namespace" not in err


def test_bare_sweep_lists_templates(monkeypatch, capsys):
    code = _run_cli(monkeypatch, ["sweep"])
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert "Namespace" not in captured.err


def test_no_cli_text_names_the_nonexistent_r4_ask(monkeypatch, capsys):
    from pathlib import Path

    import r4.cli

    root = Path(r4.cli.__file__).parent
    offenders = [
        f"{path.name}:{n}"
        for path in [root / "cli.py", *sorted((root / "_cli").glob("*.py"))]
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "r4 ask " in line and "there is no `r4 ask`" not in line
    ]
    assert offenders == []
    # …because it really does not exist:
    assert _run_cli(monkeypatch, ["ask", "teamup", "create", "x"]) != 0


@pytest.mark.parametrize("module", ["agent_exec", "artifact"])
def test_unreachable_gateway_is_not_reported_as_a2l_not_deployed(monkeypatch, capsys, module):
    import importlib

    mod = importlib.import_module(f"r4._cli.{module}")
    monkeypatch.setattr(mod, "_a2l_url", lambda client, path: f"http://gw.test/gw/a2l/{path}")

    class _Http:
        def get(self, url, **kwargs):
            raise R4ConnectionError("Connection refused")

    if module == "agent_exec":
        from r4.errors import R4Error

        check = lambda: mod._check_a2l_available(  # noqa: E731
            None, _Http(), APIError, R4ConnectionError, R4Error
        )
    else:
        check = lambda: mod._check_a2l_available(None, _Http())  # noqa: E731
    with pytest.raises(SystemExit):
        check()
    err = capsys.readouterr().err

    assert "not available on this deployment" not in err
    assert "cannot reach http://gw.test/gw/a2l/health" in err


# ---- public-source `model list` and `config context --json` ----


def _public_config():
    from r4.config import Config

    return Config(endpoint_url="http://provider.test/v1", endpoint_api_key="k")


@pytest.mark.parametrize(
    "exc",
    [AuthenticationError(status_code=401), APIError("API error: 500: boom", status_code=500)],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_public_model_list_failure_is_an_error_not_an_empty_list(monkeypatch, capsys, exc, as_json):
    from argparse import Namespace

    from r4._cli import model as model_cli

    def _fail(self, **kwargs):
        raise exc

    monkeypatch.setattr("r4.client.Client.list_public_models", _fail)

    rc = model_cli._cmd_model_list_public(Namespace(as_json=as_json), _public_config())
    out, err = capsys.readouterr()

    assert rc == 1
    assert "No models found" not in out
    assert out.strip() not in ("[]",)
    assert "cannot list models from http://provider.test/v1" in err


def test_public_model_list_401_names_the_source_key_remedy(monkeypatch, capsys):
    from argparse import Namespace

    from r4._cli import model as model_cli

    def _fail(self, **kwargs):
        raise AuthenticationError(status_code=401)

    monkeypatch.setattr("r4.client.Client.list_public_models", _fail)
    monkeypatch.setattr(
        "r4.source.resolve_active_source", lambda name=None: SimpleNamespace(name="pub")
    )

    model_cli._cmd_model_list_public(Namespace(as_json=False), _public_config())
    err = capsys.readouterr().err

    assert "r4 source set pub --api-key-stdin" in err
    assert "umrs-server" not in err


def test_config_context_json_is_json(monkeypatch, capsys):
    import json

    code = _run_cli(monkeypatch, ["config", "context", "--json"])
    out = capsys.readouterr().out

    assert code == 0
    doc = json.loads(out)
    assert set(doc) >= {"profile", "project_id", "umrs_username"}


# ---- `r4 auth status` tells accepted / rejected / could-not-verify apart ----


def _auth_status_with_reply(monkeypatch, capsys, reply):
    from argparse import Namespace

    from r4 import cli

    class _Client:
        _api_prefix = "/gw"

        def __init__(self, config=None):
            pass

        def get(self, *args, **kwargs):
            if isinstance(reply, Exception):
                raise reply
            return reply

        def readiness(self):
            return {"ready": True}

    class _Auth:
        def is_authenticated(self):
            return True

        class token_store:  # noqa: N801 - shape of AuthManager
            @staticmethod
            def load(profile):
                return None

        class api_key_manager:  # noqa: N801
            @staticmethod
            def get_api_key(profile):
                return "opaque-key"

    cfg = SimpleNamespace(
        gateway_url="http://gw.test:8080", project_id=None, bound_source=None, api_key="opaque-key"
    )
    monkeypatch.setattr("r4.client.Client", _Client)
    monkeypatch.setattr("r4.auth.get_auth_manager", lambda profile: _Auth())
    monkeypatch.setattr("r4.config.reload_config", lambda profile=None: cfg)
    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_auth_command(Namespace(auth_command="status", profile="default"))
    return exc_info.value.code, capsys.readouterr().out


@pytest.mark.parametrize(
    "reply",
    [
        APIError("API error: 500: boom", status_code=500),
        {"_raw": "<html><body>Login portal</body></html>"},
    ],
)
def test_auth_status_never_reports_acceptance_it_did_not_see(monkeypatch, capsys, reply):
    code, out = _auth_status_with_reply(monkeypatch, capsys, reply)

    assert code == 1
    assert "accepts them" not in out
    assert "could not verify" in out


def test_auth_status_on_a_down_gateway_is_not_a_rejection(monkeypatch, capsys):
    code, out = _auth_status_with_reply(
        monkeypatch, capsys, R4ConnectionError("Connection refused")
    )

    assert code == 1
    assert "rejected" not in out
    assert "Regenerate" not in out
    assert "could not verify" in out


def test_auth_status_still_reports_a_rejection_and_an_acceptance(monkeypatch, capsys):
    code, out = _auth_status_with_reply(monkeypatch, capsys, AuthenticationError(status_code=401))
    assert code == 1 and "Gateway rejected them" in out

    code, out = _auth_status_with_reply(monkeypatch, capsys, {"data": []})
    assert code == 0 and "Gateway accepts them" in out


def test_auth_status_counts_the_active_sources_own_key(monkeypatch, capsys):
    """Its auth manager has nothing, but config.api_key (the source's key) is sent."""
    from argparse import Namespace

    from r4 import cli

    class _Client:
        _api_prefix = "/gw"

        def __init__(self, config=None):
            pass

        def get(self, *args, **kwargs):
            return {"data": []}

        def readiness(self):
            return {"ready": True}

    class _NoAuth:
        def is_authenticated(self):
            return False

    cfg = SimpleNamespace(
        gateway_url="http://gw.test:8080",
        project_id=None,
        bound_source=None,
        api_key="source-own-key",
    )
    monkeypatch.setattr("r4.client.Client", _Client)
    monkeypatch.setattr("r4.auth.get_auth_manager", lambda profile: _NoAuth())
    monkeypatch.setattr("r4.config.reload_config", lambda profile=None: cfg)
    monkeypatch.setattr("r4.cli._show_jwt_identity", lambda *a: None)
    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_auth_command(Namespace(auth_command="status", profile="default"))
    out = capsys.readouterr().out

    assert exc_info.value.code == 0, out
    assert "no credentials" not in out
    assert "Gateway accepts them" in out


# ---- `r4 doctor` / `health --full` probes are bounded ----


@pytest.mark.parametrize("probe", ["check_mcp", "check_rag", "check_embedding", "check_pgvector"])
def test_diagnostic_probes_pass_a_short_timeout(monkeypatch, probe):
    from r4._cli import diagnostics

    seen: list = []

    class _Client:
        _api_prefix = "/gw"

        def __init__(self, config=None):
            pass

        def get(self, path, **kwargs):
            seen.append(kwargs.get("timeout"))
            return {}

    monkeypatch.setattr("r4.client.Client", _Client)

    getattr(diagnostics, probe)(SimpleNamespace(project_id="p1"))

    assert seen and seen[0] is not None and seen[0] <= 15, seen


def test_auth_status_behind_an_html_portal_is_unverified(monkeypatch):
    """Stab cycle 3 (python-runtime#2): through the real HTTP layer, not a mocked reply.

    Since H1 a 200 text/html page raises APIError(status_code=200); the probe
    counted that as "Gateway accepts them" (exit 0) behind a sign-in portal.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from r4 import cli
    from r4.config import Config

    class Portal(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = b"<!doctype html><html><body>Sign in</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("R4_USE_UMRS_TRANSPORT", "0")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        accepted, reason = cli._validate_auth_status(Config(gateway_url=url, api_key="r4k_x"))
    finally:
        server.shutdown()
    assert accepted is None
    assert "HTML page" in (reason or "")
