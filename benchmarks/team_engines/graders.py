"""Benchmark graders (ADR-020 §13).

A grader answers a boolean per run outcome plus a human-readable note.
LLM-judge rubric graders come later — they route through the ModelBroker,
never a provider SDK directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from r4_contracts.team_engine.results import TeamResult


@dataclass(frozen=True)
class Grade:
    name: str
    passed: bool
    note: str = ""


class Grader:
    """Base grader protocol."""

    name: str = "base"

    def grade(self, result: TeamResult, expected_outputs: tuple[str, ...]) -> Grade:
        raise NotImplementedError


class StatusGrader(Grader):
    """Run must succeed at all — the baseline success-rate signal."""

    name = "status_success"

    def grade(
        self,
        result: TeamResult,
        expected_outputs: tuple[str, ...],  # noqa: ARG002 — uniform interface
    ) -> Grade:
        ok = result.status == "success"
        return Grade(self.name, ok, "" if ok else f"status={result.status}")


class MetricsSanityGrader(Grader):
    """Result metrics must be present and non-degenerate."""

    name = "metrics_sanity"

    def grade(
        self,
        result: TeamResult,
        expected_outputs: tuple[str, ...],  # noqa: ARG002 — uniform interface
    ) -> Grade:
        metrics = result.metrics
        if metrics is None:
            return Grade(self.name, False, "no metrics")
        if metrics.llm_calls < 1:
            return Grade(self.name, False, "llm_calls == 0")
        if metrics.agent_steps < 1:
            return Grade(self.name, False, "agent_steps == 0")
        if result.error:
            return Grade(self.name, False, f"error: {result.error}")
        return Grade(self.name, True)


class ArtifactPresenceGrader(Grader):
    """All expected output artifacts exist, either in the result manifest
    or on disk under the run directory artifacts/ folder."""

    name = "artifact_presence"

    def grade(self, result: TeamResult, expected_outputs: tuple[str, ...]) -> Grade:
        if not expected_outputs:
            return Grade(self.name, True, "no expected outputs")
        produced = {a.name for a in result.artifacts}
        missing = [o for o in expected_outputs if o not in produced]
        if missing:
            return Grade(self.name, False, f"missing: {missing}")
        return Grade(self.name, True)


GRADER_REGISTRY: dict[str, type[Grader]] = {
    StatusGrader.name: StatusGrader,
    MetricsSanityGrader.name: MetricsSanityGrader,
    ArtifactPresenceGrader.name: ArtifactPresenceGrader,
}


def load_graders(names: list[str]) -> list[Grader]:
    """Instantiate graders; unknown names raise — fail closed, per bench
    reproducibility rules (never grade with an unspecified metric)."""
    graders: list[Grader] = []
    for name in names:
        cls = GRADER_REGISTRY.get(name)
        if cls is None:
            raise KeyError(f"unknown grader {name!r} (known: {sorted(GRADER_REGISTRY)})")
        graders.append(cls())
    return graders
