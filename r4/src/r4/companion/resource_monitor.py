"""Resource monitor for companion sessions.

Monitors HPC resource usage for long-running autonomous workflows:
- GPU utilization and memory (via nvidia-smi)
- CPU utilization (via psutil or fallback)
- RAM usage (via psutil or fallback)
- Slurm jobs (via squeue)

This powers the Resource Monitor panel in the TUI.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


class ResourceMonitor:
    """Monitor system resources for observability.

    This class is best-effort: if tools like nvidia-smi or squeue are
    unavailable, it returns empty lists / None instead of raising.
    """

    def get_resources(self) -> dict[str, Any]:
        """Get current resource usage snapshot."""
        return {
            "gpu": self._get_gpu_usage(),
            "cpu": self._get_cpu_usage(),
            "memory": self._get_memory_usage(),
            "slurm_jobs": self._get_slurm_jobs(),
            "hostname": self._get_hostname(),
        }

    def _get_gpu_usage(self) -> list[dict[str, Any]]:
        """Get GPU utilization and memory usage via nvidia-smi."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                return []
            gpus: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    try:
                        gpus.append(
                            {
                                "index": int(parts[0]),
                                "utilization": int(parts[1]),
                                "memory_used": int(parts[2]),
                                "memory_total": int(parts[3]),
                            }
                        )
                    except ValueError:
                        continue
            return gpus
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []

    def _get_cpu_usage(self) -> dict[str, Any] | None:
        """Get CPU utilization percentage and core count."""
        try:
            import psutil  # type: ignore

            return {
                "percent": round(psutil.cpu_percent(interval=0.1), 1),
                "count": psutil.cpu_count(logical=True) or 0,
            }
        except Exception:
            pass

        try:
            load1, _, _ = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            pct = min(100.0, (load1 / cpu_count) * 100.0)
            return {
                "percent": round(pct, 1),
                "count": cpu_count,
            }
        except Exception:
            return None

    def _get_memory_usage(self) -> dict[str, Any] | None:
        """Get RAM usage (GB + percent)."""
        # Prefer psutil if available
        try:
            import psutil  # type: ignore

            vm = psutil.virtual_memory()
            total_gb = round(vm.total / (1024**3), 1)
            used_gb = round(vm.used / (1024**3), 1)
            # Compute percent from used/total so display is consistent.
            # (psutil's vm.percent includes cached file memory on macOS,
            #  which makes the percentage look higher than used/total.)
            pct = round((vm.used / vm.total) * 100.0, 1) if vm.total > 0 else 0.0
            return {
                "total": total_gb,
                "used": used_gb,
                "percent": pct,
            }
        except Exception:
            pass

        # Fallback: try Linux /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem_total_kb = None
            mem_available_kb = None
            for line in lines:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_available_kb = int(line.split()[1])
            if mem_total_kb and mem_available_kb is not None:
                used_kb = mem_total_kb - mem_available_kb
                return {
                    "total": round(mem_total_kb / (1024 * 1024), 1),
                    "used": round(used_kb / (1024 * 1024), 1),
                    "percent": round((used_kb / mem_total_kb) * 100.0, 1),
                }
        except Exception:
            pass

        return None

    def _get_slurm_jobs(self) -> list[dict[str, Any]]:
        """Get current user's Slurm jobs via squeue."""
        try:
            user = os.environ.get("USER", "")
            cmd = ["squeue", "--noheader", "--format=%i|%T|%j"]
            if user:
                cmd.extend(["-u", user])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                return []
            jobs: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("|", 2)
                if len(parts) == 3:
                    jobs.append(
                        {
                            "job_id": parts[0].strip(),
                            "status": parts[1].strip(),
                            "name": parts[2].strip(),
                        }
                    )
            return jobs
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []

    @staticmethod
    def _get_hostname() -> str:
        return os.uname().nodename
