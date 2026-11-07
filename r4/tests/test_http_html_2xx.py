"""A 2xx HTML page is an error, not data (cli lane H1/C09, 2026-10-21).

A proxy or sign-in portal in front of the gateway answers every path with
``200 text/html``. The HTTP layer wrapped it as ``{"_raw": "<html..."}``:
about 20 commands then printed "No ... found" with exit 0 and ``--json``
printed the wrapper. A provider's ``/v1/models`` answering plain text read
as an empty model list. Plain-text 2xx replies elsewhere stay data.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from r4._http import HTTPCore
from r4.client import _json_body, _model_ids
from r4.errors import APIError

_PAGE = "<!DOCTYPE html><html><body>Please sign in</body></html>"


def _core(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> HTTPCore:
    http = HTTPCore(SimpleNamespace(api_key="t", gateway_url="https://gw.example"))

    @contextmanager
    def _client(_timeout=None):
        with httpx.Client(transport=httpx.MockTransport(lambda _r: response)) as client:
            yield client

    monkeypatch.setattr(http, "_client", _client)
    return http


def test_html_page_with_html_content_type_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    page = httpx.Response(200, text=_PAGE, headers={"Content-Type": "text/html"})
    with pytest.raises(APIError, match="HTML page"):
        _core(monkeypatch, page).get("https://gw.example/gw/projects")


def test_html_page_without_content_type_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    page = httpx.Response(200, content=_PAGE.encode())
    with pytest.raises(APIError, match="HTML page"):
        _core(monkeypatch, page).get("https://gw.example/gw/projects")


def test_html_page_on_the_cancellable_path_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    page = httpx.Response(200, text=_PAGE, headers={"Content-Type": "text/html"})
    with pytest.raises(APIError, match="HTML page"):
        _core(monkeypatch, page).get("https://gw.example/gw/projects", cancel_check=lambda: None)


def test_plain_text_2xx_stays_data(monkeypatch: pytest.MonkeyPatch) -> None:
    text = httpx.Response(200, text="not json but fine")
    assert _core(monkeypatch, text).get("https://gw.example/gw/plain") == {
        "_raw": "not json but fine"
    }


def test_streamed_chat_answered_with_html_raises() -> None:
    page = httpx.Response(200, text=_PAGE, headers={"Content-Type": "text/html"})
    with pytest.raises(APIError, match="HTML page"):
        _json_body(page)


def test_model_list_that_is_not_json_is_an_error() -> None:
    with pytest.raises(APIError, match="did not answer with a model list"):
        _model_ids("https://p.example/v1/models", {"_raw": "garbage-not-a-token"})
    assert _model_ids("u", {"data": [{"id": "m1"}, {"x": 1}]}) == ["m1"]
