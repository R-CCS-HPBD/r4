"""Container runtime abstraction for the SWE-bench lane.

SWE-bench instance images are x86_64 and ship the repo at /testbed with the
task environment pre-installed. Two image naming conventions exist in the
wild; resolution tries both:

    sweb.eval.x86_64.<instance_id>            (SWE-bench official uploads)
    swebench/sweb.eval.x86_64.<instance_id>   (SWE-agent mirror)

The runtime contract is intentionally small so a FakeRuntime can drive all
plumbing tests without a daemon, and so podman works by name swap.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 — lane drives the pinned container CLI
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from benchmarks.harness.swe.dataset import SweInstance
from benchmarks.harness.swe.fairness import strip_future_commits_script

TESTBED = "/testbed"


@dataclass
class ExecResult:
    rc: int
    stdout: str = ""
    stderr: str = ""


class ContainerRuntime:
    """Protocol for starting/execing/stopping one instance container."""

    def prepare(self, instance: SweInstance, mounts: tuple[tuple[str, str], ...] = ()) -> str:
        """Start a container for the instance at base_commit with future
        commits stripped. ``mounts`` are (host_path, container_path) pairs,
        read-only. Returns the container id/name."""
        raise NotImplementedError

    def exec(
        self,
        container: str,
        argv: list[str],
        *,
        workdir: str = TESTBED,
        env: dict[str, str] | None = None,
        timeout_sec: int = 3600,
    ) -> ExecResult:
        raise NotImplementedError

    def read_file(self, container: str, path: str) -> str:
        raise NotImplementedError

    def write_file(self, container: str, path: str, content: str) -> None:
        raise NotImplementedError

    def destroy(self, container: str) -> None:
        raise NotImplementedError


@dataclass
class DockerRuntime(ContainerRuntime):
    """docker-or-podman CLI runtime."""

    engine: str = "docker"  # "docker" | "podman"
    memory: str = "8g"
    pids_limit: int = 300
    pids_memory: dict = field(default_factory=dict)

    def _image_candidates(self, instance: SweInstance) -> list[str]:
        # Official registry convention (swe-bench tests/test_task_publish.py):
        # docker hub disallows dunders in repo names, so the instance id's
        # double underscore is mapped to _1776_.
        iid = instance.instance_id
        mapped = iid.replace("__", "_1776_")
        return [
            f"swebench/sweb.eval.x86_64.{mapped}:latest",
            f"swebench/sweb.eval.x86_64.{iid}:latest",
        ]

    def image_for(self, instance: SweInstance) -> str:
        candidates = self._image_candidates(instance)
        for candidate in candidates:
            inspect = subprocess.run(
                [self.engine, "image", "inspect", candidate],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if inspect.returncode == 0:
                return candidate
        # Neither local — pull each candidate until one succeeds.
        errors = []
        for candidate in candidates:
            pull = subprocess.run(
                [self.engine, "pull", "--platform", "linux/amd64", candidate],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if pull.returncode == 0:
                return candidate
            errors.append(f"{candidate}: {(pull.stderr or pull.stdout)[-200:]}")
        raise RuntimeError(
            f"cannot resolve image for {instance.instance_id}: {'; '.join(errors)}"
        )

    def prepare(self, instance: SweInstance, mounts: tuple[tuple[str, str], ...] = ()) -> str:
        image = self.image_for(instance)
        name = f"r4bench-{instance.instance_id.replace('/', '-').replace(':', '-')}"
        self.destroy(name)  # clean any stale container from a previous run
        # NOTE on networking: left on the default bridge. The claw fairness
        # rule bans the network FOR ANSWERS (web tools are disabled and the
        # prompt says so), but the model API endpoint itself needs egress —
        # every harness here calls a remote inference proxy. Claw benches
        # enforce the same split (provider APIs reachable, web tools denied).
        argv = [
            self.engine,
            "run",
            "-d",
            "--name",
            name,
            "--memory",
            self.memory,
            "--pids-limit",
            str(self.pids_limit),
        ]
        for host_path, container_path in mounts:
            argv += ["-v", f"{host_path}:{container_path}:ro"]
        argv += [
            image,
            "sleep",
            "infinity",
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"container start failed: {proc.stderr[-300:]}")
        strip = self.exec(name, ["sh", "-c", strip_future_commits_script(instance.base_commit)])
        if strip.rc != 0:
            self.destroy(name)
            raise RuntimeError(f"future-commit strip failed: {strip.stderr[-300:]}")
        return name

    def exec(
        self,
        container: str,
        argv: list[str],
        *,
        workdir: str = TESTBED,
        env: dict[str, str] | None = None,
        timeout_sec: int = 3600,
    ) -> ExecResult:
        cmd = [self.engine, "exec", "-w", workdir]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        cmd.append(container)
        cmd += argv
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(rc=124, stderr=f"timeout after {timeout_sec}s")
        return ExecResult(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def read_file(self, container: str, path: str) -> str:
        result = self.exec(container, ["cat", path])
        if result.rc != 0:
            raise RuntimeError(f"read {path}: {result.stderr[-200:]}")
        return result.stdout

    def write_file(self, container: str, path: str, content: str) -> None:
        parent = str(PurePosixPath(path).parent)
        mkdir = self.exec(container, ["mkdir", "-p", parent])
        if mkdir.rc != 0:
            raise RuntimeError(f"mkdir {parent}: {mkdir.stderr[-200:]}")
        with tempfile_path(content) as tmp:
            cmd = [self.engine, "cp", str(tmp), f"{container}:{path}"]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"copy to {path}: {proc.stderr[-200:]}")

    def destroy(self, container: str) -> None:
        subprocess.run(
            [self.engine, "rm", "-f", container],
            capture_output=True,
            text=True,
        )


class _TempPath:
    def __init__(self, content: str) -> None:
        self.content = content
        self.path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        handle.write(self.content)
        handle.close()
        self.path = Path(handle.name)
        return self.path

    def __exit__(self, *exc) -> None:
        if self.path:
            self.path.unlink(missing_ok=True)


def tempfile_path(content: str) -> _TempPath:
    return _TempPath(content)


def engine_available(engine: str) -> str | None:
    """Availability probe for CLI usage; returns None when usable."""
    if shutil.which(engine) is None:
        return f"{engine} CLI not on PATH"
    probe = subprocess.run(
        [engine, "info", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        if engine == "docker":
            try:
                json.loads(probe.stdout)
                return None
            except json.JSONDecodeError:
                pass
        return f"{engine} daemon not reachable (start it, e.g. `docker desktop` / `podman machine start`)"
    return None
