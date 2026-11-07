"""Token cost estimation for the R4 platform.

Provides per-turn cost calculations by combining token counts from
:class:`UsageRecord` with cost-per-1k data from the provider layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class TurnCost:
    """Cost breakdown for a single LLM turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    def __str__(self) -> str:
        if self.cost_usd > 0:
            return f"{self.total_tokens:,} tokens (${self.cost_usd:.6f})"
        return f"{self.total_tokens:,} tokens (free)"


@dataclass
class SessionCost:
    """Cumulative cost for an entire session."""

    turns: List[TurnCost] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(t.total_tokens for t in self.turns)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(t.prompt_tokens for t in self.turns)

    @property
    def total_completion_tokens(self) -> int:
        return sum(t.completion_tokens for t in self.turns)

    @property
    def total_cost_usd(self) -> float:
        return sum(t.cost_usd for t in self.turns)

    def record(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost_per_1k: float = 0.0,
        model: str = "",
    ) -> TurnCost:
        """Record a turn and return the cost."""
        total = prompt_tokens + completion_tokens
        cost = (total / 1000.0) * cost_per_1k
        turn = TurnCost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_usd=cost,
            model=model,
        )
        self.turns.append(turn)
        return turn

    def summary(self) -> str:
        """Human-readable session cost summary."""
        lines = [f"Session: {len(self.turns)} turns, {self.total_tokens:,} tokens"]
        if self.total_cost_usd > 0:
            lines[0] += f", ${self.total_cost_usd:.4f}"
        else:
            lines[0] += " (free)"
        return lines[0]
