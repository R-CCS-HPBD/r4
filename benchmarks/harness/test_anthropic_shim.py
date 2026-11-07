"""Unit tests for benchmarks/harness/anthropic_shim.py.

Mocks only urllib.request.urlopen — no upstream, no network. The shim's
server path is deprecated (2026-10-05, B5: Claude Code lanes use
same_model/anthropic_bridge.py); its source resolver stays live.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from benchmarks.harness import anthropic_shim


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://x",
        code=code,
        msg="err",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"{err}"),
    )


class _Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_post_with_retry_recovers_from_502() -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: int = 1) -> _Resp:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(502)
        return _Resp({"ok": True})

    shim = anthropic_shim
    with (
        patch.object(shim.urllib.request, "urlopen", side_effect=fake_urlopen),
        patch.object(shim.time, "sleep", return_value=None),
    ):
        status, body = shim._post_with_retry("http://x", {"m": 1}, {})
    assert status == 200
    assert body == {"ok": True}
    assert calls["n"] == 3


def test_post_with_retry_4xx_is_fast_fail() -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: int = 1) -> _Resp:
        calls["n"] += 1
        raise _http_error(400)

    shim = anthropic_shim
    with (
        patch.object(shim.urllib.request, "urlopen", side_effect=fake_urlopen),
        patch.object(shim.time, "sleep", return_value=None),
    ):
        status, body = shim._post_with_retry("http://x", {"m": 1}, {})
    assert status == 400
    assert calls["n"] == 1
    assert isinstance(body, str)


def test_post_with_retry_transport_exhausts_and_returns_502() -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: int = 1) -> _Resp:
        calls["n"] += 1
        raise urllib.error.URLError("conn refused")

    shim = anthropic_shim
    with (
        patch.object(shim.urllib.request, "urlopen", side_effect=fake_urlopen),
        patch.object(shim.time, "sleep", return_value=None),
    ):
        status, body = shim._post_with_retry("http://x", {"m": 1}, {})
    assert status == 502
    assert calls["n"] == len((0, 2, 5, 10, 20, 40))


def test_append_system_handles_str_list_none() -> None:
    append = anthropic_shim._append_system
    assert append([{"role": "user"}], None) == [{"role": "user"}]
    assert append([], "sys") == [{"role": "system", "content": "sys"}]
    out = append([], [{"text": "a"}, {"text": "b"}])
    assert out[0]["content"] == "a b"


def test_translate_tool_call_parses_string_arguments() -> None:
    call = {
        "id": "c1",
        "function": {"name": "Write", "arguments": '{"path": "f"}'},
    }
    out = anthropic_shim._translate_tool_call(call)
    assert out["type"] == "tool_use"
    assert out["input"] == {"path": "f"}
    assert out["name"] == "Write"


def _write_sources(tmp_path: Path, sources: list[dict[str, Any]]) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": sources}))
    return path


def test_resolve_source_uses_registry_entry(monkeypatch: Any, tmp_path: Path) -> None:
    _write_sources(
        tmp_path,
        [
            {
                "name": "rikyu",
                "kind": "public",
                "url": "https://api.rikyu.r-ccs.riken.jp/v1/",
                "api_key": "k1",
            }
        ],
    )
    monkeypatch.setattr(anthropic_shim, "_sources_path", lambda: tmp_path / "sources.json")
    url, key = anthropic_shim._resolve_source("rikyu")
    assert url == "https://api.rikyu.r-ccs.riken.jp/v1"
    assert key == "k1"


def test_resolve_source_rejects_non_openai_provider(monkeypatch: Any, tmp_path: Path) -> None:
    _write_sources(
        tmp_path,
        [{"name": "anthswap", "kind": "public", "provider": "anthropic", "url": "https://x"}],
    )
    monkeypatch.setattr(anthropic_shim, "_sources_path", lambda: tmp_path / "sources.json")
    with pytest.raises(SystemExit):
        anthropic_shim._resolve_source("anthswap")


def test_resolve_source_missing_name_fails_closed(monkeypatch: Any, tmp_path: Path) -> None:
    _write_sources(tmp_path, [{"name": "rikyu", "kind": "public", "url": "https://x"}])
    monkeypatch.setattr(anthropic_shim, "_sources_path", lambda: tmp_path / "sources.json")
    with pytest.raises(SystemExit):
        anthropic_shim._resolve_source("not-a-source")


def test_resolve_config_env_overrides_registry(monkeypatch: Any, tmp_path: Path) -> None:
    _write_sources(
        tmp_path,
        [{"name": "rikyu", "kind": "public", "url": "https://u1", "api_key": "k1"}],
    )
    monkeypatch.setattr(anthropic_shim, "_sources_path", lambda: tmp_path / "sources.json")
    monkeypatch.setenv("R4_BENCH_SHIM_UPSTREAM", "https://u2/")
    monkeypatch.setenv("R4_BENCH_SHIM_API_KEY", "k2")
    url, key, name = anthropic_shim._resolve_config()
    assert url == "https://u2"
    assert key == "k2"
    assert name == "rikyu"


def test_main_refuses_and_points_at_the_bridge() -> None:
    with pytest.raises(SystemExit) as exc:
        anthropic_shim.main(["--port", "8330"])
    message = str(exc.value.code)
    assert "deprecated" in message
    assert "same_model/anthropic_bridge.py" in message


def test_messages_route_answers_410_with_the_pointer(capsys: Any) -> None:
    body = json.dumps({"model": "x", "stream": True, "messages": []}).encode()
    handler = anthropic_shim.ShimHandler.__new__(anthropic_shim.ShimHandler)
    handler.path, handler.command = "/v1/messages", "POST"
    handler.headers = {"content-length": str(len(body))}  # type: ignore[assignment]
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    sent: dict[str, Any] = {"headers": []}
    handler.send_response = lambda *args: sent.setdefault("status", args[0])  # type: ignore[method-assign]
    handler.send_header = lambda *args: sent["headers"].append(args)  # type: ignore[method-assign]
    handler.end_headers = lambda: None  # type: ignore[method-assign]
    with patch.object(anthropic_shim, "_upstream_messages") as upstream:
        handler.do_POST()
    upstream.assert_not_called()  # nothing is translated or sent upstream any more
    assert sent["status"] == 410
    error = json.loads(handler.wfile.getvalue())["error"]
    assert "anthropic_bridge.py" in error["message"]
    capsys.readouterr()
