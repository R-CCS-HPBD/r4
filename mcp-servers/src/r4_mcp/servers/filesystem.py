"""
Filesystem MCP Server (M-101).

Provides sandboxed file system access for LLMs.

Features:
- Chroot-style path restrictions (can't escape root)
- Optional read-only mode
- File listing with metadata
- Content reading with size limits
- Directory creation and file writing (when not read-only)

Security:
- All paths are resolved and validated against root
- Symlinks are followed but must resolve within root
- No execution of files
- Configurable size limits
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from ..base import MCPServer, Tool, ToolResult, TransportType

logger = logging.getLogger(__name__)


class FilesystemServer(MCPServer):
    """
    Filesystem MCP server with sandboxed access.

    Provides tools for reading and writing files within a restricted
    directory tree.
    """

    name = "filesystem"
    version = "1.0.0"

    def __init__(
        self,
        root: str = ".",
        readonly: bool = False,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        transport: TransportType = TransportType.STDIO,
    ):
        """
        Initialize filesystem server.

        Args:
            root: Root directory for file access.
            readonly: If True, disable write operations.
            max_file_size: Maximum file size to read (bytes).
            transport: MCP transport type.
        """
        self.root = Path(root).resolve()
        self.readonly = readonly
        self.max_file_size = max_file_size

        if not self.root.exists():
            raise ValueError(f"Root directory does not exist: {self.root}")
        if not self.root.is_dir():
            raise ValueError(f"Root is not a directory: {self.root}")

        super().__init__(transport)
        logger.info(f"Filesystem server initialized: root={self.root}, readonly={readonly}")

    def _resolve_path(self, path: str) -> Optional[Path]:
        """
        Resolve and validate a path against the root.

        Returns None if path escapes root or is invalid.
        """
        try:
            # Handle both absolute and relative paths
            if os.path.isabs(path):
                resolved = Path(path).resolve()
            else:
                resolved = (self.root / path).resolve()

            # Check if resolved path is within root
            try:
                resolved.relative_to(self.root)
                return resolved
            except ValueError:
                logger.warning(f"Path escapes root: {path}")
                return None

        except Exception as e:
            logger.warning(f"Invalid path {path}: {e}")
            return None

    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to root for display."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    @Tool(
        name="list_directory",
        description="List contents of a directory within the allowed root",
        parameters={
            "path": {
                "type": "string",
                "description": "Directory path relative to root (use '.' for root)",
            }
        },
    )
    async def list_directory(self, path: str = ".") -> ToolResult:
        """List directory contents."""
        resolved = self._resolve_path(path)
        if resolved is None:
            return ToolResult.error(f"Invalid or disallowed path: {path}")

        if not resolved.exists():
            return ToolResult.error(f"Path does not exist: {path}")

        if not resolved.is_dir():
            return ToolResult.error(f"Path is not a directory: {path}")

        try:
            entries = []
            for entry in resolved.iterdir():
                try:
                    stat = entry.stat()
                    entries.append(
                        {
                            "name": entry.name,
                            "type": "directory" if entry.is_dir() else "file",
                            "size": stat.st_size if entry.is_file() else None,
                            "modified": stat.st_mtime,
                        }
                    )
                except (PermissionError, OSError) as e:
                    entries.append(
                        {
                            "name": entry.name,
                            "type": "unknown",
                            "error": str(e),
                        }
                    )

            # Sort: directories first, then files
            entries.sort(key=lambda e: (e["type"] != "directory", e["name"].lower()))

            result = f"Contents of {self._get_relative_path(resolved)}:\n\n"
            for entry in entries:
                if entry["type"] == "directory":
                    result += f"  📁 {entry['name']}/\n"
                elif entry["type"] == "file":
                    size = entry.get("size", 0)
                    size_str = self._format_size(size)
                    result += f"  📄 {entry['name']} ({size_str})\n"
                else:
                    result += f"  ❓ {entry['name']} ({entry.get('error', 'unknown')})\n"

            result += f"\nTotal: {len(entries)} items"
            return ToolResult.success(result)

        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except Exception as e:
            return ToolResult.error(f"Error listing directory: {e}")

    @Tool(
        name="read_file",
        description="Read the contents of a file within the allowed root",
        parameters={
            "path": {
                "type": "string",
                "description": "File path relative to root",
            }
        },
    )
    async def read_file(self, path: str) -> ToolResult:
        """Read file contents."""
        resolved = self._resolve_path(path)
        if resolved is None:
            return ToolResult.error(f"Invalid or disallowed path: {path}")

        if not resolved.exists():
            return ToolResult.error(f"File does not exist: {path}")

        if not resolved.is_file():
            return ToolResult.error(f"Path is not a file: {path}")

        # Check file size
        try:
            size = resolved.stat().st_size
            if size > self.max_file_size:
                return ToolResult.error(
                    f"File too large: {self._format_size(size)} "
                    f"(max: {self._format_size(self.max_file_size)})"
                )
        except OSError as e:
            return ToolResult.error(f"Cannot stat file: {e}")

        try:
            # Try UTF-8 first
            try:
                content = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Fall back to binary representation
                content = f"[Binary file, {self._format_size(size)}]"

            return ToolResult.success(content)

        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except Exception as e:
            return ToolResult.error(f"Error reading file: {e}")

    @Tool(
        name="write_file",
        description="Write content to a file (disabled in read-only mode)",
        parameters={
            "path": {
                "type": "string",
                "description": "File path relative to root",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file",
            },
        },
    )
    async def write_file(self, path: str, content: str) -> ToolResult:
        """Write content to a file."""
        if self.readonly:
            return ToolResult.error("Server is in read-only mode")

        resolved = self._resolve_path(path)
        if resolved is None:
            return ToolResult.error(f"Invalid or disallowed path: {path}")

        # Check content size
        if len(content.encode("utf-8")) > self.max_file_size:
            return ToolResult.error(
                f"Content too large (max: {self._format_size(self.max_file_size)})"
            )

        try:
            # Create parent directories if needed
            resolved.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            resolved.write_text(content, encoding="utf-8")

            return ToolResult.success(
                f"Wrote {len(content)} characters to {self._get_relative_path(resolved)}"
            )

        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except Exception as e:
            return ToolResult.error(f"Error writing file: {e}")

    @Tool(
        name="create_directory",
        description="Create a directory (disabled in read-only mode)",
        parameters={
            "path": {
                "type": "string",
                "description": "Directory path relative to root",
            },
        },
    )
    async def create_directory(self, path: str) -> ToolResult:
        """Create a directory."""
        if self.readonly:
            return ToolResult.error("Server is in read-only mode")

        resolved = self._resolve_path(path)
        if resolved is None:
            return ToolResult.error(f"Invalid or disallowed path: {path}")

        try:
            resolved.mkdir(parents=True, exist_ok=True)
            return ToolResult.success(f"Created directory: {self._get_relative_path(resolved)}")
        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except Exception as e:
            return ToolResult.error(f"Error creating directory: {e}")

    @Tool(
        name="get_file_info",
        description="Get detailed information about a file or directory",
        parameters={
            "path": {
                "type": "string",
                "description": "Path relative to root",
            },
        },
    )
    async def get_file_info(self, path: str) -> ToolResult:
        """Get file or directory info."""
        resolved = self._resolve_path(path)
        if resolved is None:
            return ToolResult.error(f"Invalid or disallowed path: {path}")

        if not resolved.exists():
            return ToolResult.error(f"Path does not exist: {path}")

        try:
            stat = resolved.stat()

            info = [
                f"Path: {self._get_relative_path(resolved)}",
                f"Type: {'directory' if resolved.is_dir() else 'file'}",
                f"Size: {self._format_size(stat.st_size)}",
                f"Modified: {stat.st_mtime}",
                f"Created: {stat.st_ctime}",
            ]

            if resolved.is_file():
                # Guess file type from extension
                suffix = resolved.suffix.lower()
                info.append(f"Extension: {suffix or '(none)'}")

            return ToolResult.success("\n".join(info))

        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except Exception as e:
            return ToolResult.error(f"Error getting file info: {e}")

    @staticmethod
    def _format_size(size: int) -> str:
        """Format file size for display."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


def main():
    """CLI entry point for filesystem server."""
    import argparse

    parser = argparse.ArgumentParser(description="Filesystem MCP Server")
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory for file access",
    )
    parser.add_argument(
        "--readonly",
        action="store_true",
        help="Disable write operations",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=10 * 1024 * 1024,
        help="Maximum file size in bytes",
    )

    args = parser.parse_args()

    server = FilesystemServer(
        root=args.root,
        readonly=args.readonly,
        max_file_size=args.max_size,
    )
    server.run()


if __name__ == "__main__":
    main()
