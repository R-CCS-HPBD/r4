"""Persistent stdio MCP client suite (scripts/stdio_adapter.py).

Covers six core behaviors: MCP initialize handshake, tool listing with
runtime deny filtering, invoke round-trip + audit record, denied-tool
refusal, restart after child death, and timeout kill. Uses a fake
stdio MCP server — no vendor dependencies or HPC access required.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import stdio_adapter  # noqa: E402
from stdio_adapter import StdioMcpAdapter, StdioMcpError  # noqa: E402

FAKE_SERVER = r"""
import json, sys, time

def reply(msg, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg["id"]}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result or {}
    print(json.dumps(out), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        reply(msg, {"protocolVersion": msg["params"]["protocolVersion"],
                    "capabilities": {}, "serverInfo": {"name": "fake"}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        reply(msg, {"tools": [
            {"name": "echo", "description": "echo back",
             "inputSchema": {"type": "object"}},
            {"name": "denied_tool", "description": "secret",
             "inputSchema": {"type": "object"}},
            {"name": "slow", "description": "sleeps",
             "inputSchema": {"type": "object"}},
        ]})
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name == "echo":
            reply(msg, {"content": [{"type": "text", "text": str(args.get("text", ""))}],
                        "isError": False})
        elif name == "slow":
            time.sleep(5)
            reply(msg, {"content": [{"type": "text", "text": "done"}], "isError": False})
        else:
            reply(msg, {"content": [{"type": "text", "text": "unknown"}],
                        "isError": True})
"""


@pytest.fixture()
def fake_server_path(tmp_path: Path) -> Path:
    path = tmp_path / "fake_mcp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


@pytest.fixture()
def adapter(fake_server_path: Path, tmp_path: Path, monkeypatch) -> StdioMcpAdapter:
    monkeypatch.setattr(stdio_adapter, "STATE_DIR", tmp_path / "state")
    return StdioMcpAdapter(
        name="fake",
        command=[sys.executable, str(fake_server_path)],
        call_timeout=2.0,
        tool_deny=["denied_tool"],
    )


def test_list_tools_filters_denied(adapter: StdioMcpAdapter) -> None:
    names = [t["name"] for t in adapter.list_tools()]
    assert names == ["echo", "slow"]


def test_invoke_returns_content_and_audits(adapter: StdioMcpAdapter) -> None:
    result = adapter.invoke("echo", {"text": "hello"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "hello"

    audit_path = stdio_adapter.STATE_DIR / "fake.audit.jsonl"
    record = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["tool"] == "echo"
    assert record["ok"] is True
    assert len(record["args_sha256"]) == 64


def test_denied_tool_refused(adapter: StdioMcpAdapter) -> None:
    with pytest.raises(StdioMcpError, match="denied"):
        adapter.invoke("denied_tool", {})


def test_restart_after_child_death(adapter: StdioMcpAdapter) -> None:
    adapter.list_tools()
    first_pid = adapter._proc.pid  # noqa: SLF001
    adapter._proc.terminate()  # noqa: SLF001
    adapter._proc.wait()  # noqa: SLF001

    result = adapter.invoke("echo", {"text": "again"})
    assert result["content"][0]["text"] == "again"
    assert adapter._proc.pid != first_pid  # noqa: SLF001


def test_timeout_kills_child(adapter: StdioMcpAdapter) -> None:
    with pytest.raises(StdioMcpError, match="timeout"):
        adapter.invoke("slow", {})
    time.sleep(0.1)
    assert adapter._proc is None or adapter._proc.poll() is not None  # noqa: SLF001


def test_spawn_failure_raises(adapter: StdioMcpAdapter) -> None:
    broken = StdioMcpAdapter(
        name="broken",
        command=["/nonexistent/python", "-m", "nothing"],
        call_timeout=1.0,
    )
    with pytest.raises(StdioMcpError, match="spawn failed"):
        broken.list_tools()
