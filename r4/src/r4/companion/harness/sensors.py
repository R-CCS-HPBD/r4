"""QualitySensor — pre-execution and post-execution validation.

Computational sensors (deterministic checks) run on every tool call.
They catch errors *before* expensive compute is wasted — Fowler's
"keep quality left" principle.

The researcher experiences this as a colleague who double-checks work.
"""

from __future__ import annotations

import ast
import logging
import os
import re

logger = logging.getLogger(__name__)


class QualitySensor:
    """Pre- and post-execution validation for tool calls."""

    def __init__(self):
        # Track consecutive errors per tool for escalation
        self._error_counts: dict[str, int] = {}

    def pre_execution(self, tool_name: str, arguments: dict) -> str | None:
        """Run before a tool executes. Returns a warning/block message, or None."""
        checker = _PRE_CHECKS.get(tool_name)
        if checker:
            return checker(arguments)
        return None

    def post_execution(self, tool_name: str, arguments: dict, result: str) -> str | None:
        """Run after a tool executes. Returns a diagnostic or None."""
        # Track failures
        if _looks_like_error(result):
            key = tool_name
            self._error_counts[key] = self._error_counts.get(key, 0) + 1
            if self._error_counts[key] >= 3:
                self._error_counts[key] = 0
                return (
                    f"⚠ The same type of error has occurred 3 times for {tool_name}. "
                    "Consider a fundamentally different approach."
                )
        else:
            self._error_counts.pop(tool_name, None)

        checker = _POST_CHECKS.get(tool_name)
        if checker:
            return checker(arguments, result)
        return None

    def reset(self) -> None:
        self._error_counts.clear()


# ---------------------------------------------------------------------------
# Pre-execution checks (computational sensors)
# ---------------------------------------------------------------------------


def _check_shell_pre(args: dict) -> str | None:
    """Validate shell commands before execution."""
    cmd = args.get("command", "")

    # Dangerous commands
    if re.search(r"\brm\s+-rf\s+/", cmd):
        return "🛑 BLOCKED: Attempting to rm -rf an absolute path. This is too dangerous."
    if re.search(r"\bsudo\b", cmd):
        return (
            "⚠ Warning: Using sudo. Are you sure you need root privileges? "
            "On HPC, this is usually not available or allowed."
        )

    # Platform constraint: no model downloads in inference context
    if re.search(r"\b(wget|curl)\b.*\b(huggingface|hf\.co|models/)\b", cmd, re.IGNORECASE):
        return (
            "⚠ Warning: This looks like a direct model download. "
            "Models should be staged via `r4 model stage`, not downloaded "
            "directly. See development instructions: 'No Model Downloads Inside RiVault'."
        )

    # pip install in system python
    if re.search(r"\bpip\s+install\b", cmd) and "venv" not in cmd and "conda" not in cmd:
        return (
            "⚠ Warning: Installing with pip outside a virtual environment. "
            "Consider using a venv or conda environment."
        )

    return None


def _check_python_pre(args: dict) -> str | None:
    """Validate Python code before execution."""
    code = args.get("code", "")

    # Syntax check
    try:
        ast.parse(code)
    except SyntaxError as e:
        return f"⚠ Syntax error in Python code (line {e.lineno}): {e.msg}. Fix before running."

    return None


def _check_file_read_pre(args: dict) -> str | None:
    """Validate file exists before reading."""
    path = args.get("path", "")
    if path and not os.path.exists(path):
        return f"⚠ File not found: {path}. Check the path."
    return None


def _check_file_write_pre(args: dict) -> str | None:
    """Validate parent directory exists."""
    path = args.get("path", "")
    if path:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            return f"⚠ Parent directory does not exist: {parent}. Create it first."
    return None


def _check_hpc_submit_pre(args: dict) -> str | None:
    """Validate Slurm script before submission."""
    script_path = args.get("script_path", "")

    if not script_path:
        return "⚠ No script path provided."
    if not os.path.exists(script_path):
        return f"⚠ Script not found: {script_path}"

    try:
        content = open(script_path).read()
    except OSError:
        return None  # Can't read — let sbatch handle it

    warnings: list[str] = []

    # Check for shebang
    if not content.startswith("#!"):
        warnings.append("Missing shebang (#!/bin/bash)")

    # Check TRANSFORMERS_OFFLINE for inference jobs
    if "vllm" in content.lower() or "inference" in content.lower():
        if "TRANSFORMERS_OFFLINE" not in content:
            warnings.append(
                "Inference job missing TRANSFORMERS_OFFLINE=1. "
                "Add 'export TRANSFORMERS_OFFLINE=1' to prevent model downloads."
            )

    # GPU over-allocation heuristic
    gpu_match = re.search(r"#SBATCH\s+--gres=gpu:(\d+)", content)
    if gpu_match:
        gpus = int(gpu_match.group(1))
        # Simple heuristic: small models don't need many GPUs
        model_sizes = re.findall(r"(\d+)[Bb]", content)
        for size_str in model_sizes:
            size = int(size_str)
            if size <= 7 and gpus > 2:
                warnings.append(
                    f"Requesting {gpus} GPUs for a ~{size}B model. "
                    "Consider reducing — 1-2 GPUs is usually sufficient for ≤7B models."
                )
            elif size <= 14 and gpus > 4:
                warnings.append(
                    f"Requesting {gpus} GPUs for a ~{size}B model. Consider reducing to 2-4."
                )

    # Time limit check
    time_match = re.search(r"#SBATCH\s+--time=(\d+):(\d+):(\d+)", content)
    if time_match:
        hours = int(time_match.group(1))
        if hours > 48:
            warnings.append(f"Time limit is {hours} hours. Most HPC policies limit to 24-48h.")

    if warnings:
        return "⚠ Slurm script issues:\n" + "\n".join(f"  - {w}" for w in warnings)
    return None


# Registry of pre-execution checkers
_PRE_CHECKS = {
    "shell_exec": _check_shell_pre,
    "python_exec": _check_python_pre,
    "file_read": _check_file_read_pre,
    "file_write": _check_file_write_pre,
    "file_edit": _check_file_write_pre,
    "hpc_submit": _check_hpc_submit_pre,
}


# ---------------------------------------------------------------------------
# Post-execution checks
# ---------------------------------------------------------------------------


def _post_check_shell(args: dict, result: str) -> str | None:
    """Diagnose failed shell commands."""
    if "command not found" in result:
        cmd_word = args.get("command", "").split()[0] if args.get("command") else "?"
        return (
            f"💡 The command `{cmd_word}` was not found. "
            "Try loading the module (`module load ...`) or checking the PATH."
        )
    if "Permission denied" in result:
        return "💡 Permission denied. Check file permissions with `ls -la`."
    if "No such file or directory" in result:
        return "💡 A file or directory was not found. Verify paths with `ls`."
    return None


def _post_check_python(args: dict, result: str) -> str | None:
    """Diagnose failed Python execution."""
    if "ModuleNotFoundError" in result:
        match = re.search(r"No module named '(\w+)'", result)
        if match:
            mod = match.group(1)
            return (
                f"💡 Module `{mod}` is not installed. Install with `pip install {mod}` (in a venv)."
            )
    if "ImportError" in result:
        return "💡 Import error. Check that packages are installed in the active environment."
    return None


def _post_check_hpc(args: dict, result: str) -> str | None:
    """Diagnose Slurm submission failures."""
    if "Invalid partition" in result:
        return "💡 Invalid Slurm partition. Use `sinfo` to see available partitions."
    if "Unable to allocate resources" in result:
        return (
            "💡 Resource allocation failed. The cluster may be busy — "
            "try a smaller allocation or different partition."
        )
    return None


_POST_CHECKS = {
    "shell_exec": _post_check_shell,
    "python_exec": _post_check_python,
    "hpc_submit": _post_check_hpc,
}


def _looks_like_error(result: str) -> bool:
    """Heuristic: does the result look like an error?"""
    error_markers = [
        "error:",
        "Error:",
        "ERROR:",
        "Traceback (most recent call last)",
        "command not found",
        "Permission denied",
        "FAILED",
        "fatal:",
    ]
    return any(m in result for m in error_markers)
