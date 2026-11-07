"""Sandbox resource enforcer for custom MCP entry points (COR-004).

Uses ``resource`` module (POSIX rlimits) to enforce memory and CPU
time limits on child processes.  Falls back gracefully on platforms
that lack rlimit support (Windows).
"""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class SandboxLimits:
    """Resource limits to enforce on a child process."""

    max_memory_mb: int = 512
    max_runtime_sec: int = 30
    max_open_files: int = 256

    def validate(self) -> None:
        if self.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be positive")
        if self.max_runtime_sec <= 0:
            raise ValueError("max_runtime_sec must be positive")
        if self.max_open_files <= 0:
            raise ValueError("max_open_files must be positive")


def build_preexec_fn(limits: SandboxLimits) -> Callable[[], None] | None:
    """Build a preexec_fn for subprocess.Popen that sets rlimits.

    Returns None on platforms without resource module (Windows).
    """
    try:
        import resource as _resource
    except ImportError:
        logger.info("resource module unavailable; sandbox rlimits not enforced")
        return None

    def _apply_limits() -> None:
        # Memory limit (address space)
        mem_bytes = limits.max_memory_mb * 1024 * 1024
        try:
            _resource.setrlimit(_resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except (ValueError, OSError) as exc:
            logger.warning("Failed to set RLIMIT_AS: %s", exc)

        # CPU time limit (hard kill after soft+5s)
        try:
            _resource.setrlimit(
                _resource.RLIMIT_CPU,
                (limits.max_runtime_sec, limits.max_runtime_sec + 5),
            )
        except (ValueError, OSError) as exc:
            logger.warning("Failed to set RLIMIT_CPU: %s", exc)

        # Open file descriptors
        try:
            _resource.setrlimit(
                _resource.RLIMIT_NOFILE,
                (limits.max_open_files, limits.max_open_files),
            )
        except (ValueError, OSError) as exc:
            logger.warning("Failed to set RLIMIT_NOFILE: %s", exc)

    return _apply_limits


async def enforce_timeout(
    proc: Any,
    timeout_sec: int,
    *,
    cleanup_fn: Optional[Callable[[], None]] = None,
) -> int:
    """Wait for a process with timeout, kill on expiry.

    Args:
        proc: asyncio.subprocess.Process or similar with .wait()/.kill().
        timeout_sec: Maximum seconds to wait.
        cleanup_fn: Optional callback invoked after killing the process.

    Returns:
        Process exit code (negative if killed).
    """
    import asyncio

    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.warning(
            "Sandbox timeout (%ds) exceeded; killing process pid=%s",
            timeout_sec,
            getattr(proc, "pid", "?"),
        )
        try:
            proc.kill()
        except OSError:
            pass
        if cleanup_fn:
            try:
                cleanup_fn()
            except Exception as exc:
                logger.warning("Cleanup after timeout kill failed: %s", exc)
        return -signal.SIGKILL
