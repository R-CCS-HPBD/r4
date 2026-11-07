"""Fake stdio MCP server for remote-proxy tests (NDJSON over stdin/stdout).

Speaks newline-delimited JSON-RPC: answers initialize, tools/list and
tools/call (echo). Prints its pid marker on startup for the respawn pin.
"""

from __future__ import annotations

import json
import sys

TOOLS = [{"name": "stdio_echo", "description": "echo arguments", "inputSchema": {"type": "object"}}]


def _reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        msg_id = message.get("id")
        if method == "initialize":
            _reply(msg_id, {"protocolVersion": "2025-06-18", "capabilities": {}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params", {})
            content = [{"type": "text", "text": json.dumps(params)}]
            _reply(msg_id, {"content": content, "isError": False})


if __name__ == "__main__":
    main()
