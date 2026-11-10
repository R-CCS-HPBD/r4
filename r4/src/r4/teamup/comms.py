"""
Teamup Agent Communication (A-803, A-804)

File-based communication between agents following VibeCodeHPC pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChangeLogEntry:
    """A single entry in the ChangeLog."""

    timestamp: datetime
    agent: str
    action: str
    description: str
    details: Optional[dict[str, Any]] = None

    def format(self) -> str:
        """Format as markdown entry."""
        time_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"### [{time_str}] {self.agent}: {self.action}\n\n{self.description}"
        if self.details:
            entry += (
                "\n\n<details>\n<summary>Details</summary>\n\n```json\n"
                f"{json.dumps(self.details, indent=2)}\n```\n</details>"
            )
        return entry + "\n\n---\n"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "agent": self.agent,
            "action": self.action,
            "description": self.description,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChangeLogEntry":
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            agent=data["agent"],
            action=data["action"],
            description=data["description"],
            details=data.get("details"),
        )


class AgentCommunication:
    """
    File-based agent communication (A-803).

    Implements the ChangeLog.md pattern for multi-agent communication.
    """

    def __init__(self, shared_dir: Path):
        """
        Initialize agent communication.

        Args:
            shared_dir: Path to agent-shared directory
        """
        self.shared_dir = Path(shared_dir)
        self.changelog_path = self.shared_dir / "ChangeLog.md"
        self.entries_path = self.shared_dir / "changelog_entries.json"
        self._lock = asyncio.Lock()
        # Guards the sync JSON read-modify-write below — parallel role turns
        # (parallel_roles > 1, 2026-09-29) append concurrently; without this
        # the changelog_entries.json update loses entries.
        self._sync_lock = threading.Lock()

    def init_changelog(self) -> None:
        """Initialize the changelog file."""
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        header = f"""# ChangeLog

Multi-agent communication log for teamup session.

Created: {datetime.now().isoformat()}

---

"""
        self.changelog_path.write_text(header)

        # Initialize entries file
        with open(self.entries_path, "w") as f:
            json.dump([], f)

    async def add_entry(
        self,
        agent: str,
        action: str,
        description: str,
        details: Optional[dict[str, Any]] = None,
    ) -> ChangeLogEntry:
        """
        Add an entry to the changelog.

        Args:
            agent: Agent identifier (PM, SE, PG, CD, SYSTEM)
            action: Action type (e.g., OPTIMIZE, MEASURE, REPORT)
            description: Human-readable description
            details: Optional structured details

        Returns:
            The created entry
        """
        entry = ChangeLogEntry(
            timestamp=datetime.now(),
            agent=agent.upper(),
            action=action.upper(),
            description=description,
            details=details,
        )

        async with self._lock:
            # Append to markdown file
            with open(self.changelog_path, "a") as f:
                f.write(entry.format())

            # Append to JSON entries
            entries = self._load_entries()
            entries.append(entry.to_dict())
            self._save_entries(entries)

        logger.debug(f"Changelog entry: {agent}:{action}")
        return entry

    def add_entry_sync(
        self,
        agent: str,
        action: str,
        description: str,
        details: Optional[dict[str, Any]] = None,
    ) -> ChangeLogEntry:
        """
        Add an entry to the changelog (synchronous version for CLI).

        Args:
            agent: Agent identifier (PM, SE, PG, CD, SYSTEM)
            action: Action type (e.g., OPTIMIZE, MEASURE, REPORT)
            description: Human-readable description
            details: Optional structured details

        Returns:
            The created entry
        """
        entry = ChangeLogEntry(
            timestamp=datetime.now(),
            agent=agent.upper(),
            action=action.upper(),
            description=description,
            details=details,
        )

        with self._sync_lock:
            # Append to markdown file
            with open(self.changelog_path, "a") as f:
                f.write(entry.format())

            # Append to JSON entries
            entries = self._load_entries()
            entries.append(entry.to_dict())
            self._save_entries(entries)

        logger.debug(f"Changelog entry (sync): {agent}:{action}")
        return entry

    def get_recent_entries(
        self,
        agent: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """
        Get recent changelog entries as markdown.

        Args:
            agent: Filter by agent (optional)
            action: Filter by action type (optional)
            limit: Maximum entries to return

        Returns:
            Markdown formatted entries
        """
        entries = self._load_entries()

        # Apply filters
        if agent:
            entries = [e for e in entries if e["agent"].upper() == agent.upper()]
        if action:
            entries = [e for e in entries if e["action"].upper() == action.upper()]

        # Get most recent
        entries = entries[-limit:]

        # Format as markdown
        result = []
        for data in entries:
            entry = ChangeLogEntry.from_dict(data)
            result.append(entry.format())

        return "\n".join(result) if result else "_No entries_"

    def get_entries_since(self, since: datetime) -> list[ChangeLogEntry]:
        """Get all entries since a given time."""
        entries = self._load_entries()
        result = []
        for data in entries:
            entry = ChangeLogEntry.from_dict(data)
            if entry.timestamp >= since:
                result.append(entry)
        return result

    def get_agent_entries(self, agent: str) -> list[ChangeLogEntry]:
        """Get all entries from a specific agent."""
        entries = self._load_entries()
        result = []
        for data in entries:
            if data["agent"].upper() == agent.upper():
                result.append(ChangeLogEntry.from_dict(data))
        return result

    def _load_entries(self) -> list[dict[str, Any]]:
        """Load entries from JSON file."""
        if not self.entries_path.exists():
            return []
        try:
            with open(self.entries_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _save_entries(self, entries: list[dict[str, Any]]) -> None:
        """Save entries to JSON file."""
        with open(self.entries_path, "w") as f:
            json.dump(entries, f, indent=2)


# AgentMailbox (A-804 direct messages) was deleted 2026-11-08 (ADR-114 D-8):
# nothing ever called it. Roles share the ChangeLog; sessions message each
# other through r4.companion.session_bus.


class ConflictDetector:
    """
    Detect and resolve conflicts between agent proposals (A-804).

    When multiple agents propose conflicting changes, this class helps
    identify conflicts and coordinate resolution.
    """

    @dataclass
    class Proposal:
        """An agent's proposal for a change."""

        agent: str
        target: str  # What's being changed
        change_type: str
        description: str
        priority: int = 0
        timestamp: datetime = field(default_factory=datetime.now)

    def __init__(self):
        """Initialize conflict detector."""
        self.pending_proposals: list[ConflictDetector.Proposal] = []

    def submit_proposal(
        self,
        agent: str,
        target: str,
        change_type: str,
        description: str,
        priority: int = 0,
    ) -> None:
        """Submit a proposal for a change."""
        proposal = self.Proposal(
            agent=agent,
            target=target,
            change_type=change_type,
            description=description,
            priority=priority,
        )
        self.pending_proposals.append(proposal)

    def detect_conflicts(self) -> list[tuple[Proposal, Proposal]]:
        """
        Detect conflicts between pending proposals.

        Returns:
            List of conflicting proposal pairs
        """
        conflicts = []

        for i, p1 in enumerate(self.pending_proposals):
            for p2 in self.pending_proposals[i + 1 :]:
                if self._conflicts(p1, p2):
                    conflicts.append((p1, p2))

        return conflicts

    def _conflicts(self, p1: Proposal, p2: Proposal) -> bool:
        """Check if two proposals conflict."""
        # Same target = potential conflict
        if p1.target == p2.target:
            return True

        # Check for overlapping code regions (same file + line range overlap)
        if hasattr(p1, "file") and hasattr(p2, "file"):
            if p1.file == p2.file:  # noqa: F821 — dynamically checked
                return True

        # Check for incompatible optimization strategies
        if p1.change_type != p2.change_type:
            incompatible_pairs = {
                ("optimize", "revert"),
                ("parallelize", "serialize"),
                ("gpu_offload", "cpu_only"),
            }
            pair = (p1.change_type, p2.change_type)
            if pair in incompatible_pairs or tuple(reversed(pair)) in incompatible_pairs:
                return True

        return False

    def resolve_by_priority(self, p1: Proposal, p2: Proposal) -> tuple[Proposal, Proposal]:
        """
        Resolve conflict by priority.

        Returns:
            Tuple of (winner, loser)
        """
        if p1.priority >= p2.priority:
            return (p1, p2)
        return (p2, p1)

    def clear_proposals(self) -> None:
        """Clear all pending proposals."""
        self.pending_proposals.clear()
