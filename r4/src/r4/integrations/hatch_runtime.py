"""Local HTTP bridge for Hatch MCP packages."""

from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import os
import signal
import socketserver
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(tool)
    if "inputSchema" not in normalized and "parameters" in normalized:
        normalized["inputSchema"] = normalized["parameters"]
    if "parameters" not in normalized and "inputSchema" in normalized:
        normalized["parameters"] = normalized["inputSchema"]
    return normalized


def _load_module(entry_point: Path) -> ModuleType:
    module_name = f"r4_hatch_runtime_{entry_point.stem}_{entry_point.stat().st_mtime_ns}"
    spec = importlib.util.spec_from_file_location(module_name, entry_point)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {entry_point}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_server(entry_point: Path) -> Any:
    module = _load_module(entry_point)
    create_server = getattr(module, "create_server", None)
    if create_server is None or not callable(create_server):
        raise RuntimeError(f"{entry_point} does not define callable create_server()")

    server = create_server()
    if not hasattr(server, "list_tools") or not hasattr(server, "invoke"):
        raise RuntimeError(
            f"{entry_point} create_server() result must provide list_tools() and invoke()"
        )
    return server


def _wrap_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and "content" in result:
        wrapped = dict(result)
        wrapped.setdefault("isError", False)
        return wrapped

    text = result if isinstance(result, str) else json.dumps(result)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }


class HatchBridgeHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        package_name: str,
        bridge_server: Any,
        token: str | None = None,
    ):
        super().__init__(server_address, request_handler_class)
        self.package_name = package_name
        self.bridge_server = bridge_server
        # Bearer token required on every request when set (see _authorized).
        self.token = token

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class HatchBridgeHandler(BaseHTTPRequestHandler):
    server: HatchBridgeHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        """Require the runtime token, when one was set, on every request.

        The launcher sets R4_HATCH_RUNTIME_TOKEN whenever the runtime listens
        beyond loopback; without this check any host on the network could call
        the package's tools.
        """
        expected = self.server.token
        if not expected:
            return True
        got = self.headers.get("Authorization", "")
        if hmac.compare_digest(got.encode(), f"Bearer {expected}".encode()):
            return True
        self._send_json({"detail": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
        return False

    def parse_request(self) -> bool:
        """Runs before every do_* handler: the token check lives here, once."""
        return super().parse_request() and self._authorized()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "server": f"hatch-{self.server.package_name}",
                    "transport": "http",
                }
            )
            return

        if self.path == "/tools":
            tools = self.server.bridge_server.list_tools()
            normalized = [_normalize_tool(tool) for tool in tools]
            self._send_json({"tools": normalized})
            return

        self._send_json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/tools/call":
            self._send_json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Invalid JSON: {exc}"},
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        request_id = payload.get("id")
        params = payload.get("params", {}) if isinstance(payload, dict) else {}
        tool_name = params.get("name") or payload.get("tool")
        arguments = params.get("arguments") or payload.get("arguments") or {}

        if not tool_name:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "Tool name is required"},
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            result = self.server.bridge_server.invoke(tool_name, arguments)
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _wrap_result(result),
                }
            )
        except Exception as exc:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local HTTP bridge for a Hatch package")
    parser.add_argument(
        "--entry-point", required=True, help="Path to the Hatch package server entry point"
    )
    parser.add_argument("--package-name", required=True, help="Installed Hatch package name")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, required=True, help="Bind port")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    entry_point = Path(args.entry_point).expanduser().resolve()
    bridge_server = _load_server(entry_point)
    httpd = HatchBridgeHTTPServer(
        (args.host, args.port),
        HatchBridgeHandler,
        package_name=args.package_name,
        bridge_server=bridge_server,
        token=os.environ.get("R4_HATCH_RUNTIME_TOKEN") or None,
    )

    def _shutdown(*_: Any) -> None:
        httpd.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
