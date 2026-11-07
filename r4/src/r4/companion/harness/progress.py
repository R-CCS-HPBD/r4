"""ProgressTracker — cross-session continuity and milestone tracking.

At session end, auto-generates structured progress notes.
At session start, orients the researcher: "Last time we were working on X…"

The researcher experiences seamless continuity, even weeks later.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROGRESS_DIR = Path(
    os.environ.get("R4_COMPANION_PROGRESS_DIR")
    or os.environ.get("R4_COMPANION_PROGRESS_DIR")
    or os.path.expanduser("~/.config/r4/companion/progress")
)


@dataclass
class Milestone:
    """A tracked step in a multi-session research task."""

    label: str
    status: str = "pending"  # pending | active | done
    notes: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ProgressState:
    """Persistent progress across sessions."""

    last_summary: str = ""
    milestones: list[Milestone] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)  # Key paths, job IDs, etc.
    turn_count: int = 0
    last_user_message: str = ""
    last_response: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class ProgressTracker:
    """Track progress within and across sessions."""

    def __init__(self, progress_dir: Path | None = None):
        self._dir = progress_dir or PROGRESS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _progress_file(self) -> Path:
        return self._dir / "current.json"

    def _load(self) -> ProgressState:
        pf = self._progress_file()
        if not pf.is_file():
            return ProgressState()
        try:
            data = json.loads(pf.read_text())
            milestones = [Milestone(**m) for m in data.get("milestones", [])]
            return ProgressState(
                last_summary=data.get("last_summary", ""),
                milestones=milestones,
                artifacts=data.get("artifacts", []),
                turn_count=data.get("turn_count", 0),
                last_user_message=data.get("last_user_message", ""),
                last_response=data.get("last_response", ""),
                updated_at=data.get("updated_at", ""),
            )
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning("Failed to load progress: %s", e)
            return ProgressState()

    def _save(self) -> None:
        data = {
            "last_summary": self._state.last_summary,
            "milestones": [
                {"label": m.label, "status": m.status, "notes": m.notes, "updated_at": m.updated_at}
                for m in self._state.milestones
            ],
            "artifacts": self._state.artifacts,
            "turn_count": self._state.turn_count,
            "last_user_message": self._state.last_user_message,
            "last_response": self._state.last_response,
            "updated_at": self._state.updated_at,
        }
        self._progress_file().write_text(json.dumps(data, indent=2))

    def record_turn(self, user_message: str, response: str) -> None:
        """Record a conversational turn."""
        self._state.turn_count += 1
        self._state.last_user_message = user_message[:500]
        self._state.last_response = response[:500]
        self._state.updated_at = datetime.now().isoformat()

        # Extract artifacts (job IDs, paths)
        self._extract_artifacts(response)
        self._save()

    def orientation(self) -> str | None:
        """Generate an orientation message for session start.

        Returns None if there's no prior progress to orient from.
        """
        if not self._state.last_summary and self._state.turn_count == 0:
            return None

        parts: list[str] = ["[Session Continuity]"]

        if self._state.last_summary:
            parts.append(f"Previous session summary:\n{self._state.last_summary}")

        if self._state.milestones:
            parts.append("\nProgress milestones:")
            for m in self._state.milestones:
                icon = "✓" if m.status == "done" else "○" if m.status == "pending" else "▶"
                parts.append(f"  {icon} {m.label}")
                if m.notes:
                    parts.append(f"    {m.notes}")

        if self._state.artifacts:
            parts.append(f"\nKey artifacts: {', '.join(self._state.artifacts[-10:])}")

        return "\n".join(parts)

    def add_milestone(self, label: str, status: str = "pending") -> None:
        self._state.milestones.append(Milestone(label=label, status=status))
        self._save()

    def update_milestone(self, label: str, status: str, notes: str = "") -> None:
        for m in self._state.milestones:
            if m.label == label:
                m.status = status
                m.notes = notes
                m.updated_at = datetime.now().isoformat()
                break
        self._save()

    def generate_summary(self, messages: list) -> dict[str, Any]:
        """Generate an end-of-session summary from the conversation.

        Returns a dict with the summary fields and saves to progress state.
        """
        # Build a compact representation of the conversation
        discussed: list[str] = []
        accomplished: list[str] = []

        for msg in messages:
            content = (
                msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            )
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            if not content:
                continue
            # Heuristic: user messages are "discussed"
            if role == "user":
                discussed.append(content[:100])
            # Heuristic: tool results with "Created" or "Submitted" are accomplishments
            if "created" in content.lower() or "submitted" in content.lower():
                for line in content.split("\n")[:3]:
                    if any(w in line.lower() for w in ("created", "submitted", "saved", "wrote")):
                        accomplished.append(line.strip()[:100])

        summary_text = ""
        if discussed:
            summary_text += "Discussed: " + "; ".join(discussed[-5:]) + "\n"
        if accomplished:
            summary_text += "Accomplished: " + "; ".join(accomplished[-5:]) + "\n"
        if self._state.milestones:
            pending = [m.label for m in self._state.milestones if m.status == "pending"]
            if pending:
                summary_text += "Remaining: " + "; ".join(pending) + "\n"

        self._state.last_summary = summary_text.strip()
        self._save()

        return {
            "summary": summary_text,
            "milestones": [{"label": m.label, "status": m.status} for m in self._state.milestones],
            "artifacts": self._state.artifacts,
            "total_turns": self._state.turn_count,
        }

    def _extract_artifacts(self, text: str) -> None:
        """Extract key artifacts (job IDs, file paths) from text."""
        import re

        # Slurm job IDs
        for match in re.finditer(r"Submitted batch job (\d+)", text):
            jid = f"slurm:{match.group(1)}"
            if jid not in self._state.artifacts:
                self._state.artifacts.append(jid)

        # Limit artifact list
        self._state.artifacts = self._state.artifacts[-50:]

    def reset(self) -> None:
        """Reset progress (for new research task)."""
        self._state = ProgressState()
        self._save()
