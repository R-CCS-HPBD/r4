"""A redirect that lands on the gateway's browser login page is an auth
failure, never an API result (production enablement 2026-09-25).

Live defect: ``/gw/scientific-catalogs`` 301'd to an absolute
``http://host:50080/...`` Location, httpx followed it without the
Authorization header, and the gateway's 200 login form came back wrapped as
``{"_raw": "<html>..."}`` — ``r4 catalog list`` printed "No scientific
catalogs." and the release acceptance step passed.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from r4._http import HTTPCore
from r4.errors import AuthenticationError

_LOGIN_HTML = "<html><title>R4 Console Login</title><form></form></html>"


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/gw/scientific-catalogs":
        return httpx.Response(
            301, headers={"Location": "http://gw.example:50080/gw/scientific-catalogs/"}
        )
    if path == "/gw/scientific-catalogs/":
        return httpx.Response(302, headers={"Location": "/auth/web-login?redirect_to=/x"})
    if path == "/auth/web-login":
        return httpx.Response(200, text=_LOGIN_HTML, headers={"Content-Type": "text/html"})
    if path == "/gw/plain":
        return httpx.Response(200, text="not json but fine")
    if path == "/gw/files/download/notes/auth/web-login":
        return httpx.Response(200, content=b"user data")
    if path == "/gw/knowledge/upload":
        return httpx.Response(302, headers={"Location": "/auth/web-login?redirect_to=/x"})
    return httpx.Response(200, json={"items": []})


@pytest.fixture
def core(monkeypatch):
    http = HTTPCore(SimpleNamespace(api_key="t", gateway_url="https://gw.example"))

    @contextmanager
    def _client(_timeout=None):
        with httpx.Client(transport=httpx.MockTransport(_handler), follow_redirects=True) as c:
            yield c

    monkeypatch.setattr(http, "_client", _client)
    return http


def test_login_page_after_redirect_raises_auth_error(core):
    with pytest.raises(AuthenticationError, match="browser login page"):
        core.get("https://gw.example/gw/scientific-catalogs")


def test_json_reply_unchanged(core):
    assert core.get("https://gw.example/gw/other") == {"items": []}


def test_non_json_2xx_elsewhere_still_wrapped(core):
    assert core.get("https://gw.example/gw/plain") == {"_raw": "not json but fine"}


def test_default_accept_asks_for_json(core):
    # nginx's @error401 answers JSON 401 (not a 302 to the login page) only
    # when the client accepts JSON.
    assert core.build_headers()["Accept"] == "application/json"
    assert core.build_headers({"Accept": "text/event-stream"})["Accept"] == "text/event-stream"


def test_stream_login_landing_raises(core):
    with pytest.raises(AuthenticationError, match="browser login page"):
        with core.stream("GET", "https://gw.example/gw/scientific-catalogs"):
            pass


def test_files_download_refuses_login_page(monkeypatch, tmp_path):
    from r4.files import FilesClient

    fc = FilesClient(SimpleNamespace(api_key="t", gateway_url="https://gw.example"))

    def _dl_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/gw/files/download/"):
            assert "application/json" in request.headers["accept"]
            return httpx.Response(302, headers={"Location": "/auth/web-login?redirect_to=/x"})
        return _handler(request)

    @contextmanager
    def _client(_timeout=None):
        with httpx.Client(transport=httpx.MockTransport(_dl_handler), follow_redirects=True) as c:
            yield c

    monkeypatch.setattr(fc._http, "_client", _client)
    out = tmp_path / "out.h5"
    with pytest.raises(AuthenticationError):
        fc.download_file("results/out.h5", dest=out)
    assert not out.exists()  # the HTML page is never saved as the file


def test_a_path_that_merely_contains_the_login_text_is_data(core):
    """Only a redirect that ENDS on the login endpoint counts (review 2026-09-25)."""
    assert core.get("https://gw.example/gw/files/download/notes/auth/web-login") == {
        "_raw": "user data"
    }


def test_post_multipart_refuses_login_page(monkeypatch):
    from r4.client import Client

    client = Client(
        config=SimpleNamespace(
            model="m",
            gateway_url="https://gw.example",
            api_key="t",
            endpoint_url=None,
            endpoint_provider=None,
            model_routes={},
        )
    )

    @contextmanager
    def _client(_timeout=None):
        with httpx.Client(transport=httpx.MockTransport(_handler), follow_redirects=True) as c:
            yield c

    monkeypatch.setattr(client.http, "_client", _client)
    with pytest.raises(AuthenticationError):
        client.post_multipart("/gw/knowledge/upload", filename="a.txt", content=b"x")
