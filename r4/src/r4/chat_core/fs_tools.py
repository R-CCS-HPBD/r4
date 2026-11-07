"""Local read-only filesystem tools for the chat REPL.

These tools stay client-side: they do not register the user's local disk with
Gateway/UMRS, and they only expose a small read-only surface rooted at a
single configured directory tree.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DEFAULT_MAX_FILE_SIZE = 256 * 1024


def _tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    execute,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "execute": execute,
    }


def _max_file_size_bytes() -> int:
    try:
        kb = int(os.environ.get("R4_CHAT_FS_MAX_KB", str(_DEFAULT_MAX_FILE_SIZE // 1024)))
    except ValueError:
        kb = _DEFAULT_MAX_FILE_SIZE // 1024
    return max(kb, 1) * 1024


class LocalFilesystemTools:
    """Read-only file tools confined to a single root directory."""

    def __init__(self, root: str | Path, *, max_file_size: int | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            raise ValueError(f"Root directory does not exist: {self.root}")
        if not self.root.is_dir():
            raise ValueError(f"Root is not a directory: {self.root}")
        self.max_file_size = max_file_size or _max_file_size_bytes()

    def tools(self) -> list[dict[str, Any]]:
        return [
            _tool(
                "list_directory",
                "List contents of a directory within the configured filesystem root",
                {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path relative to the configured root; "
                                "use '.' for the root itself."
                            ),
                        }
                    },
                },
                self.list_directory,
            ),
            _tool(
                "read_file",
                "Read a UTF-8 text file within the configured filesystem root",
                {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to the configured root.",
                        }
                    },
                    "required": ["path"],
                },
                self.read_file,
            ),
            _tool(
                "get_file_info",
                "Get metadata about a file or directory within the configured filesystem root",
                {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the configured root.",
                        }
                    },
                    "required": ["path"],
                },
                self.get_file_info,
            ),
        ]

    def _resolve_path(self, path: str) -> Path | None:
        try:
            candidate = Path(path)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (self.root / candidate).resolve()
            )
            resolved.relative_to(self.root)
            return resolved
        except Exception:
            return None

    def _display_path(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return str(path)
        return "." if str(rel) == "." else str(rel)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"

    def list_directory(self, path: str = ".") -> str:
        resolved = self._resolve_path(path)
        if resolved is None:
            return f"Error: invalid or disallowed path: {path}"
        if not resolved.exists():
            return f"Error: path does not exist: {path}"
        if not resolved.is_dir():
            return f"Error: path is not a directory: {path}"

        entries: list[tuple[str, str | None]] = []
        try:
            for entry in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if entry.is_dir():
                    entries.append((f"  [dir] {entry.name}/", None))
                else:
                    size = self._format_size(entry.stat().st_size)
                    entries.append((f"  [file] {entry.name}", size))
        except OSError as exc:
            return f"Error: could not list directory {path}: {exc}"

        lines = [f"Contents of {self._display_path(resolved)}:"]
        if not entries:
            lines.append("  (empty)")
        else:
            for label, size in entries:
                lines.append(f"{label} ({size})" if size else label)
        return "\n".join(lines)

    def read_file(self, path: str) -> str:
        resolved = self._resolve_path(path)
        if resolved is None:
            return f"Error: invalid or disallowed path: {path}"
        if not resolved.exists():
            return f"Error: file does not exist: {path}"
        if not resolved.is_file():
            return f"Error: path is not a file: {path}"

        try:
            size = resolved.stat().st_size
        except OSError as exc:
            return f"Error: could not stat file {path}: {exc}"
        if size > self.max_file_size:
            return (
                f"Error: file too large: {self._format_size(size)} "
                f"(max: {self._format_size(self.max_file_size)})"
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: file is not valid UTF-8 text: {path}"
        except OSError as exc:
            return f"Error: could not read file {path}: {exc}"

        header = f"File: {self._display_path(resolved)} ({self._format_size(size)})"
        return f"{header}\n\n{content}"

    def get_file_info(self, path: str) -> str:
        resolved = self._resolve_path(path)
        if resolved is None:
            return f"Error: invalid or disallowed path: {path}"
        if not resolved.exists():
            return f"Error: path does not exist: {path}"

        try:
            stat = resolved.stat()
        except OSError as exc:
            return f"Error: could not inspect path {path}: {exc}"

        lines = [
            f"Path: {self._display_path(resolved)}",
            f"Type: {'directory' if resolved.is_dir() else 'file'}",
            f"Size: {self._format_size(stat.st_size)}",
            f"Modified: {stat.st_mtime}",
            f"Created: {stat.st_ctime}",
        ]
        if resolved.is_file():
            lines.append(f"Extension: {resolved.suffix or '(none)'}")
        return "\n".join(lines)


def build_local_fs_tools(
    root: str | Path, *, max_file_size: int | None = None
) -> list[dict[str, Any]]:
    """Return read-only local filesystem tool definitions for the given root."""
    return LocalFilesystemTools(root, max_file_size=max_file_size).tools()


__all__ = ["LocalFilesystemTools", "build_local_fs_tools"]
