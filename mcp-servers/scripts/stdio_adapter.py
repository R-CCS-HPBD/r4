#!/usr/bin/env python3
"""Stdio MCP client adapter for Hatch runtime bridge packages.

Speaks MCP (JSON-RPC 2.0, newline-delimited) to a vendor stdio server
subprocess and presents the Hatch package contract (``list_tools()`` /
``invoke(tool, args)``) consumed by ``r4/src/r4/integrations/hatch_runtime.py``.

Design rules (mirrors hpc-agent-core PORTING.md):
- The adapter never fails at import or construction time: the child process
  is spawned lazily on first use and respawned if it dies. Process
  discipline is deliberately conservative: one long-lived child per adapter
  instance; children are transient — a dead child is terminated and a fresh
  one is respawned rather than nursed back to health.
- Stdlib only — the frontend host Python carries no MCP dependency; the
  vendor servers run in their own venv whose interpreter is the spawn target.
- Lifecycle events go to structured logging (``stdio_adapter`` logger);
  every tool invocation is audit-logged as a JSON line (tool name, args
  hash, duration, success) under the default-MCP state dir. The child's
  stderr is retained in a per-package log file alongside the audit JSONL —
  both are append-only operator evidence.
- Fail-closed on unknown/denied tools: denied tools are refused before any
  child round-trip; anything else is passed through and JSON-RPC errors from
  the child surface as :class:`StdioMcpError`, which the package layer
  converts into a clean client-facing error.

Trust boundary note: the R4 ⇄ adapter traffic is confined to loopback
(127.0.0.1) and invoke arguments pass through **unvalidated** to the vendor
server today (server-side validation stays with the vendor, e.g.
fugaku_policy.py; UMRS-side policy enforcement is tracked in
`docs/development/default-mcp-expansion-plan.md` Phase 2). Privileged tools
are identified declaratively via ``capabilities.approval.dangerous_tools``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("stdio_adapter")

MCP_PROTOCOL_VERSION = "2024-11-05"

STATE_DIR = Path(
    os.environ.get("R4_DEFAULT_MCP_STATE_DIR", Path.home() / ".local/state/r4-default-mcp")
)


class StdioMcpError(RuntimeError):
    """Raised when the stdio child cannot serve a request."""


class StdioMcpAdapter:
    """JSON-RPC stdio client for one MCP server subprocess.

    Parameters:
        name: package name (used in logs/audit).
        command: spawn argv for the vendor stdio server.
        env: extra environment merged over ``os.environ`` for the child.
        cwd: working directory for the child.
        call_timeout: seconds to wait for a single request/response.
            # TODO(hardening): make this configurable per package (e.g. via
            # hatch.json) — doc lookups tolerate longer timeouts than
            # job-submission calls on production clusters.
        tool_deny: tool names hidden from ``list_tools()`` and refused in
            ``invoke()``.
    """

    def __init__(
        self,
        name: str,
        command: Iterable[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        call_timeout: float = 60.0,
        tool_deny: Iterable[str] | None = None,
    ) -> None:
        self.name = name
        self.command = list(command)
        self.env = dict(env or {})
        self.cwd = cwd
        self.call_timeout = call_timeout
        self.tool_deny = set(tool_deny or ())

        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._tools_cache: list[dict[str, Any]] | None = None
        self._last_error: str = ""

    # ------------------------------------------------------------------
    # Child lifecycle (structured events via the stdio_adapter logger)
    # ------------------------------------------------------------------
    def _spawn(self) -> None:
        self._log_path().parent.mkdir(parents=True, exist_ok=True)
        child_env = os.environ.copy()
        child_env.update(self.env)
        stderr_log = open(self._log_path(), "ab")
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_log,
            text=True,
            bufsize=1,
            cwd=self.cwd,
            env=child_env,
            start_new_session=True,
        )
        self._pending = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "hatch-stdio-adapter", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", {})
        self._tools_cache = None
        logger.info("spawned %s pid=%s cmd=%s", self.name, self._proc.pid, self.command)

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        logger.info("terminate %s pid=%s", self.name, proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _ensure(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
        logger.info("respawn %s (child dead or absent)", self.name)
        self._kill()
        try:
            self._spawn()
        except Exception as exc:
            self._last_error = str(exc)
            raise StdioMcpError(f"{self.name}: spawn failed: {exc}") from exc

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid in self._pending:
                self._pending.pop(rid).put(msg)

    def _send(self, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                raise StdioMcpError(f"{self.name}: child not running")
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            waiter: queue.Queue[dict[str, Any]] = queue.Queue()
            self._pending[rid] = waiter
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            msg = waiter.get(timeout=self.call_timeout)
        except queue.Empty as exc:
            self._pending.pop(rid, None)
            self._kill()
            raise StdioMcpError(f"{self.name}: timeout ({self.call_timeout}s) on {method}") from exc
        if "error" in msg:
            raise StdioMcpError(f"{self.name}: {method} error: {msg['error']}")
        return msg.get("result", {})

    def _log_call(self, tool: str, duration_s: float, ok: bool) -> None:
        if ok:
            logger.info("invoke %s.%s ok %.1fms", self.name, tool, duration_s * 1000)
        else:
            logger.warning("invoke %s.%s failed %.1fms", self.name, tool, duration_s * 1000)

    # ------------------------------------------------------------------
    # Hatch contract
    # ------------------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        """Return the child's tools minus denied entries."""
        self._ensure()
        if self._tools_cache is None:
            result = self._request("tools/list", {})
            self._tools_cache = [t for t in result.get("tools", []) if isinstance(t, dict)]
        return [t for t in self._tools_cache if t.get("name") not in self.tool_deny]

    def invoke(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the child and return the MCP result content."""
        if tool_name in self.tool_deny:
            raise StdioMcpError(f"{self.name}: tool denied by policy: {tool_name}")
        started = time.monotonic()
        ok = False
        try:
            self._ensure()
            result = self._request("tools/call", {"name": tool_name, "arguments": dict(args)})
            ok = not result.get("isError", False)
            return result
        finally:
            duration = time.monotonic() - started
            self._log_call(tool_name, duration, ok)
            self._audit(tool_name, args, duration, ok)

    @property
    def last_error(self) -> str:
        return self._last_error

    # ------------------------------------------------------------------
    # Logging / audit
    # ------------------------------------------------------------------
    def _log_path(self) -> Path:
        return STATE_DIR / f"{self.name}.stderr.log"

    def _audit_path(self) -> Path:
        return STATE_DIR / f"{self.name}.audit.jsonl"

    def _audit(self, tool: str, args: dict[str, Any], duration_s: float, ok: bool) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "server": self.name,
            "tool": tool,
            "args_sha256": hashlib.sha256(
                json.dumps(args, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "duration_ms": round(duration_s * 1000, 1),
            "ok": ok,
        }
        try:
            self._audit_path().parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass
