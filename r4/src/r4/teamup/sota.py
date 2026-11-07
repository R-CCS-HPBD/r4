"""
Teamup SOTA Tracking (A-806)

4-level State-of-the-Art tracking for optimization:
- Local: Agent's personal best
- Family: Best within technique family
- Hardware: Best for target hardware
- Project: Overall project best
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SOTALevel(Enum):
    """SOTA tracking level."""

    LOCAL = "local"
    FAMILY = "family"
    HARDWARE = "hardware"
    PROJECT = "project"


class MetricType(Enum):
    """Type of performance metric."""

    GFLOPS = "gflops"
    THROUGHPUT = "throughput"
    LATENCY = "latency"
    EFFICIENCY = "efficiency"
    SPEEDUP = "speedup"
    MEMORY_BW = "memory_bw"
    CUSTOM = "custom"


@dataclass
class SOTAEntry:
    """A single SOTA entry."""

    value: float
    metric_type: MetricType
    timestamp: datetime = field(default_factory=datetime.now)
    agent: Optional[str] = None
    technique: Optional[str] = None
    hardware_target: Optional[str] = None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    code_ref: Optional[str] = None  # Reference to the code/commit

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "value": self.value,
            "metric_type": self.metric_type.value,
            "timestamp": self.timestamp.isoformat(),
            "agent": self.agent,
            "technique": self.technique,
            "hardware_target": self.hardware_target,
            "description": self.description,
            "metadata": self.metadata,
            "code_ref": self.code_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SOTAEntry":
        """Create from dictionary."""
        return cls(
            value=data["value"],
            metric_type=MetricType(data["metric_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            agent=data.get("agent"),
            technique=data.get("technique"),
            hardware_target=data.get("hardware_target"),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
            code_ref=data.get("code_ref"),
        )

    def format(self) -> str:
        """Format as human-readable string."""
        agent_str = f" by {self.agent}" if self.agent else ""
        technique_str = f" ({self.technique})" if self.technique else ""
        return f"{self.value:.4f} {self.metric_type.value}{agent_str}{technique_str}"


class SOTATracker:
    """
    4-level SOTA tracker (A-806).

    Tracks State-of-the-Art performance at multiple levels:
    - Local: Each agent's personal best
    - Family: Best within each technique family
    - Hardware: Best for each hardware target
    - Project: Overall project best
    """

    def __init__(self, shared_dir: Path, metric_type: MetricType = MetricType.GFLOPS):
        """
        Initialize SOTA tracker.

        Args:
            shared_dir: Path to agent-shared directory
            metric_type: Primary metric to track
        """
        self.shared_dir = Path(shared_dir)
        self.metric_type = metric_type
        self.history: list[SOTAEntry] = []

        # SOTA at each level
        self._local_best: dict[str, SOTAEntry] = {}  # agent -> entry
        self._family_best: dict[str, SOTAEntry] = {}  # family -> entry
        self._hardware_best: dict[str, SOTAEntry] = {}  # hardware -> entry
        self._project_best: Optional[SOTAEntry] = None

        # Load existing state
        self._load_state()

    def _get_sota_file(self, level: SOTALevel) -> Path:
        """Get path to SOTA file for a level."""
        return self.shared_dir / f"sota_{level.value}.json"

    def _load_state(self) -> None:
        """Load existing SOTA state from files."""
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        # Load history
        history_file = self.shared_dir / "sota_history.json"
        if history_file.exists():
            try:
                with open(history_file) as f:
                    data = json.load(f)
                self.history = [SOTAEntry.from_dict(e) for e in data]
            except (json.JSONDecodeError, IOError):
                pass

        # Load level-specific bests
        for level in SOTALevel:
            sota_file = self._get_sota_file(level)
            if sota_file.exists():
                try:
                    with open(sota_file) as f:
                        data = json.load(f)
                    if level == SOTALevel.PROJECT:
                        self._project_best = SOTAEntry.from_dict(data)
                    elif level == SOTALevel.LOCAL:
                        self._local_best = {k: SOTAEntry.from_dict(v) for k, v in data.items()}
                    elif level == SOTALevel.FAMILY:
                        self._family_best = {k: SOTAEntry.from_dict(v) for k, v in data.items()}
                    elif level == SOTALevel.HARDWARE:
                        self._hardware_best = {k: SOTAEntry.from_dict(v) for k, v in data.items()}
                except (json.JSONDecodeError, IOError):
                    pass

    def _save_state(self) -> None:
        """Save current SOTA state to files."""
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        # Save history
        history_file = self.shared_dir / "sota_history.json"
        with open(history_file, "w") as f:
            json.dump([e.to_dict() for e in self.history], f, indent=2)

        # Save level-specific bests
        for level in SOTALevel:
            sota_file = self._get_sota_file(level)
            if level == SOTALevel.PROJECT and self._project_best:
                with open(sota_file, "w") as f:
                    json.dump(self._project_best.to_dict(), f, indent=2)
            elif level == SOTALevel.LOCAL:
                with open(sota_file, "w") as f:
                    json.dump({k: v.to_dict() for k, v in self._local_best.items()}, f, indent=2)
            elif level == SOTALevel.FAMILY:
                with open(sota_file, "w") as f:
                    json.dump({k: v.to_dict() for k, v in self._family_best.items()}, f, indent=2)
            elif level == SOTALevel.HARDWARE:
                with open(sota_file, "w") as f:
                    json.dump({k: v.to_dict() for k, v in self._hardware_best.items()}, f, indent=2)

    def record(
        self,
        value: float,
        agent: str,
        technique: Optional[str] = None,
        technique_family: Optional[str] = None,
        hardware_target: Optional[str] = None,
        description: str = "",
        metadata: Optional[dict[str, Any]] = None,
        code_ref: Optional[str] = None,
    ) -> dict[str, bool]:
        """
        Record a new performance measurement.

        Args:
            value: Performance value
            agent: Agent that achieved this
            technique: Specific technique used
            technique_family: Family of technique (vectorization, memory, etc.)
            hardware_target: Target hardware ID
            description: Description of the optimization
            metadata: Additional metadata
            code_ref: Reference to the code

        Returns:
            Dict of which SOTA levels were updated
        """
        entry = SOTAEntry(
            value=value,
            metric_type=self.metric_type,
            agent=agent,
            technique=technique,
            hardware_target=hardware_target,
            description=description,
            metadata=metadata or {},
            code_ref=code_ref,
        )

        self.history.append(entry)
        updates: dict[str, bool] = {
            "local": False,
            "family": False,
            "hardware": False,
            "project": False,
        }

        # Check local (agent) best
        if agent not in self._local_best or value > self._local_best[agent].value:
            self._local_best[agent] = entry
            updates["local"] = True
            logger.info(f"New local best for {agent}: {entry.format()}")

        # Check family best
        if technique_family:
            if (
                technique_family not in self._family_best
                or value > self._family_best[technique_family].value
            ):
                self._family_best[technique_family] = entry
                updates["family"] = True
                logger.info(f"New family best for {technique_family}: {entry.format()}")

        # Check hardware best
        if hardware_target:
            if (
                hardware_target not in self._hardware_best
                or value > self._hardware_best[hardware_target].value
            ):
                self._hardware_best[hardware_target] = entry
                updates["hardware"] = True
                logger.info(f"New hardware best for {hardware_target}: {entry.format()}")

        # Check project best
        if self._project_best is None or value > self._project_best.value:
            self._project_best = entry
            updates["project"] = True
            logger.info(f"New project best: {entry.format()}")

        self._save_state()
        return updates

    def get_local_best(self, agent: str) -> Optional[SOTAEntry]:
        """Get agent's local best."""
        return self._local_best.get(agent)

    def get_family_best(self, family: str) -> Optional[SOTAEntry]:
        """Get family best."""
        return self._family_best.get(family)

    def get_hardware_best(self, hardware: str) -> Optional[SOTAEntry]:
        """Get hardware best."""
        return self._hardware_best.get(hardware)

    def get_project_best(self) -> Optional[SOTAEntry]:
        """Get overall project best."""
        return self._project_best

    def get_status_report(self) -> str:
        """
        Get status report for all SOTA levels.

        Returns:
            Markdown formatted status report
        """
        lines = ["## SOTA Status\n"]

        # Project best
        if self._project_best:
            lines.append(f"**Project Best**: {self._project_best.format()}")
        else:
            lines.append("**Project Best**: _No measurements_")

        lines.append("")

        # Hardware bests
        if self._hardware_best:
            lines.append("### Hardware Targets")
            for hw, entry in sorted(self._hardware_best.items()):
                lines.append(f"- **{hw}**: {entry.format()}")
        else:
            lines.append("### Hardware Targets\n_No hardware-specific measurements_")

        lines.append("")

        # Family bests
        if self._family_best:
            lines.append("### Technique Families")
            for family, entry in sorted(self._family_best.items()):
                lines.append(f"- **{family}**: {entry.format()}")
        else:
            lines.append("### Technique Families\n_No family-specific measurements_")

        lines.append("")

        # Agent bests
        if self._local_best:
            lines.append("### Agent Bests")
            for agent, entry in sorted(self._local_best.items()):
                lines.append(f"- **{agent}**: {entry.format()}")
        else:
            lines.append("### Agent Bests\n_No agent-specific measurements_")

        return "\n".join(lines)

    def get_history_report(self, limit: int = 20) -> str:
        """
        Get history report of recent measurements.

        Args:
            limit: Maximum entries to return

        Returns:
            Markdown formatted history
        """
        if not self.history:
            return "_No measurements recorded_"

        lines = ["| Time | Value | Agent | Technique |", "|------|-------|-------|-----------|"]

        for entry in self.history[-limit:]:
            time_str = entry.timestamp.strftime("%H:%M:%S")
            technique = entry.technique or "-"
            agent = entry.agent or "-"
            lines.append(f"| {time_str} | {entry.value:.4f} | {agent} | {technique} |")

        return "\n".join(lines)

    def get_improvement_summary(self, baseline: Optional[float] = None) -> dict[str, Any]:
        """
        Get improvement summary.

        Args:
            baseline: Optional baseline value to compare against

        Returns:
            Summary dict with improvement metrics
        """
        if not self.history:
            return {"has_data": False}

        first = self.history[0].value
        last = self.history[-1].value
        best = self._project_best.value if self._project_best else last

        result = {
            "has_data": True,
            "first_value": first,
            "last_value": last,
            "best_value": best,
            "measurements": len(self.history),
            "improvement_from_first": ((best - first) / first * 100) if first > 0 else 0,
        }

        if baseline:
            result["baseline"] = baseline
            result["improvement_from_baseline"] = (
                ((best - baseline) / baseline * 100) if baseline > 0 else 0
            )

        return result
