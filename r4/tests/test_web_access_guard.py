"""ADR-099 D-9: the companion's web tools follow the session's web-access choice.

Before RS-1 ``web_fetch`` was a direct ``urllib`` call with no check at all
(its description claimed "Subject to EgressGuard policy"). These tests run
the companion's own tool executors against a local HTTP server.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from r4.companion import session_store as store_module
from r4.companion.session_store import CompanionSessionStore
from r4.companion.tools import get_builtin_tools
from r4.companion.web_access import (
    REFUSAL_OFF,
    REFUSAL_UNCHOSEN,
    WebAccess,
    WebAccessError,
    check_search,
    check_url,
    host_allowed,
    make_web_access,
    parse_web_access_spec,
    parse_web_flag,
)

# ── a local web server: /page answers, /hop redirects to another host ──────


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
        if self.path.startswith("/hop"):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/page")
            self.end_headers()
            return
        body = b"<html><script>x()</script><p>hello web</p></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def web_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


def _executor(access: WebAccess, name: str = "web_fetch"):
    tools = get_builtin_tools(None, web_access=lambda: access)
    return next(t for t in tools if t["name"] == name)["execute"]


def _allow_local() -> WebAccess:
    return make_web_access("allowlist", ["127.0.0.1"], source="prompt")


# ── the executor ────────────────────────────────────────────────────────────


def test_web_fetch_is_refused_when_web_access_is_off(web_server: int) -> None:
    out = _executor(make_web_access("off", source="prompt"))(
        url=f"http://127.0.0.1:{web_server}/page"
    )
    assert out == f"Error: {REFUSAL_OFF}"


def test_web_fetch_is_refused_before_any_choice(web_server: int) -> None:
    out = _executor(WebAccess(mode=""))(url=f"http://127.0.0.1:{web_server}/page")
    assert out == f"Error: {REFUSAL_UNCHOSEN}"


def test_web_fetch_outside_the_allowlist_is_refused_with_the_host(web_server: int) -> None:
    out = _executor(_allow_local())(url=f"http://localhost:{web_server}/page")
    assert out.startswith("Error: ") and "'localhost' is not on it" in out


def test_web_fetch_inside_the_allowlist_runs(web_server: int) -> None:
    out = _executor(_allow_local())(url=f"http://127.0.0.1:{web_server}/page")
    assert out == "hello web"


def test_a_redirect_off_the_allowlist_is_refused(web_server: int) -> None:
    out = _executor(_allow_local())(url=f"http://127.0.0.1:{web_server}/hop")
    assert out.startswith("Error: redirect to http://localhost:")
    assert "not on it" in out


def test_public_runs_the_agent_handler(web_server: int) -> None:
    out = _executor(make_web_access("public", source="prompt"))(
        url=f"http://localhost:{web_server}/hop"
    )
    assert out == "hello web"


def test_web_search_needs_public(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.agent import tools as agent_tools

    monkeypatch.setattr(
        agent_tools, "_web_search_fn", lambda q, n: [{"title": "T", "snippet": q, "url": "u"}]
    )
    for mode in ("off", "allowlist"):
        domains = [".arxiv.org"] if mode == "allowlist" else []
        out = _executor(make_web_access(mode, domains, source="prompt"), "web_search")(query="q")
        assert out.startswith("Error: ")
    out = _executor(make_web_access("public", source="prompt"), "web_search")(query="q")
    assert out.startswith("1. T")


def test_the_choice_is_read_at_each_call(web_server: int) -> None:
    """A /web change reaches the next call (the getter, not a snapshot)."""
    current = {"access": make_web_access("off", source="prompt")}
    tools = get_builtin_tools(None, web_access=lambda: current["access"])
    fetch = next(t for t in tools if t["name"] == "web_fetch")["execute"]
    url = f"http://127.0.0.1:{web_server}/page"
    assert fetch(url=url).startswith("Error: ")
    current["access"] = _allow_local()
    assert fetch(url=url) == "hello web"


def test_the_companion_session_wires_its_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, web_server: int
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)
    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
    store_module.set_snapshot_enabled(None)
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    client = SimpleNamespace(
        config=SimpleNamespace(profile="default", gateway_url="", project_id="p", model="m")
    )
    session = store.create_session(client, name="web")
    fetch = next(t for t in session.tools if t["name"] == "web_fetch")["execute"]
    url = f"http://127.0.0.1:{web_server}/page"
    assert fetch(url=url) == f"Error: {REFUSAL_UNCHOSEN}"
    session.metadata["web_access"] = _allow_local().to_metadata()
    assert fetch(url=url) == "hello web"


# ── the pieces ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "domains", "allowed"),
    [
        ("arxiv.org", ["arxiv.org"], True),
        ("export.arxiv.org", ["arxiv.org"], False),
        ("export.arxiv.org", [".arxiv.org"], True),
        ("arxiv.org", [".arxiv.org"], True),
        ("notarxiv.org", [".arxiv.org"], False),
        ("ARXIV.org.", ["arxiv.org"], True),
        ("", ["arxiv.org"], False),
    ],
)
def test_host_allowed_follows_squid_dstdomain(host: str, domains: list[str], allowed: bool) -> None:
    assert host_allowed(host, domains) is allowed


def test_check_url_and_search() -> None:
    allow = make_web_access("allowlist", ".arxiv.org, pypi.org", source="flag")
    assert check_url(allow, "https://export.arxiv.org/abs/1") is None
    assert "not on it" in str(check_url(allow, "https://evil.example/x"))
    assert check_url(make_web_access("public", source="flag"), "https://a.b") is None
    assert check_search(allow) is not None


def test_choices_validate() -> None:
    assert parse_web_flag("off") == "none"
    with pytest.raises(WebAccessError):
        parse_web_flag("open")
    with pytest.raises(WebAccessError, match="at least one domain"):
        make_web_access("allowlist", [], source="flag")
    with pytest.raises(WebAccessError, match="not a domain"):
        make_web_access("allowlist", ["https://a.org/x"], source="flag")
    spec = parse_web_access_spec("allowlist:.arxiv.org,pypi.org", source="env")
    assert spec.domains == (".arxiv.org", "pypi.org") and spec.source == "env"
    assert make_web_access("public", ["a.org"], source="flag").domains == ()


def test_a_session_without_a_choice_follows_the_env_setting() -> None:
    """ACP editors and the REPL do not ask: R4_WEB_ACCESS applies (D-2)."""
    from r4.companion.web_access import session_web_access

    own = {"web_access": make_web_access("off", source="prompt").to_metadata()}
    assert session_web_access({}, {"R4_WEB_ACCESS": "public"}).mode == "public"
    assert session_web_access(own, {"R4_WEB_ACCESS": "public"}).mode == "none"
    assert not session_web_access({}, {"R4_WEB_ACCESS": "allowlist"}).chosen  # invalid
    assert not session_web_access({}, {}).chosen


def test_metadata_round_trip_and_unreadable_values() -> None:
    access = make_web_access("allowlist", ["a.org"], source="prompt")
    assert WebAccess.from_metadata({"web_access": access.to_metadata()}) == access
    assert not WebAccess.from_metadata({"web_access": {"mode": "open"}}).chosen
    assert not WebAccess.from_metadata({}).chosen
