"""Removing the workspaces finished or abandoned plan runs kept (ADR-092 slice 5).

A run keeps an isolated workspace at its end only while a ``merge_back``
that names its step is pending, so a resume can still merge it. A run that is
never resumed keeps its worktrees, ``r4-plan/<run>/*`` branches and copies
for good. ``r4 team plan clean`` removes them, one run at a time, through
:func:`clean_run`.

A run is cleaned only when one of these holds:

- its ``result.json`` is terminal (success, partial, failed, cancelled) for
  its LAST attempt (a later attempt with no result yet may be running);
- ``older_than_days`` is given and its journal and event log were last
  written longer ago than that (an abandoned attempt; the caller asserts it).

And never when its journal holds a human's yes to a merge-back that was not
applied (a resume replays that yes and applies the change set; deleting the
workspaces would throw away work a human approved), whatever else holds.

What is removed: every workspace the journal does not record as removed
(``git worktree remove --force`` and its branch, or the copy), any other
directory left under ``<run_dir>/workspaces``, stale worktree records
(``git worktree prune``) and every branch under ``r4-plan/<run>/`` in the
repositories the run used, never another run's. Each removal is journalled.
No git hook runs (``workspace._GIT_SAFE``). ``dry_run`` reports and touches
nothing.

Stdlib only: the plan package's import fence (ADR-092 D-2).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from r4_contracts.team_engine.plan import MERGE_BACK_CLASS

from . import workspace as ws_mod
from .isolation import RunWorkspaces
from .journal import JOURNAL_FILE, PlanJournal
from .workspace import PROJECT_ROOT_KEY, WORKSPACES_DIR, Workspace

TERMINAL_STATUSES = frozenset({"success", "partial", "failed", "cancelled"})
CLEAN_ATTEMPT = "clean"  # the journal's ``attempt`` of a removal made by clean
_DAY = 86400.0


@dataclass
class CleanReport:
    """What cleaning one run did, or would do (``dry_run``), and why."""

    run_id: str
    eligible: bool
    reason: str
    workspaces: list[str] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def has_work(self) -> bool:
        return bool(self.workspaces or self.branches)


@dataclass
class _Leftovers:
    kept: list[Workspace]
    dirs: list[Path]
    repos: list[Path]


def clean_run(
    run_dir: Path,
    *,
    older_than_days: float | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> CleanReport:
    """Clean one run directory if it may be cleaned; report either way."""
    records = PlanJournal(run_dir).records()
    left = _leftovers(run_dir, records)
    branches = _branches(left.repos, run_dir.name)
    eligible, reason = _eligibility(run_dir, records, older_than_days, now)
    report = CleanReport(
        run_id=run_dir.name,
        eligible=eligible,
        reason=reason,
        workspaces=[ws.path for ws in left.kept] + [str(d) for d in left.dirs],
        branches=[branch for _repo, branch in branches],
        dry_run=dry_run,
    )
    if eligible and not dry_run and report.has_work:
        report.errors = _remove(run_dir, left)
    return report


def clean_all(
    run_root: Path,
    *,
    older_than_days: float | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> list[CleanReport]:
    """:func:`clean_run` over every run directory under ``run_root``."""
    if not run_root.is_dir():
        return []
    return [
        clean_run(d, older_than_days=older_than_days, dry_run=dry_run, now=now)
        for d in sorted(run_root.iterdir())
        if d.is_dir()
    ]


# ── may this run be cleaned? ────────────────────────────────────────────────


def _eligibility(
    run_dir: Path, records: list[dict[str, Any]], older_than_days: float | None, now: float | None
) -> tuple[bool, str]:
    pending = pending_merge_answers(records)
    if pending:
        where = ", ".join(pending)
        return False, f"a human approved merge-back {where}, not yet applied: resume the run"
    status = terminal_status(run_dir)
    if status is not None:
        return True, f"finished ({status})"
    idle = _idle_days(run_dir, now)
    if older_than_days is not None and idle is not None and idle > older_than_days:
        return True, f"idle for {idle:.1f} days (more than {older_than_days:g})"
    return False, (
        "running or unfinished (its last attempt has no terminal result); "
        "pass --older-than DAYS to clean an abandoned run"
    )


def pending_merge_answers(records: list[dict[str, Any]]) -> list[str]:
    """Merge-backs a human approved whose merge was never journalled as done."""
    approved = {
        str(r.get("node"))
        for r in records
        if r.get("kind") == "gate" and r.get("class") == MERGE_BACK_CLASS and r.get("ok")
    }
    done = {str(r.get("node")) for r in records if r.get("kind") == "merge_back"}
    return sorted(approved - done)


def terminal_status(run_dir: Path) -> str | None:
    """The run's terminal status when its LAST attempt wrote one, else None."""
    doc = _json(run_dir / "result.json")
    status = doc.get("status")
    if status not in TERMINAL_STATUSES:
        return None
    attempt = _json(run_dir / "resolution.json").get("attempt")
    if attempt and doc.get("attempt") != f"attempt-{attempt}":
        return None  # a later attempt started and has not finished
    return str(status)


def _idle_days(run_dir: Path, now: float | None) -> float | None:
    """Days since the journal or the event log was last written."""
    files = (run_dir / JOURNAL_FILE, run_dir / "events.jsonl")
    stamps = [path.stat().st_mtime for path in files if path.is_file()]
    if not stamps:
        return None
    return ((now if now is not None else time.time()) - max(stamps)) / _DAY


# ── what is left, and removing it ───────────────────────────────────────────


def _leftovers(run_dir: Path, records: list[dict[str, Any]]) -> _Leftovers:
    recorded = _recorded(records)
    kept = [ws for ws, status in recorded if status != "removed" and Path(ws.path).exists()]
    return _Leftovers(kept, _stray_dirs(run_dir, kept), _repos(run_dir, recorded))


def _recorded(records: list[dict[str, Any]]) -> list[tuple[Workspace, Any]]:
    """Every workspace the journal names, with its latest status."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("kind") == "workspace" and isinstance(record.get("workspace"), dict):
            latest[str(record.get("id"))] = record
    pairs = [(Workspace.from_record(r["workspace"]), r.get("status")) for r in latest.values()]
    return [(ws, status) for ws, status in pairs if ws is not None]


def _stray_dirs(run_dir: Path, kept: list[Workspace]) -> list[Path]:
    """Directories under ``workspaces/`` no kept record covers (a crash, a
    removal that failed)."""
    home = run_dir / WORKSPACES_DIR
    if not home.is_dir():
        return []
    kept_paths = {os.path.realpath(ws.path) for ws in kept}
    return [
        d for d in sorted(home.iterdir()) if d.is_dir() and os.path.realpath(d) not in kept_paths
    ]


def _repos(run_dir: Path, recorded: list[tuple[Workspace, Any]]) -> list[Path]:
    """Every repository the run made worktrees in, and its project's."""
    found = {Path(ws.repo) for ws, _status in recorded if ws.repo}
    project = ws_mod.repo_of(_json(run_dir / "resolution.json").get(PROJECT_ROOT_KEY))
    if project is not None:
        found.add(project)
    return sorted(repo for repo in found if repo.is_dir())


def _branches(repos: list[Path], run_id: str) -> list[tuple[Path, str]]:
    return [(repo, branch) for repo in repos for branch in ws_mod.run_branches(repo, run_id)]


def _remove(run_dir: Path, left: _Leftovers) -> list[str]:
    errors: list[str] = []
    book = RunWorkspaces(run_dir, PlanJournal(run_dir), None)
    for ws in left.kept:
        error = ws_mod.remove(ws, run_dir)
        book.removed(ws, CLEAN_ATTEMPT, error)
        errors += [error] if error else []
    for path in left.dirs:
        shutil.rmtree(path, ignore_errors=True)
        errors += [f"{path} not removed"] if path.exists() else []
    for repo in left.repos:
        ws_mod.prune_worktrees(repo)
    for repo, branch in _branches(left.repos, run_dir.name):
        if not ws_mod.delete_branch(repo, branch):
            errors.append(f"branch {branch} not deleted in {repo}")
    return errors


def _json(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}
