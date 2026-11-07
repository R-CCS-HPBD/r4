"""EnvironmentProbe — invisible context discovery at session start.

Scans the researcher's working directory, HPC environment, available
models, and recent sessions to build an EnvironmentSnapshot that is
injected into the system prompt.

The researcher never invokes this manually — the companion just "knows"
their environment.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ...safe_git import git_argv, git_env

if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)


@dataclass
class EnvironmentSnapshot:
    """Structured view of the researcher's environment."""

    cwd: str = ""
    # Project detection
    has_readme: bool = False
    readme_summary: str = ""
    has_pyproject: bool = False
    project_name: str = ""
    has_makefile: bool = False
    make_targets: list[str] = field(default_factory=list)
    data_dirs: list[str] = field(default_factory=list)
    # HPC detection
    is_hpc: bool = False
    has_slurm: bool = False
    has_singularity: bool = False
    has_module_system: bool = False
    hostname: str = ""
    loaded_modules: list[str] = field(default_factory=list)
    available_partitions: list[str] = field(default_factory=list)
    # Gateway / models
    gateway_reachable: bool = False
    available_models: list[str] = field(default_factory=list)
    # Git
    is_git_repo: bool = False
    git_branch: str = ""
    git_recent_commits: list[str] = field(default_factory=list)


class EnvironmentProbe:
    """Discover the researcher's environment without any user action."""

    def __init__(self, *, client: "Client | None" = None):
        self._client = client

    def run(self, cwd: str | None = None) -> EnvironmentSnapshot:
        """Run all probes and return a snapshot."""
        snap = EnvironmentSnapshot(cwd=cwd or os.getcwd())

        self._probe_project(snap)
        self._probe_hpc(snap)
        self._probe_git(snap)
        self._probe_gateway(snap)

        return snap

    # -- Project ---------------------------------------------------------------

    def _probe_project(self, snap: EnvironmentSnapshot) -> None:
        cwd = Path(snap.cwd)

        readme = cwd / "README.md"
        if readme.is_file():
            snap.has_readme = True
            try:
                text = readme.read_text(errors="replace")[:2000]
                # First heading + first paragraph
                lines = text.split("\n")
                summary_lines = []
                for line in lines[:20]:
                    summary_lines.append(line)
                    if len(summary_lines) > 5 and line.strip() == "":
                        break
                snap.readme_summary = "\n".join(summary_lines).strip()
            except OSError:
                pass

        pyproj = cwd / "pyproject.toml"
        if pyproj.is_file():
            snap.has_pyproject = True
            try:
                text = pyproj.read_text(errors="replace")
                for line in text.split("\n"):
                    if line.strip().startswith("name"):
                        snap.project_name = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except OSError:
                pass

        makefile = cwd / "Makefile"
        if makefile.is_file():
            snap.has_makefile = True
            try:
                text = makefile.read_text(errors="replace")
                targets = []
                for line in text.split("\n"):
                    if line and not line.startswith(("\t", " ", "#")) and ":" in line:
                        target = line.split(":")[0].strip()
                        if target and not target.startswith("."):
                            targets.append(target)
                snap.make_targets = targets[:20]
            except OSError:
                pass

        # Data directories
        for name in ("data", "datasets", "results", "output", "outputs"):
            d = cwd / name
            if d.is_dir():
                snap.data_dirs.append(name)

    # -- HPC -------------------------------------------------------------------

    def _probe_hpc(self, snap: EnvironmentSnapshot) -> None:
        snap.has_slurm = shutil.which("squeue") is not None
        snap.has_singularity = (
            shutil.which("singularity") is not None or shutil.which("apptainer") is not None
        )
        snap.has_module_system = (
            shutil.which("modulecmd") is not None or os.environ.get("MODULESHOME") is not None
        )
        snap.is_hpc = snap.has_slurm or snap.has_singularity or snap.has_module_system

        snap.hostname = os.environ.get("HOSTNAME", "")

        # Loaded modules
        if snap.has_module_system:
            loaded = os.environ.get("LOADEDMODULES", "")
            if loaded:
                snap.loaded_modules = [m for m in loaded.split(":") if m][:20]

        # Slurm partitions
        if snap.has_slurm:
            try:
                result = subprocess.run(
                    ["sinfo", "-h", "-o", "%P"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    snap.available_partitions = [
                        p.rstrip("*") for p in result.stdout.strip().split("\n") if p.strip()
                    ][:20]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    # -- Git -------------------------------------------------------------------

    def _probe_git(self, snap: EnvironmentSnapshot) -> None:
        cwd = snap.cwd
        try:
            result = subprocess.run(
                git_argv("rev-parse", "--is-inside-work-tree"),
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=3,
                stdin=subprocess.DEVNULL,
                env=git_env(),
            )
            if result.returncode != 0:
                return
        except (FileNotFoundError, OSError):
            return

        snap.is_git_repo = True

        try:
            branch = subprocess.run(
                git_argv("branch", "--show-current"),
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=3,
                stdin=subprocess.DEVNULL,
                env=git_env(),
            )
            snap.git_branch = branch.stdout.strip()

            log = subprocess.run(
                git_argv("log", "--no-show-signature", "--oneline", "-5"),
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=3,
                stdin=subprocess.DEVNULL,
                env=git_env(),
            )
            snap.git_recent_commits = [
                line.strip() for line in log.stdout.strip().split("\n") if line.strip()
            ]
        except (FileNotFoundError, OSError):
            pass

    # -- Gateway / Models ------------------------------------------------------

    def _probe_gateway(self, snap: EnvironmentSnapshot) -> None:
        if not self._client:
            return
        try:
            self._client.mcp.list_tools()  # lightweight ping
            snap.gateway_reachable = True
        except Exception:
            snap.gateway_reachable = False

        if snap.gateway_reachable:
            try:
                # Try listing models
                resp = self._client._request("GET", "/v1/models")
                if resp and "data" in resp:
                    snap.available_models = [m.get("id", "") for m in resp["data"] if m.get("id")][
                        :20
                    ]
            except Exception:
                pass

    # -- Prompt ----------------------------------------------------------------

    @staticmethod
    def as_prompt(snap: EnvironmentSnapshot) -> str:
        """Convert snapshot to a compact system prompt fragment."""
        parts: list[str] = []
        parts.append("[Environment]")
        parts.append(f"Working directory: {snap.cwd}")

        if snap.project_name:
            parts.append(f"Project: {snap.project_name}")
        if snap.readme_summary:
            parts.append(f"README excerpt:\n{snap.readme_summary}")
        if snap.has_makefile and snap.make_targets:
            parts.append(f"Make targets: {', '.join(snap.make_targets[:10])}")
        if snap.data_dirs:
            parts.append(f"Data directories: {', '.join(snap.data_dirs)}")

        if snap.is_hpc:
            parts.append("\n[HPC Environment]")
            parts.append(f"Hostname: {snap.hostname}")
            if snap.has_slurm:
                parts.append("Slurm: available")
            if snap.available_partitions:
                parts.append(f"Partitions: {', '.join(snap.available_partitions)}")
            if snap.has_singularity:
                parts.append("Singularity/Apptainer: available")
            if snap.loaded_modules:
                parts.append(f"Loaded modules: {', '.join(snap.loaded_modules[:10])}")

        if snap.is_git_repo:
            parts.append(f"\n[Git] Branch: {snap.git_branch}")
            if snap.git_recent_commits:
                parts.append("Recent commits:")
                for c in snap.git_recent_commits[:3]:
                    parts.append(f"  {c}")

        if snap.available_models:
            parts.append(f"\n[Available Models] {', '.join(snap.available_models[:5])}")

        return "\n".join(parts)
