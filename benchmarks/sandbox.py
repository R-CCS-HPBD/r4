"""
Secure code execution sandbox.

Runs model-generated code in isolated subprocesses with:
- Timeout enforcement
- Sandboxed namespace for Python
- Compilation support for C/C++/MPI/CUDA
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SandboxResult:
    """Result from sandbox execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    timed_out: bool = False


@dataclass
class CompileResult:
    """Result from compilation step."""

    success: bool
    binary_path: Optional[str] = None
    stderr: str = ""
    exit_code: int = -1
    error: Optional[str] = None


_TEMPLATE = r"""
import sys, math, traceback

__code__

__test_code__

def __run_test(name, fn):
    try:
        fn()
        return {"name": name, "passed": True}
    except AssertionError as e:
        return {"name": name, "passed": False, "error": str(e)}
    except Exception as e:
        return dict(name=name, passed=False, error=f"{type(e).__name__}: {e}")

if __name__ == "__main__":
    results = {"passed": 0, "failed": 0, "errors": []}
__runner_block__
    print("__PASSED__:" + str(results["passed"]))
    print("__FAILED__:" + str(results["failed"]))
    for err in results["errors"]:
        print("__ERROR__:" + err)
    sys.exit(0 if results["failed"] == 0 else 1)
"""


def run_python_sandbox(
    code: str,
    test_code: str,
    timeout_sec: int = 10,
) -> SandboxResult:
    """Run Python code with provided pytest-style tests in a sandboxed namespace."""
    start = time.perf_counter()

    # Extract test function names
    test_names = re.findall(r"^def (test_\w+)", test_code, re.MULTILINE)
    runner_lines = []
    for name in test_names:
        runner_lines.append(f'    r = __run_test("{name}", {name})')
        runner_lines.append('    results["passed"] += 1 if r["passed"] else 0')
        runner_lines.append('    results["failed"] += 0 if r["passed"] else 1')
        runner_lines.append(
            '    if not r["passed"]: results["errors"].append(r["name"] + ": " + r["error"])'
        )

    runner_block = "\n".join(runner_lines)

    full_script = (
        _TEMPLATE.replace("__code__", code)
        .replace("__test_code__", test_code)
        .replace("__runner_block__", runner_block)
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-c", full_script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env={**os.environ, "PYTHONHASHSEED": "0"},
        )

        elapsed = (time.perf_counter() - start) * 1000

        passed = 0
        failed = 0
        errors = []
        for line in proc.stdout.splitlines():
            if line.startswith("__PASSED__:"):
                passed = int(line.split(":", 1)[1])
            elif line.startswith("__FAILED__:"):
                failed = int(line.split(":", 1)[1])
            elif line.startswith("__ERROR__:"):
                errors.append(line.split(":", 1)[1])

        clean_stdout = "\n".join(l for l in proc.stdout.splitlines() if not l.startswith("__"))

        return SandboxResult(
            success=(proc.returncode == 0 and failed == 0),
            stdout=clean_stdout,
            stderr=proc.stderr[:2000],
            exit_code=proc.returncode,
            elapsed_ms=elapsed,
            timed_out=False,
            error="; ".join(errors)
            if errors
            else (f"Passed:{passed} Failed:{failed}" if failed > 0 else None),
        )

    except subprocess.TimeoutExpired:
        elapsed = (time.perf_counter() - start) * 1000
        return SandboxResult(
            success=False,
            stdout="",
            stderr="",
            exit_code=-1,
            elapsed_ms=elapsed,
            timed_out=True,
            error="Execution timed out",
        )
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return SandboxResult(success=False, error=str(e), elapsed_ms=elapsed)


def compile_c_code(
    code: str,
    compiler: str = "gcc",
    compiler_flags: str = "-O2",
    source_ext: str = ".c",
    work_dir: Optional[str] = None,
) -> CompileResult:
    """Compile C/C++/CUDA source code."""
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="bench_sandbox_")

    src_path = Path(work_dir) / f"program{source_ext}"
    binary_path = Path(work_dir) / "program"
    src_path.write_text(code)

    cmd = [compiler] + compiler_flags.split() + ["-o", str(binary_path), str(src_path)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            os.chmod(binary_path, 0o755)
            return CompileResult(
                success=True, binary_path=str(binary_path), stderr=proc.stderr[:2000], exit_code=0
            )
        else:
            return CompileResult(
                success=False,
                stderr=proc.stderr[:2000],
                exit_code=proc.returncode,
                error=f"Compilation failed: {proc.stderr[:500]}",
            )
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, error="Compilation timed out")
    except FileNotFoundError:
        return CompileResult(success=False, error=f"Compiler '{compiler}' not found.")


def run_c_binary(
    binary_path: str,
    timeout_sec: int = 15,
    env: Optional[dict] = None,
    run_cmd_template: Optional[str] = None,
) -> SandboxResult:
    """Run a compiled C/C++ binary and capture output."""
    start = time.perf_counter()

    if run_cmd_template:
        cmd = run_cmd_template.replace("{binary}", binary_path).split()
    else:
        cmd = [binary_path]

    run_env = {**os.environ}
    if env:
        run_env.update(env)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, env=run_env)
        elapsed = (time.perf_counter() - start) * 1000
        return SandboxResult(
            success=(proc.returncode == 0),
            stdout=proc.stdout[:5000],
            stderr=proc.stderr[:2000],
            exit_code=proc.returncode,
            elapsed_ms=elapsed,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            success=False,
            timed_out=True,
            elapsed_ms=(time.perf_counter() - start) * 1000,
            error="Execution timed out",
        )
    except Exception as e:
        return SandboxResult(
            success=False, error=str(e), elapsed_ms=(time.perf_counter() - start) * 1000
        )


def extract_function_code(output: str, pattern: Optional[str] = None) -> Optional[str]:
    """Extract just the function code from model output.

    Handles markdown code blocks and bare function definitions.
    """
    # Try code block first
    code_block = re.search(r"```(?:python|py)?\s*\n(.*?)```", output, re.DOTALL)
    if code_block:
        code = code_block.group(1)
    else:
        code = output

    # Find the function definition
    if pattern:
        match = re.search(pattern, code)
        if not match:
            return None

    # Extract from function def to end (stop at next top-level def)
    lines = code.splitlines()
    func_lines = []
    in_func = False
    for line in lines:
        if re.match(r"^def\s+\w+\s*\(", line):
            if in_func:
                break
            in_func = True
        if in_func:
            func_lines.append(line)

    if not func_lines:
        return None

    return "\n".join(func_lines)
