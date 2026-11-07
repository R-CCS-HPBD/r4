"""Science loop state tracker — the TUI read-model on the ResearchRuntime seam.

Tracks the current scientific loop iteration:
  hypothesis → experiment → reward → belief update → next hypothesis

Folded 2026-09-22 (gap row D-32, ROADMAP 2.1): this module lives with the
``r4.runtime`` package — the client-side loop seam (ResearchRuntime SPI,
ADR-design one-loop-one-engine). It is a thin SQLite read-model for the
TUI's science dashboard; it is NOT a loop driver and never executes
experiments. The loop itself runs server-side as ``research_loop_job``
(ADR-036) or locally through the SPI.

Formerly ``r4/companion/science_loop.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class Hypothesis:
    """A testable scientific hypothesis."""

    id: str = field(default_factory=lambda: str(uuid4()))
    statement: str = ""
    confidence: float = 0.5
    status: str = "proposed"  # proposed | testing | confirmed | falsified
    parameters: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "confidence": self.confidence,
            "status": self.status,
            "parameters": self.parameters,
            "created_at": self.created_at,
        }


@dataclass
class ExperimentState:
    """Current experiment status within the science loop."""

    id: str = field(default_factory=lambda: str(uuid4()))
    hypothesis_id: str = ""
    status: str = "pending"  # pending | running | completed | failed
    metrics: dict[str, float] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hypothesis_id": self.hypothesis_id,
            "status": self.status,
            "metrics": self.metrics,
            "artifacts": self.artifacts,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "created_at": self.created_at,
        }


@dataclass
class RewardTrajectory:
    """Reward trajectory across science loop iterations."""

    iterations: list[dict[str, Any]] = field(default_factory=list)
    cumulative_reward: float = 0.0
    total_experiments: int = 0
    total_gpu_hours: float = 0.0

    def add_iteration(
        self,
        *,
        hypothesis: str,
        experiment_id: str,
        performance: float = 0.0,
        novelty: float = 0.0,
        information_gain: float = 0.0,
        reproducibility: float = 0.0,
        cost: float = 0.0,
        total: float = 0.0,
    ) -> None:
        self.iterations.append(
            {
                "hypothesis": hypothesis,
                "experiment_id": experiment_id,
                "performance": performance,
                "novelty": novelty,
                "information_gain": information_gain,
                "reproducibility": reproducibility,
                "cost": cost,
                "total": total,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.cumulative_reward += total
        self.total_experiments += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations[-20:],  # Keep last 20 for TUI
            "cumulative_reward": self.cumulative_reward,
            "total_experiments": self.total_experiments,
            "total_gpu_hours": self.total_gpu_hours,
        }


@dataclass
class BeliefState:
    """Bayesian belief state over hypotheses."""

    hypotheses: dict[str, float] = field(default_factory=dict)  # hypothesis_id → probability
    entropy: float = 0.0
    converged: bool = False

    def update(self, hypothesis_id: str, probability: float) -> None:
        self.hypotheses[hypothesis_id] = probability
        # Recompute entropy
        total = sum(self.hypotheses.values())
        if total > 0:
            import math

            self.entropy = -sum(
                (p / total) * math.log2(p / total) for p in self.hypotheses.values() if p > 0
            )
        # Check convergence (low entropy)
        self.converged = self.entropy < 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypotheses": self.hypotheses,
            "entropy": self.entropy,
            "converged": self.converged,
        }


class ScienceLoop:
    """Science loop state tracker.

    Stores the current state of the autonomous science loop:
    - Current hypothesis
    - Current experiment status
    - Reward trajectory
    - Belief state

    All state is persisted to SQLite so it survives TUI restarts.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # ':memory:' keeps ONE connection for the object's lifetime —
        # sqlite3.connect(':memory:') per call is a fresh, table-free db.
        self._memory_conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS science_loop (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    experiment_id TEXT,
                    reward_total REAL,
                    performance REAL,
                    novelty REAL,
                    information_gain REAL,
                    reproducibility REAL,
                    cost REAL,
                    belief_entropy REAL,
                    timestamp TEXT NOT NULL
                )"""
            )
            conn.execute(
                (
                    "CREATE INDEX IF NOT EXISTS idx_science_session"
                    " ON science_loop(session_id, timestamp)"
                )
            )

    def _conn(self) -> sqlite3.Connection:
        # 2026-10-18 (H-4 contract find, same class as companion/knowledge_base):
        # row_factory was never set, so dict(row) in get_trajectory raised
        # ValueError as soon as a session held an iteration.
        if self._db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:")
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_iteration(
        self,
        *,
        session_id: str,
        hypothesis: str,
        experiment_id: str = "",
        reward_total: float = 0.0,
        performance: float = 0.0,
        novelty: float = 0.0,
        information_gain: float = 0.0,
        reproducibility: float = 0.0,
        cost: float = 0.0,
        belief_entropy: float = 0.0,
    ) -> str:
        """Record a science loop iteration."""
        iteration_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO science_loop
                   (id, session_id, hypothesis, experiment_id, reward_total,
                    performance, novelty, information_gain, reproducibility,
                    cost, belief_entropy, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    iteration_id,
                    session_id,
                    hypothesis,
                    experiment_id,
                    reward_total,
                    performance,
                    novelty,
                    information_gain,
                    reproducibility,
                    cost,
                    belief_entropy,
                    timestamp,
                ),
            )
        return iteration_id

    def get_trajectory(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get the reward trajectory for a session."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM science_loop WHERE session_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_cumulative_reward(self, session_id: str) -> float:
        """Get cumulative reward for a session."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT SUM(reward_total) FROM science_loop WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return float(row[0] or 0.0)

    def get_stats(self, session_id: str) -> dict[str, Any]:
        """Get science loop statistics for TUI display."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                    COUNT(*) as total,
                    SUM(reward_total) as cum_reward,
                    AVG(reward_total) as avg_reward,
                    SUM(performance) as total_perf,
                    SUM(novelty) as total_novelty,
                    SUM(information_gain) as total_ig,
                    AVG(belief_entropy) as avg_entropy
                   FROM science_loop WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
        if not row or row[0] == 0:
            return {
                "total_iterations": 0,
                "cumulative_reward": 0.0,
                "avg_reward": 0.0,
                "total_performance": 0.0,
                "total_novelty": 0.0,
                "total_information_gain": 0.0,
                "avg_entropy": 0.0,
            }
        return {
            "total_iterations": row[0],
            "cumulative_reward": row[1] or 0.0,
            "avg_reward": row[2] or 0.0,
            "total_performance": row[3] or 0.0,
            "total_novelty": row[4] or 0.0,
            "total_information_gain": row[5] or 0.0,
            "avg_entropy": row[6] or 0.0,
        }
