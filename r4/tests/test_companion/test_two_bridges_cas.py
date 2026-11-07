"""Two REAL bridge processes on one session (cycle 3, T1 and L3).

Both run ``python -m r4.companion.ui.bridge_server`` over JSONL against a
local fake OpenAI-compatible source, with a scratch HOME. Before the fix the
second console's turn was erased by the first console's next save (the
stored history read A1, A2: B1 was gone), and a corrupt row answered
``initialize`` with the bare decoder text.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


class _FakeOpenAI(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list[str] = []
    # A turn whose last user message is a key here waits for its event: the
    # model is asked, and answers only once the test lets it.
    hold: dict[str, threading.Event] = {}

    def log_message(self, *_args: Any) -> None:
        pass

    def _json(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "fake-a", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        users = [m.get("content") for m in body.get("messages") or [] if m.get("role") == "user"]
        # The user's text, without the per-turn <turn_context> block (wave 80).
        last = str(users[-1] if users else "").split("\n\n<turn_context>", 1)[0]
        _FakeOpenAI.seen.append(last)
        if last in _FakeOpenAI.hold:
            _FakeOpenAI.hold[last].wait(30)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        for delta, finish in (({"content": "reply"}, None), ({}, "stop")):
            chunk = {
                "id": "x",
                "object": "chat.completion.chunk",
                "model": "fake-a",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


@pytest.fixture()
def source_home(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _FakeOpenAI.seen = []
    _FakeOpenAI.hold = {}
    home = tmp_path / "home"
    (home / ".config" / "r4").mkdir(parents=True)
    (home / "work").mkdir()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    sources = {
        "sources": [{"name": "fake", "kind": "public", "url": url, "default_model": "fake-a"}],
        "active": "fake",
    }
    (home / ".config" / "r4" / "sources.json").write_text(json.dumps(sources))
    yield home
    server.shutdown()


class _Bridge:
    def __init__(self, home: Path, *args: str) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("R4_", "UMRS_", "GIT_", "XDG_"))
        }
        env.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "PYTHONPATH": str(_SRC),
                "PYTHONUNBUFFERED": "1",
                "NO_COLOR": "1",
            }
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "r4.companion.ui.bridge_server", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=str(home / "work"),
            text=True,
        )
        self._next = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next += 1
        rid = self._next
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(
            json.dumps({"id": rid, "method": method, "params": params or {}}) + "\n"
        )
        self.proc.stdin.flush()
        for line in self.proc.stdout:
            frame = json.loads(line)
            if frame.get("id") == rid and "ok" in frame:
                return frame
        raise AssertionError(f"bridge exited before answering {method}")

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def _stored_users(home: Path, sid: str) -> list[str]:
    db = home / ".config" / "r4" / "companion" / "sessions.db"
    with sqlite3.connect(db) as conn:
        (raw,) = conn.execute("SELECT messages FROM sessions WHERE id = ?", (sid,)).fetchone()
    return [m["content"] for m in json.loads(raw) if m["role"] == "user"]


@pytest.fixture()
def bridges(source_home):
    """Start bridges on the scratch HOME; every one is closed at teardown."""
    started: list[_Bridge] = []

    def _start(*args: str) -> _Bridge:
        started.append(_Bridge(source_home, *args))
        return started[-1]

    yield _start
    for bridge in started:
        bridge.close()


def _error_view(reply: dict[str, Any]) -> dict[str, Any]:
    error = reply.get("error") or {}
    return {
        "ok": reply["ok"],
        "type": error.get("type"),
        "code": (error.get("data") or {}).get("code"),
    }


def _conflict_says(error: dict[str, Any], sid: str) -> dict[str, Any]:
    """What a session_conflict tells the console, in a comparable shape."""
    message = error["message"]
    return {
        "stage": (error.get("data") or {}).get("stage"),
        "changed elsewhere": "changed in another console" in message,
        "ran": "your turn ran" in message,
        "reload hint": f"/sessions resume {sid}" in error["hint"],
    }


def test_two_consoles_never_erase_each_others_turns(source_home, bridges):
    a = bridges()
    sid = a.request("initialize")["result"]["session"]["id"]
    assert a.request("send_message", {"text": "A1"})["ok"]
    b = bridges("--session-id", sid)
    assert b.request("initialize", {"session_id": sid})["ok"]
    assert b.request("send_message", {"text": "B1"})["ok"]
    refused = a.request("send_message", {"text": "A2"})
    assert _error_view(refused) == {
        "ok": False,
        "type": "SessionConflictError",
        "code": "session_conflict",
    }
    assert _conflict_says(refused["error"], sid) == {
        "stage": "before_turn",
        "changed elsewhere": True,
        "ran": False,
        "reload hint": True,
    }
    assert "A2" not in _FakeOpenAI.seen  # refused before the model was asked
    assert _stored_users(source_home, sid) == ["A1", "B1"]


def test_a_refused_console_continues_after_a_reload(source_home, bridges):
    a = bridges()
    sid = a.request("initialize")["result"]["session"]["id"]
    assert a.request("send_message", {"text": "A1"})["ok"]
    b = bridges("--session-id", sid)
    b.request("initialize", {"session_id": sid})
    b.request("send_message", {"text": "B1"})
    assert not a.request("send_message", {"text": "A2"})["ok"]
    assert a.request("load_session", {"session_id": sid})["ok"]
    assert a.request("send_message", {"text": "A2"})["ok"]
    assert _stored_users(source_home, sid) == ["A1", "B1", "A2"]


def _send_in_background(bridge: _Bridge, text: str) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}
    thread = threading.Thread(
        target=lambda: out.update(bridge.request("send_message", {"text": text}))
    )
    thread.start()
    deadline = time.monotonic() + 30
    while text not in _FakeOpenAI.seen and time.monotonic() < deadline:
        time.sleep(0.02)
    assert text in _FakeOpenAI.seen  # the turn is running: the model was asked
    return thread, out


def test_a_conflict_at_the_end_of_a_turn_says_the_turn_ran(source_home, bridges):
    # Cycle 3 review (data-integrity#1, tui-runtime#0, python-runtime#0): the
    # end-of-turn save's conflict read like the up-front refusal, and the TUI
    # asked for the message again — every tool of the turn would run twice.
    a = bridges()
    sid = a.request("initialize")["result"]["session"]["id"]
    assert a.request("send_message", {"text": "A1"})["ok"]
    _FakeOpenAI.hold["A2"] = threading.Event()
    thread, out = _send_in_background(a, "A2")
    b = bridges("--session-id", sid)
    assert b.request("initialize", {"session_id": sid})["ok"]
    assert b.request("send_message", {"text": "B1"})["ok"]
    _FakeOpenAI.hold["A2"].set()
    thread.join(60)
    assert _error_view(out) == {
        "ok": False,
        "type": "SessionConflictError",
        "code": "session_conflict",
    }
    error = out["error"]
    copy_id = error["data"]["copy_id"]
    assert _conflict_says(error, sid) == {
        "stage": "after_turn",
        "changed elsewhere": True,
        "ran": True,
        "reload hint": True,
    }
    names_the_copy = (copy_id in error["message"], f"/sessions resume {copy_id}" in error["hint"])
    assert (names_the_copy, "do not send it again" in error["message"]) == ((True, True), True)
    assert _FakeOpenAI.seen.count("A2") == 1
    assert _stored_users(source_home, copy_id) == ["A1", "A2"]


def _corrupt_session(source_home: Path, bridges: Any) -> tuple[str, Path]:
    a = bridges()
    sid = a.request("initialize")["result"]["session"]["id"]
    assert a.request("send_message", {"text": "hello"})["ok"]
    a.close()
    db = source_home / ".config" / "r4" / "companion" / "sessions.db"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE sessions SET messages = '[{\"role\": ' WHERE id = ?", (sid,))
    return sid, db


def test_a_corrupt_row_answers_initialize_with_a_typed_error(source_home, bridges):
    sid, db = _corrupt_session(source_home, bridges)
    reply = bridges("--session-id", sid).request("initialize", {"session_id": sid})
    assert _error_view(reply) == {
        "ok": False,
        "type": "SessionCorruptError",
        "code": "session_corrupt",
    }
    assert f"session {sid} cannot be opened: its stored messages" in reply["error"]["message"]
    assert "/sessions new" in reply["error"]["hint"]
    with sqlite3.connect(db) as conn:
        (raw,) = conn.execute("SELECT messages FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert raw == '[{"role": '  # left untouched


def test_a_corrupt_row_exports_raw_from_a_fresh_console(source_home, bridges):
    sid, _db = _corrupt_session(source_home, bridges)
    fresh = bridges()
    assert fresh.request("initialize")["ok"]
    exported = fresh.request("export_session", {"session_id": sid})
    assert exported["ok"], exported
    assert '[{"role": ' in exported["result"]["markdown"]
