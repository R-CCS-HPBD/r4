"""Host-side decision-model proxy for the Terminal-Bench lane (2026-10-03).

Task containers hold only a public source (``support.filter_sources``):
platform profiles and their tokens never enter them. To let the in-container
R4 harness ask the platform decision model (ADR-076), this proxy runs on the
bench host and forwards ONE request shape through the host's own platform
session:

- ``POST /v1/completions`` for the decision model only, with
  ``max_tokens == 1`` (a scoring call; nothing longer passes);
- ``GET /v1/models`` listing that model.

Every request must carry the per-run bearer token (``--token``, also handed to
the containers) and come from a private docker address (172.16.0.0/12,
10.0.0.0/8, 192.168.0.0/16 or loopback). Anything else answers 403/404.

    .venv/bin/python benchmarks/harness/terminal/nimble_proxy.py \
        --profile rccs-cloud --port 8331 --token "$TOKEN"
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger("nimble_proxy")

DEFAULT_MODEL = "Bespoke-Nimble-9B"
_PRIVATE = [
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
]


def allowed_peer(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in net for net in _PRIVATE)


def check_payload(payload: Any, model: str) -> str | None:
    """Why a completions body is refused, or None when it is a scoring call."""
    if not isinstance(payload, dict):
        return "body must be a JSON object"
    requested = str(payload.get("model") or "").rsplit("@", 1)[0]
    if requested != model:
        return f"only {model} is served here"
    if payload.get("max_tokens") != 1:
        return "only max_tokens=1 scoring calls are forwarded"
    return None


class _Clients(threading.local):
    client: Any = None


def make_handler(profile: str, model: str, token: str, stats: dict[str, int]):
    clients = _Clients()

    def client():
        if clients.client is None:
            from r4.client import Client
            from r4.config import Config

            clients.client = Client(Config.from_env(profile=profile))
        return clients.client

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: one line per request below
            pass

        def _send(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not allowed_peer(self.client_address[0]):
                return False
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def do_GET(self):  # noqa: N802 - http.server API
            if not self._authorized():
                return self._send(403, {"error": "forbidden"})
            if self.path.rstrip("/") != "/v1/models":
                return self._send(404, {"error": "not found"})
            return self._send(200, {"object": "list", "data": [{"id": model, "object": "model"}]})

        def do_POST(self):  # noqa: N802 - http.server API
            if not self._authorized():
                stats["refused"] += 1
                return self._send(403, {"error": "forbidden"})
            if self.path.rstrip("/") != "/v1/completions":
                return self._send(404, {"error": "not found"})
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"error": "bad json"})
            reason = check_payload(payload, model)
            if reason:
                stats["refused"] += 1
                return self._send(400, {"error": reason})
            payload["model"] = model
            started = time.monotonic()
            try:
                body = client().text_completion(payload, timeout=30)
            except Exception as exc:  # noqa: BLE001 - relayed as a 502
                stats["errors"] += 1
                logger.warning("upstream error: %s", exc)
                return self._send(502, {"error": f"{type(exc).__name__}: {exc}"[:300]})
            stats["forwarded"] += 1
            logger.info("%s %dms", self.client_address[0], (time.monotonic() - started) * 1000)
            return self._send(200, body)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default="rccs-cloud")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - peer + token checked per request
    parser.add_argument("--port", type=int, default=8331)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    stats = {"forwarded": 0, "refused": 0, "errors": 0}
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(args.profile, args.model, args.token, stats)
    )
    logger.info(
        "nimble proxy on %s:%d → %s via profile %s", args.host, args.port, args.model, args.profile
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("stats %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
