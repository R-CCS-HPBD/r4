"""A plan agent's workspace, honoured by the file, shell and python tools.

ADR-092 slice 5. Inside a plan agent's turn the broker sets the agent's
workspace in a context variable (:func:`agent_workspace`) around every tool
call. While one is set:

- relative paths and the working directory resolve under the workspace;
- a path is resolved to its real path first (``..`` and every symlink
  followed), so neither traversal nor a symlink leaves the workspace;
- a write (``file_write``, ``file_edit``, ``apply_patch``, the cwd of
  ``shell_exec`` and ``python_exec``) must land inside the workspace;
- a read (``file_read``, ``grep_search``, ``glob_search``, ``list_dir``) must
  land inside the workspace or the project root;
- anything else is refused by name (:class:`WorkspaceEscape`).

An agent that runs with ``isolation: none`` has the project root as its
workspace; an isolated one has its own worktree or copy, and may read (not
write) the project root it was made from.

What this does NOT contain: the text of a shell command or of python code.
``shell_exec`` and ``python_exec`` start in the workspace, but a command can
still name any absolute path; those calls keep the workstation posture
under the permission ladder (ADR-092 D-11). Isolation keeps parallel agents
off one another's files; it is not a sandbox.

With no workspace set (the lead's own session, a team role) every tool
behaves as it always has.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class WorkspaceEscape(ValueError):
    """A path an agent named resolves outside what its workspace allows."""


@dataclass(frozen=True)
class AgentWorkspace:
    """Where one plan agent works. Both paths are real paths (no symlinks)."""

    root: Path
    project_root: Path
    label: str = ""

    @classmethod
    def of(cls, root: str | Path, project_root: str | Path | None = None, label: str = ""):
        """Real paths of both; ``project_root`` defaults to ``root``."""
        real = Path(os.path.realpath(root))
        project = Path(os.path.realpath(project_root)) if project_root else real
        return cls(root=real, project_root=project, label=label)

    @property
    def isolated(self) -> bool:
        """True for a worktree or copy of its own; False in the project root."""
        return self.root != self.project_root


_CURRENT: contextvars.ContextVar[AgentWorkspace | None] = contextvars.ContextVar(
    "r4_agent_workspace", default=None
)


def current_workspace() -> AgentWorkspace | None:
    """The workspace of the plan agent whose tool call is running, or None."""
    return _CURRENT.get()


@contextlib.contextmanager
def agent_workspace(workspace: AgentWorkspace | None) -> Iterator[None]:
    """Run the enclosed tool call in ``workspace`` (None: no containment)."""
    token = _CURRENT.set(workspace)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve(path: str | os.PathLike[str] | None, *, write: bool) -> Path:
    """``path`` as the tool should use it, or :class:`WorkspaceEscape`.

    Without a workspace this is ``Path(path).expanduser()``, unchanged.
    With one, a relative path is joined to the workspace root, the result is
    resolved to its real path (``..`` collapsed, every symlink followed, the
    missing tail of a new file kept), and it must lie inside the workspace,
    or, for a read, inside the workspace or the project root.
    """
    workspace = _CURRENT.get()
    raw = Path(str(path if path not in (None, "") else ".")).expanduser()
    if workspace is None:
        return raw
    joined = raw if raw.is_absolute() else workspace.root / raw
    real = Path(os.path.realpath(joined))
    allowed = (workspace.root,) if write else (workspace.root, workspace.project_root)
    if any(_within(real, root) for root in allowed):
        return real
    where = "the agent's workspace" if write else "the agent's workspace or the project root"
    kind = "write" if write else "read"
    raise WorkspaceEscape(
        f"refused: {kind} path {str(path)!r} resolves to {real}, outside {where} "
        f"({workspace.root}) (ADR-092 slice 5)"
    )


def resolve_cwd(cwd: str | None) -> str | None:
    """A process's working directory: the workspace root by default, any
    ``cwd`` given resolved inside the workspace (a process may write there)."""
    if _CURRENT.get() is None:
        return cwd
    return str(resolve(cwd or ".", write=True))


def contained(path: Path) -> bool:
    """True when ``path`` (resolved) is readable under the current workspace;
    always True without one. Filters search results that a symlink led out."""
    workspace = _CURRENT.get()
    if workspace is None:
        return True
    real = Path(os.path.realpath(path))
    return _within(real, workspace.root) or _within(real, workspace.project_root)
