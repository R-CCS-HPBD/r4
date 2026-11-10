"""A plan run's isolated workspaces: registry, merge batches, cleanup (slice 5).

``RunWorkspaces`` is the run's view of its workspaces. It makes one when an
isolated agent first runs live, remembers which agent calls of this attempt
ended ok or not in each, builds the change set a ``merge_back`` step asks a
human about, and removes every workspace at run end unless a merge-back
that names its step is still pending (not yet applied or declined by a
human), so a resume can still merge it.

Workspace events are journalled (``kind: workspace``, ``call_key
workspace:<id>``, no phase, never replayed as an agent result) so a resumed
attempt finds the workspaces an earlier one kept.

Stdlib only: the plan package's import fence (ADR-092 D-2).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import workspace as ws_mod
from .journal import PlanJournal
from .workspace import Change, Workspace, WorkspaceError

MAX_MERGE_PROMPT_DIFF_CHARS = 2400
MAX_PROBLEMS_NAMED = 8


@dataclass
class MergeEntry:
    """One workspace in a merge batch: its changes and why any may not apply."""

    workspace: Workspace
    changes: list[Change]
    problems: list[str]
    diff_path: str = ""
    diff: str = ""


@dataclass
class MergeBatch:
    """What one ``merge_back`` step would apply, and what it skipped."""

    entries: list[MergeEntry] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return sum(len(e.changes) for e in self.entries)

    @property
    def digest(self) -> str:
        """The change set's identity: what the human approves is exactly
        this set of per-file transitions; a resume asks again if it differs."""
        rows = [
            [e.workspace.id, c.rel, c.kind, c.base, c.new] for e in self.entries for c in e.changes
        ]
        return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()

    def files(self) -> list[str]:
        return sorted({c.rel for e in self.entries for c in e.changes})

    def deletions(self) -> list[str]:
        """Exactly the project-root files this batch would unlink."""
        return sorted({c.rel for e in self.entries for c in e.changes if c.kind == "delete"})


class RunWorkspaces:
    """The workspaces of one plan run directory."""

    def __init__(self, run_dir: Path, journal: PlanJournal, project_root: str | None) -> None:
        self._dir = run_dir
        self._journal = journal
        self.project_root = project_root or None
        self._known: dict[str, Workspace] = {}
        self._calls: dict[str, dict[str, Any]] = {}

    # ── the registry ────────────────────────────────────────────────────────

    def load(self) -> None:
        """Workspaces an earlier attempt kept (their directory still there)."""
        latest: dict[str, dict[str, Any]] = {}
        for record in self._journal.records():
            if record.get("kind") == "workspace" and isinstance(record.get("workspace"), dict):
                latest[str(record.get("id"))] = record
        for ws_id, record in latest.items():
            ws = Workspace.from_record(record["workspace"])
            if ws is not None and record.get("status") != "removed" and Path(ws.root).is_dir():
                self._known[ws_id] = ws

    def get(self, ws_id: str) -> Workspace | None:
        ws = self._known.get(ws_id)
        return ws if ws is not None and Path(ws.root).is_dir() else None

    def create(self, ws_id: str, mode: str, *, run_id: str, step: str, node: str) -> Workspace:
        """Make a workspace (blocking: run it on a worker thread). It is
        registered at once, so the run's cleanup removes it even when the
        agent that asked for it was cancelled meanwhile."""
        ws = ws_mod.create(
            ws_id, mode, self.project_root, self._dir, run_id=run_id, step=step, node=node
        )
        self._known[ws_id] = ws
        return ws

    def created(self, ws: Workspace, attempt: str) -> None:
        """Journal a new workspace (on the run's own thread, as every record)."""
        self._record(ws, "created", attempt)

    def mark(self, ws_id: str, step: str, ok: bool) -> None:
        """One agent call of this attempt in ``ws_id`` ended ``ok`` or not."""
        state = self._calls.setdefault(ws_id, {"step": step, "ok": 0, "failed": 0})
        state["ok" if ok else "failed"] += 1

    def all(self) -> list[Workspace]:
        return [self._known[k] for k in sorted(self._known)]

    # ── merge-back ──────────────────────────────────────────────────────────

    def batch(self, merge_step: str, sources: Iterable[str]) -> MergeBatch:
        """The change set of every workspace of ``sources`` whose agents all
        ended ok in this attempt (replayed or live). A workspace whose agents
        failed or never ran is skipped by name; one whose agents ran but
        whose directory is gone is a problem (its work cannot be merged)."""
        wanted = set(sources)
        batch = MergeBatch()
        ids = sorted(
            {k for k, ws in self._known.items() if ws.step in wanted}
            | {k for k, state in self._calls.items() if state["step"] in wanted}
        )
        for ws_id in ids:
            self._add(batch, merge_step, ws_id)
        batch.problems += _overlaps(batch.entries)
        return batch

    def _add(self, batch: MergeBatch, merge_step: str, ws_id: str) -> None:
        state = self._calls.get(ws_id) or {"ok": 0, "failed": 0}
        if state["failed"] or not state["ok"]:
            why = "agent_failed" if state["failed"] else "agent_not_run"
            batch.skipped.append({"workspace": ws_id, "why": why})
            return
        ws = self.get(ws_id)
        if ws is None:
            batch.problems.append(f"workspace_missing:{ws_id}")
            return
        try:
            found = ws_mod.changes(ws, self._dir)
        except WorkspaceError as exc:
            batch.problems.append(f"{exc.code}:{ws_id}")
            return
        if not found:
            return
        problems = ws_mod.merge_problems(ws, found)
        entry = MergeEntry(ws, found, problems)
        entry.diff = ws_mod.render_diff(ws, found)
        path = ws_mod.diff_path(self._dir, merge_step, ws_id)
        ws_mod.write_text(path, entry.diff)
        entry.diff_path = str(path)
        batch.entries.append(entry)
        batch.problems += [f"{p}@{ws_id}" for p in problems]

    def recheck(self, batch: MergeBatch) -> list[str]:
        """Problems now (the project root may have moved while a human read
        the card); changes are recomputed and must still be the approved set."""
        problems: list[str] = []
        for entry in batch.entries:
            try:
                now = ws_mod.changes(entry.workspace, self._dir)
            except WorkspaceError as exc:
                problems.append(f"{exc.code}:{entry.workspace.id}")
                continue
            if now != entry.changes:
                problems.append(f"changed_since_approval:{entry.workspace.id}")
                continue
            problems += [
                f"{p}@{entry.workspace.id}" for p in ws_mod.merge_problems(entry.workspace, now)
            ]
        return problems + _overlaps(batch.entries)

    @staticmethod
    def apply(batch: MergeBatch) -> list[str]:
        written: list[str] = []
        for entry in batch.entries:
            written += ws_mod.apply(entry.workspace, entry.changes)
        return sorted(set(written))

    # ── cleanup ─────────────────────────────────────────────────────────────

    def cleanup(self, pending_steps: set[str], attempt: str) -> dict[str, Any]:
        """Remove every workspace but those a pending merge-back still needs."""
        report: dict[str, Any] = {}
        for ws in self.all():
            row: dict[str, Any] = {"mode": ws.mode, "step": ws.step, "path": ws.path}
            if ws.step in pending_steps and Path(ws.path).exists():
                row.update(state="kept", why="merge_back pending")
                self._record(ws, "kept", attempt)
            else:
                error = ws_mod.remove(ws, self._dir)
                row.update(state="removed", error=error)
                self._record(ws, "removed", attempt, error=error)
                self._known.pop(ws.id, None)
            if ws.branch:
                row["branch"] = ws.branch
            report[ws.id] = row
        return report

    def removed(self, ws: Workspace, attempt: str, error: str | None = None) -> None:
        """Journal a workspace removed outside a run (``r4 team plan clean``)."""
        self._known.pop(ws.id, None)
        self._record(ws, "removed", attempt, error=error)

    def _record(self, ws: Workspace, status: str, attempt: str, error: str | None = None) -> None:
        record: dict[str, Any] = {
            "call_key": f"workspace:{ws.id}",
            "kind": "workspace",
            "id": ws.id,
            "status": status,
            "step": ws.step,
            "phase": None,  # never invalidated by --from-phase: the directory outlives it
            "ok": False,  # never replayed as an agent result
            "attempt": attempt,
            "workspace": ws.record(),
        }
        if error:
            record["error"] = error
        self._journal.append(record)


def _overlaps(entries: list[MergeEntry]) -> list[str]:
    """A file two workspaces changed to different content: refused, not merged."""
    seen: dict[str, set[str | None]] = {}
    for entry in entries:
        for change in entry.changes:
            seen.setdefault(change.rel, set()).add(change.new)
    return [f"overlap:{rel}" for rel, news in sorted(seen.items()) if len(news) > 1]


def merge_prompt(root: str, batch: MergeBatch) -> str:
    """What the human approves: the files, per workspace, and the diff."""
    lines = [
        f"Merge back into {root}: {batch.changed} file change(s) from "
        f"{len(batch.entries)} isolated workspace(s). Approving writes exactly these "
        "text changes to the project root; nothing is executed. A no stops the run.",
    ]
    for entry in batch.entries:
        counts = {kind: sum(c.kind == kind for c in entry.changes) for kind in ("add", "modify")}
        deleted = sum(c.kind == "delete" for c in entry.changes)
        lines.append(
            f"  {entry.workspace.id} ({entry.workspace.mode}, step {entry.workspace.step}): "
            f"+{counts['add']} ~{counts['modify']} -{deleted}; diff {entry.diff_path}"
        )
    for row in batch.skipped:
        lines.append(f"  skipped {row['workspace']}: {row['why']} (not merged)")
    lines.append("Files: " + ", ".join(batch.files()))
    diff = "".join(entry.diff for entry in batch.entries)
    if len(diff) > MAX_MERGE_PROMPT_DIFF_CHARS:
        diff = diff[:MAX_MERGE_PROMPT_DIFF_CHARS] + "\n… (the full diffs are at the paths above)"
    return "\n".join(lines) + "\n" + diff
