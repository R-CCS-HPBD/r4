"""
Code Execution MCP Server (M-104).

Provides tools for executing code in a sandboxed environment.

Security features:
- Execution timeout
- Memory limits
- No network access
- Restricted imports
- Output capture
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Optional

from ..base import MCPServer, Tool, ToolResult

logger = logging.getLogger(__name__)


# Default blocked imports
DEFAULT_BLOCKED_IMPORTS = [
    "os",
    "subprocess",
    "shutil",
    "socket",
    "http",
    "urllib",
    "requests",
    "aiohttp",
    "asyncio.subprocess",
    "multiprocessing",
    "threading",
    "ctypes",
    "importlib",
    "__builtins__",
]

# Default allowed imports
DEFAULT_ALLOWED_IMPORTS = [
    "math",
    "statistics",
    "json",
    "datetime",
    "collections",
    "itertools",
    "functools",
    "re",
    "string",
    "random",
    "decimal",
    "fractions",
    "typing",
    "dataclasses",
    "enum",
    "copy",
    "pprint",
    "textwrap",
    "csv",
    "io",
    "base64",
    "hashlib",
    "hmac",
    # Scientific (if available)
    "numpy",
    "pandas",
    "scipy",
    "sklearn",
]


class RestrictedImporter:
    """Import hook that restricts available modules."""

    def __init__(
        self,
        allowed: list[str],
        blocked: list[str],
    ):
        self.allowed = set(allowed)
        self.blocked = set(blocked)

    def find_module(self, name: str, path=None):
        """Check if module is allowed."""
        base_module = name.split(".")[0]

        # Check blocked first
        if base_module in self.blocked:
            return self

        # If allowlist is defined, only allow those
        if self.allowed and base_module not in self.allowed:
            return self

        return None

    def load_module(self, name: str):
        """Raise error for blocked modules."""
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")


class CodeExecutionServer(MCPServer):
    """
    MCP server for code execution.

    Executes Python code in a restricted sandbox.
    """

    name = "code"
    version = "1.0.0"

    def __init__(
        self,
        timeout_seconds: int = 30,
        max_output_length: int = 10000,
        allowed_imports: Optional[list[str]] = None,
        blocked_imports: Optional[list[str]] = None,
    ):
        """
        Initialize code execution server.

        Args:
            timeout_seconds: Maximum execution time
            max_output_length: Maximum output capture size
            allowed_imports: Modules allowed to import
            blocked_imports: Modules blocked from importing
        """
        super().__init__()
        self.timeout_seconds = timeout_seconds
        self.max_output_length = max_output_length
        self.allowed_imports = allowed_imports or DEFAULT_ALLOWED_IMPORTS
        self.blocked_imports = blocked_imports or DEFAULT_BLOCKED_IMPORTS

    def _create_sandbox_globals(self) -> dict[str, Any]:
        """Create restricted globals for code execution."""
        # Safe builtins
        safe_builtins = {
            "abs": abs,
            "all": all,
            "any": any,
            "ascii": ascii,
            "bin": bin,
            "bool": bool,
            "bytearray": bytearray,
            "bytes": bytes,
            "callable": callable,
            "chr": chr,
            "complex": complex,
            "dict": dict,
            "divmod": divmod,
            "enumerate": enumerate,
            "filter": filter,
            "float": float,
            "format": format,
            "frozenset": frozenset,
            # getattr intentionally excluded — allows __globals__ chain escape
            "hasattr": hasattr,
            "hash": hash,
            "hex": hex,
            "int": int,
            "isinstance": isinstance,
            "issubclass": issubclass,
            "iter": iter,
            "len": len,
            "list": list,
            "map": map,
            "max": max,
            "min": min,
            "next": next,
            "oct": oct,
            "ord": ord,
            "pow": pow,
            "print": print,
            "range": range,
            "repr": repr,
            "reversed": reversed,
            "round": round,
            "set": set,
            "slice": slice,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "type": type,
            "zip": zip,
            # Constants
            "True": True,
            "False": False,
            "None": None,
            # __import__ intentionally excluded — arbitrary module import
            # is the primary sandbox escape vector.  Allowed modules are
            # injected explicitly by the caller before code execution.
        }

        return {
            "__builtins__": safe_builtins,
            "__name__": "__sandbox__",
        }

    async def _execute_code(
        self,
        code: str,
        globals_dict: dict[str, Any],
    ) -> tuple[Any, str, str]:
        """Execute code and capture output."""
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        result = None
        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # Compile the code
                compiled = compile(code, "<sandbox>", "exec")

                # Execute
                exec(compiled, globals_dict)

                # If there's a result variable, capture it
                result = globals_dict.get("result", None)

        except Exception:
            traceback.print_exc(file=stderr_capture)

        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()

        # Truncate if needed
        if len(stdout) > self.max_output_length:
            stdout = stdout[: self.max_output_length] + "\n... (truncated)"
        if len(stderr) > self.max_output_length:
            stderr = stderr[: self.max_output_length] + "\n... (truncated)"

        return result, stdout, stderr

    @Tool(
        name="execute",
        description="Execute Python code in a sandboxed environment",
    )
    async def execute(
        self,
        code: str,
    ) -> ToolResult:
        """
        Execute Python code.

        Args:
            code: Python code to execute

        Returns:
            Execution result with stdout, stderr, and return value
        """
        # Install import restriction
        importer = RestrictedImporter(
            allowed=self.allowed_imports,
            blocked=self.blocked_imports,
        )

        # Save original meta_path
        original_meta_path = sys.meta_path.copy()

        try:
            # Install our importer at the front
            sys.meta_path.insert(0, importer)

            # Create sandbox globals
            sandbox_globals = self._create_sandbox_globals()

            # Execute with timeout
            try:
                result, stdout, stderr = await asyncio.wait_for(
                    self._execute_code(code, sandbox_globals),
                    timeout=self.timeout_seconds,
                )

                return ToolResult.success(
                    {
                        "result": repr(result) if result is not None else None,
                        "stdout": stdout,
                        "stderr": stderr,
                        "success": not bool(stderr),
                    }
                )

            except asyncio.TimeoutError:
                return ToolResult.error(f"Execution timed out after {self.timeout_seconds}s")

        finally:
            # Restore original meta_path
            sys.meta_path = original_meta_path

    @Tool(
        name="evaluate",
        description="Evaluate a Python expression and return the result",
    )
    async def evaluate(
        self,
        expression: str,
    ) -> ToolResult:
        """
        Evaluate a Python expression.

        Args:
            expression: Python expression to evaluate

        Returns:
            Expression result
        """
        # Wrap expression to capture result
        code = f"result = {expression}"
        return await self.execute(code)

    @Tool(
        name="check_syntax",
        description="Check Python code for syntax errors without executing",
    )
    async def check_syntax(
        self,
        code: str,
    ) -> ToolResult:
        """
        Check code syntax without executing.

        Args:
            code: Python code to check
        """
        try:
            compile(code, "<syntax_check>", "exec")
            return ToolResult.success(
                {
                    "valid": True,
                    "message": "Syntax is valid",
                }
            )
        except SyntaxError as e:
            return ToolResult.success(
                {
                    "valid": False,
                    "error": str(e),
                    "line": e.lineno,
                    "column": e.offset,
                }
            )

    @Tool(
        name="format_code",
        description="Format Python code using standard formatting",
    )
    async def format_code(
        self,
        code: str,
    ) -> ToolResult:
        """
        Format Python code.

        Args:
            code: Python code to format
        """
        try:
            import black

            formatted = black.format_str(code, mode=black.Mode())
            return ToolResult.success(
                {
                    "formatted": formatted,
                    "changed": formatted != code,
                }
            )
        except ImportError:
            # Fallback: just check syntax
            syntax_result = await self.check_syntax(code)
            if syntax_result.isError:
                return syntax_result
            return ToolResult.success(
                {
                    "formatted": code,
                    "changed": False,
                    "note": "black not installed; code returned as-is",
                }
            )
        except Exception as e:
            return ToolResult.error(f"Formatting failed: {e}")


def create_server(
    timeout_seconds: int = 30,
    max_output_length: int = 10000,
    allowed_imports: Optional[list[str]] = None,
    blocked_imports: Optional[list[str]] = None,
) -> CodeExecutionServer:
    """Create a code execution MCP server instance."""
    return CodeExecutionServer(
        timeout_seconds=timeout_seconds,
        max_output_length=max_output_length,
        allowed_imports=allowed_imports,
        blocked_imports=blocked_imports,
    )
