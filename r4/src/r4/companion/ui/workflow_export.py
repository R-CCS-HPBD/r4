"""``workflow.export`` for the console (2026-11-07): save a visible workflow as a zip.

The console asks for one workflow the caller can see (own, shared to them, or
shared to their project); the bridge picks a local folder and a file name
that never overwrites anything, and hands both to the one SDK path,
``WorkflowClient.export`` (it streams UMRS ``GET /workflows/{name}/export``
and verifies the archive's EXPORT.json checksums before it returns).

Folder: an explicit ``dest_dir`` param when the console sends one, else the
bridge's working directory when writable, else ``~/Downloads`` when
writable; a numeric suffix (``name-1.zip``, ``name-2.zip``…) keeps every
earlier export. The verb is a READ (it may overlap other verbs and other
exports, 2026-11-07), so a chosen name stays reserved in-process until its
download ends: two overlapping exports never land on the same file.
"""

from __future__ import annotations

import json
import os
import re
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Characters a workflow name may keep in a local file name.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

#: Highest numeric suffix tried before the export is refused.
_MAX_SUFFIX = 999

#: Targets of exports still downloading in this process (not on disk yet).
_RESERVED: set[Path] = set()
_RESERVED_LOCK = threading.Lock()


def _writable_dir(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def export_dest_dir(
    dest_dir: str = "", *, cwd: Path | None = None, home: Path | None = None
) -> Path:
    """The folder an export lands in: explicit, else cwd, else ~/Downloads."""
    if dest_dir:
        chosen = Path(dest_dir).expanduser()
        if not _writable_dir(chosen):
            raise ValueError(f"workflow.export: {chosen} is not a writable folder")
        return chosen
    for candidate in (cwd or Path.cwd(), (home or Path.home()) / "Downloads"):
        if _writable_dir(candidate):
            return candidate
    raise ValueError(
        "workflow.export: neither the working directory nor ~/Downloads is writable"
        " — run `r4 workflow export NAME -o PATH` with a writable path"
    )


def export_file_name(workflow_name: str) -> str:
    """A local file stem from a workflow name (no separators, never empty)."""
    stem = _UNSAFE_FILENAME.sub("_", workflow_name).strip("._")
    return stem or "workflow"


def _taken(path: Path) -> bool:
    return path in _RESERVED or path.exists()


@contextmanager
def _reserved_export_path(directory: Path, workflow_name: str) -> Iterator[Path]:
    """A free export path, held reserved for the ``with`` body."""
    with _RESERVED_LOCK:
        target = free_export_path(directory, workflow_name)
        _RESERVED.add(target)
    try:
        yield target
    finally:
        with _RESERVED_LOCK:
            _RESERVED.discard(target)


def free_export_path(directory: Path, workflow_name: str) -> Path:
    """``<dir>/<name>.zip``, else the first free ``<name>-N.zip``: never an
    existing file nor one an overlapping export has reserved."""
    stem = export_file_name(workflow_name)
    first = directory / f"{stem}.zip"
    if not _taken(first):
        return first
    for suffix in range(1, _MAX_SUFFIX + 1):
        candidate = directory / f"{stem}-{suffix}.zip"
        if not _taken(candidate):
            return candidate
    raise ValueError(f"workflow.export: {directory} already holds {_MAX_SUFFIX} exports of {stem}")


def manifest_counts(path: Path) -> tuple[int | None, int | None]:
    """(files, excluded) from the archive's EXPORT.json; (None, None) when unreadable."""
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("EXPORT.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None, None
    if not isinstance(manifest, dict):
        return None, None
    files, excluded = manifest.get("files"), manifest.get("excluded")
    return (
        len(files) if isinstance(files, list) else None,
        len(excluded) if isinstance(excluded, list) else None,
    )


def export_workflow(workflows: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run one console export through ``WorkflowClient.export``; never overwrites."""
    workflow_name = str(params.get("workflow_name") or "").strip()
    if not workflow_name:
        raise ValueError("workflow.export params.workflow_name is required")
    directory = export_dest_dir(str(params.get("dest_dir") or "").strip())
    with _reserved_export_path(directory, workflow_name) as target:
        written = Path(workflows.export(workflow_name, target, overwrite=False))
    files, excluded = manifest_counts(written)
    return {
        "ok": True,
        "workflow_name": workflow_name,
        "path": str(written),
        "files": files,
        "excluded": excluded,
    }
