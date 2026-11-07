"""r4 LSP integration — config-driven language server management.

Discovers LSP servers from project config (`.r4/config.toml`) and
manages their lifecycle. This enables r4 to provide code intelligence
(diagnostics, completions, hover) for files in the workspace.

LSP servers are external processes; r4 manages start/stop/restart
and provides a unified interface for the agent to query them. An agent can
start them (``start_language_servers``), so they run under the local tool
sandbox when it is on (ADR-101 D-4); the manager records which ones did.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LSPServerConfig:
    """Configuration for a language server."""

    name: str
    language_id: str  # e.g. "python", "typescript", "rust"
    command: List[str] = field(default_factory=list)
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    root_uri: Optional[str] = None
    initialization_options: Dict[str, Any] = field(default_factory=dict)
    auto_start: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LSPServerConfig:
        return cls(
            name=data.get("name", ""),
            language_id=data.get("language_id", ""),
            command=data.get("command", []),
            args=data.get("args", []),
            env=data.get("env", {}),
            root_uri=data.get("root_uri"),
            initialization_options=data.get("initialization_options", {}),
            auto_start=data.get("auto_start", True),
        )


@dataclass
class LSPServerStatus:
    """Status of a running LSP server."""

    name: str
    language_id: str
    running: bool = False
    pid: Optional[int] = None
    capabilities: List[str] = field(default_factory=list)


# Well-known LSP server presets
_PRESETS: Dict[str, Dict[str, Any]] = {
    "python": {
        "name": "pylsp",
        "language_id": "python",
        "command": ["pylsp"],
    },
    "typescript": {
        "name": "typescript-language-server",
        "language_id": "typescript",
        "command": ["typescript-language-server", "--stdio"],
    },
    "rust": {
        "name": "rust-analyzer",
        "language_id": "rust",
        "command": ["rust-analyzer"],
    },
    "go": {
        "name": "gopls",
        "language_id": "go",
        "command": ["gopls", "serve"],
    },
    "c": {
        "name": "clangd",
        "language_id": "c",
        "command": ["clangd"],
    },
}


def _signal_group(proc: subprocess.Popen[bytes], name: str) -> None:
    """Send signal ``name`` to the server's process group (``proc`` alone without killpg)."""
    from r4.procgroup import signal_group

    signal_group(proc, getattr(signal, name, signal.SIGTERM))


def _stop_group(proc: subprocess.Popen[bytes]) -> None:
    """Stop a running server and everything in its group.

    Under the local tool sandbox ``proc`` is the outer bwrap, which leaves the
    server (and the inner bwrap) running when it alone is signalled (ADR-101).
    SIGTERM to the group, SIGKILL when it does not end in 5 s, and SIGKILL once
    more for members that outlived the leader (their group id is still ours:
    the pid is not reused while it names a live group).
    """
    _signal_group(proc, "SIGTERM")
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_group(proc, "SIGKILL")
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    _signal_group(proc, "SIGKILL")


class LSPManager:
    """Manages LSP server processes."""

    def __init__(self) -> None:
        self._configs: Dict[str, LSPServerConfig] = {}
        self._processes: Dict[str, subprocess.Popen[bytes]] = {}
        self._sandboxed: Dict[str, bool] = {}

    def add_config(self, config: LSPServerConfig) -> None:
        """Register an LSP server configuration."""
        self._configs[config.name] = config

    def load_presets(self, languages: Optional[List[str]] = None) -> int:
        """Load preset configs for well-known languages.

        Returns number of presets loaded.
        """
        targets = languages or list(_PRESETS.keys())
        loaded = 0
        for lang in targets:
            preset = _PRESETS.get(lang)
            if not preset:
                continue
            cmd = preset["command"][0]
            if shutil.which(cmd):
                config = LSPServerConfig.from_dict(preset)
                self.add_config(config)
                loaded += 1
            else:
                logger.debug("LSP preset %s not available (%s not found)", lang, cmd)
        return loaded

    def start(self, name: str) -> bool:
        """Start an LSP server by name.

        Raises:
            SandboxError: the local tool sandbox is on and cannot wrap the
                server; nothing was started (ADR-101 D-3).
        """
        from r4.companion.sandbox import wrap_spawn

        config = self._configs.get(name)
        if not config:
            logger.warning("Unknown LSP server: %s", name)
            return False

        if name in self._processes:
            proc = self._processes[name]
            if proc.poll() is None:
                logger.info("LSP server %s already running (pid=%d)", name, proc.pid)
                return True

        cmd = config.command + config.args
        env = {**os.environ, **config.env}

        spec = wrap_spawn(cmd, shell=False, cwd=None, env=env)
        try:
            proc = subprocess.Popen(
                spec.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=spec.env,
                start_new_session=True,  # stop() signals the whole group (the wrapper too)
            )
            self._processes[name] = proc
            self._sandboxed[name] = spec.sandboxed
            logger.info("Started LSP server %s (pid=%d)", name, proc.pid)
            return True
        except FileNotFoundError:
            logger.warning("LSP server command not found: %s", cmd)
            return False
        except OSError as exc:
            logger.warning("Failed to start LSP server %s: %s", name, exc)
            return False

    def stop(self, name: str) -> bool:
        """Stop an LSP server."""
        proc = self._processes.pop(name, None)
        self._sandboxed.pop(name, None)
        if proc is None:
            return False
        if proc.poll() is None:
            _stop_group(proc)
        logger.info("Stopped LSP server %s", name)
        return True

    def stop_all(self) -> None:
        """Stop all running LSP servers."""
        for name in list(self._processes.keys()):
            self.stop(name)

    def unconfined(self) -> List[str]:
        """Names of running servers started with the local tool sandbox off."""
        running = list(self._processes.items())  # a snapshot: start() may run on another thread
        return sorted(
            name
            for name, proc in running
            if proc.poll() is None and not self._sandboxed.get(name, False)
        )

    def status(self) -> List[LSPServerStatus]:
        """Get status of all configured servers."""
        results: List[LSPServerStatus] = []
        for name, config in self._configs.items():
            proc = self._processes.get(name)
            running = proc is not None and proc.poll() is None
            results.append(
                LSPServerStatus(
                    name=name,
                    language_id=config.language_id,
                    running=running,
                    pid=proc.pid if running and proc else None,
                )
            )
        return results

    def get_diagnostics(self, name: str, file_uri: str) -> List[Dict[str, Any]]:
        """Request diagnostics from an LSP server (stub).

        Full LSP protocol communication requires an LSP client library.
        This is a placeholder for the interface.
        """
        proc = self._processes.get(name)
        if not proc or proc.poll() is not None:
            return []
        # Full implementation would use JSON-RPC over stdio
        return []
