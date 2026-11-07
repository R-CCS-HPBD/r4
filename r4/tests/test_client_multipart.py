"""Client.post_multipart — the one file-part POST seam (knowledge upload,
files upload). The JSON request helper cannot carry file parts."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from r4.client import Client


def _client(monkeypatch, handler):
    cfg = SimpleNamespace(
        model="m",
        gateway_url="https://gw.example",
        api_key="jwt",
        endpoint_url=None,
        endpoint_provider=None,
        model_routes={},
    )
    client = Client(config=cfg)
    monkeypatch.setattr(
        client.http,
        "_client",
        lambda timeout=None: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client


def test_post_multipart_sends_file_params_and_merged_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.content
        return httpx.Response(202, json={"job_id": "j-1"})

    client = _client(monkeypatch, handler)
    out = client.post_multipart(
        "/gw/knowledge/upload",
        filename="fig.png",
        content=b"PNGDATA",
        content_type="image/png",
        params={"project_id": "p1", "graph_id": "g1"},
        headers={"x-proposal-id": "gp-1"},
    )
    assert out == {"job_id": "j-1"}
    assert seen["url"] == "https://gw.example/gw/knowledge/upload?project_id=p1&graph_id=g1"
    assert seen["headers"]["x-proposal-id"] == "gp-1"
    assert seen["headers"]["content-type"].startswith("multipart/form-data")
    assert b'filename="fig.png"' in seen["body"] and b"PNGDATA" in seen["body"]


def test_upload_file_bytes_rides_post_multipart(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"uri": "s3://b/k"})

    client = _client(monkeypatch, handler)
    out = client.upload_file_bytes("d.md", b"# draft", purpose="publications")
    assert out == {"uri": "s3://b/k"}
    assert seen["url"].endswith("/files/upload")
    assert b'name="purpose"' in seen["body"] and b"publications" in seen["body"]


def test_post_multipart_http_error_raises(monkeypatch):
    from r4.errors import R4Error

    client = _client(monkeypatch, lambda request: httpx.Response(400, json={"detail": "bad"}))
    with pytest.raises(R4Error):
        client.post_multipart("/gw/knowledge/upload", filename="a", content=b"x")
