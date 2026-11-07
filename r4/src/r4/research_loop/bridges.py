"""Integration bridges between Research Loop and existing R4 subsystems.

Phase 1 scope:
- HypothesisBridge: converts between Research Loop HypothesisRecord and
  r4.agent.hypothesis.Hypothesis (the existing ResearchOrchestrator model).
- ExperimentBridge: converts experimental protocols to UMRS pipeline JSON.
  Phase 1 stub only — full implementation in Phase 2.
- EvidenceBridge: converts experiment results to structured evidence records.
  Phase 1 stub only — DA agent integration in Phase 3.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .state import EvidenceRecord, ExperimentRecord, HypothesisRecord

if TYPE_CHECKING:
    pass  # r4.agent imports are lazy to avoid circular deps

logger = logging.getLogger(__name__)


class HypothesisBridge:
    """Converts between Research Loop HypothesisRecord and r4.agent Hypothesis.

    The existing ResearchOrchestrator and HypothesisManager use
    r4.agent.hypothesis.Hypothesis. This bridge maps the Research Loop's
    HypothesisRecord to/from that model without coupling the loop state to
    the agent module.
    """

    @staticmethod
    def to_orchestrator(h: HypothesisRecord) -> Any:
        """Convert a HypothesisRecord to r4.agent.hypothesis.Hypothesis."""
        try:
            from r4.agent.hypothesis import Hypothesis as AgentHypothesis
            from r4.agent.hypothesis import HypothesisStatus as AgentStatus

            status_map = {
                "proposed": AgentStatus.PROPOSED,
                "testing": AgentStatus.TESTING,
                "supported": AgentStatus.SUPPORTED,
                "refuted": AgentStatus.REFUTED,
                "inconclusive": AgentStatus.INCONCLUSIVE,
                "retired": AgentStatus.RETIRED,
            }
            return AgentHypothesis(
                statement=h.statement,
                confidence=h.confidence,
                status=status_map.get(h.status.value, AgentStatus.PROPOSED),
                evidence_for=[],  # populated by orchestrator via EvidenceBridge
                evidence_against=[],
            )
        except ImportError:
            logger.debug("r4.agent.hypothesis not available — returning dict")
            return {
                "statement": h.statement,
                "confidence": h.confidence,
                "status": h.status.value,
            }

    @staticmethod
    def from_orchestrator(h: Any) -> HypothesisRecord:
        """Convert r4.agent.hypothesis.Hypothesis to a HypothesisRecord."""
        try:
            from r4.agent.hypothesis import Hypothesis as AgentHypothesis

            if isinstance(h, AgentHypothesis):
                status_map = {
                    "PROPOSED": "proposed",
                    "TESTING": "testing",
                    "SUPPORTED": "supported",
                    "REFUTED": "refuted",
                    "INCONCLUSIVE": "inconclusive",
                    "RETIRED": "retired",
                }
                return HypothesisRecord(
                    id="",
                    statement=h.statement,
                    status=status_map.get(h.status.name, "proposed"),
                    confidence=h.confidence,
                )
        except ImportError:
            pass

        # Fallback for dict or unknown types
        return HypothesisRecord(
            id="",
            statement=h.get("statement", "") if isinstance(h, dict) else str(h),
            confidence=h.get("confidence", 0.5) if isinstance(h, dict) else 0.5,
        )


class ExperimentBridge:
    """Converts experimental protocols to UMRS pipeline definitions.

    Phase 1: stub — returns a placeholder pipeline JSON.
    Phase 2: translates ED agent-designed protocols into valid UMRS pipeline
    definitions with step routing, resource specs, and DAG dependencies.
    """

    @staticmethod
    def protocol_to_pipeline(protocol: dict[str, Any]) -> dict[str, Any]:
        """Convert an experimental protocol to a UMRS pipeline definition.

        Protocol format (designed by ED agent):
        {
            "name": "GCMC_MOF5_CO2_uptake",
            "steps": [
                {"name": "gcmc_sim", "type": "slurm_job",
                 "script": "run_gcmc.sh", "resources": {"gpus": 4, "time": "2h"}},
                {"name": "analysis", "type": "python_exec",
                 "script": "analyze.py", "depends_on": ["gcmc_sim"]},
            ]
        }

        Returns: UMRS pipeline JSON compatible with r4 workflow upload.
        """
        if not protocol or not protocol.get("steps"):
            logger.warning("Empty protocol — returning placeholder pipeline")
            return _placeholder_pipeline(protocol.get("name", "unnamed"))

        steps = []
        for step in protocol.get("steps", []):
            step_spec = {
                "name": step.get("name", f"step_{len(steps)}"),
                "entrypoint": _entrypoint_for_step_type(step.get("type", "shell")),
                "depends_on": step.get("depends_on", []),
                "config": step,
                "max_retries": step.get("max_retries", 1),
            }
            if "resources" in step:
                step_spec["resources"] = {
                    "gpu_count": step["resources"].get("gpus", 0),
                    "cpu_limit": step["resources"].get("cpus", 1),
                    "timeout_seconds": _parse_timeout(step["resources"].get("time", "1h")),
                    "memory_limit": step["resources"].get("memory", ""),
                }
            steps.append(step_spec)

        return {
            "name": protocol.get("name", "research-experiment"),
            "version": "1.0",
            "description": protocol.get("description", "Research loop experiment"),
            "steps": steps,
        }

    @staticmethod
    def results_from_pipeline_output(output: dict[str, Any]) -> dict[str, Any]:
        """Extract structured results from UMRS pipeline output.

        Phase 1: stub — passes through raw output.
        Phase 2: parse pipeline outputs (S3 keys, embeddings, metrics) into
        ExperimentRecord.results format.
        """
        return {
            "raw_output": output,
            "parsed": False,  # Phase 2: True after DA agent parsing
            "summary": "Pipeline output (Phase 2 parsing pending)",
        }


class EvidenceBridge:
    """Converts experiment results to structured evidence.

    Phase 1: stub — creates EvidenceRecord with neutral strength.
    Phase 2: DA agent computes actual statistical tests, p-values, effect sizes.
    """

    @staticmethod
    def results_to_evidence(
        experiment: ExperimentRecord,
        hypothesis: HypothesisRecord,
        results: dict[str, Any],
    ) -> EvidenceRecord:
        """Convert raw experiment results to structured evidence.

        In Phase 2, the DA agent calls this after running statistical analysis
        on experiment outputs. Phase 1 returns neutral placeholder evidence.
        """
        return EvidenceRecord(
            id="",  # assigned by state.add_evidence()
            experiment_id=experiment.id,
            hypothesis_id=hypothesis.id,
            supports=True,  # Phase 2: actual determination from results
            strength=0.5,  # Phase 2: computed from statistical tests
            summary=f"Evidence from experiment {experiment.id} (Phase 2 DA analysis pending)",
        )

    @staticmethod
    def compute_evidence_strength(
        p_value: float | None = None,
        effect_size: float | None = None,
        sample_size: int = 1,
    ) -> float:
        """Compute evidence strength from statistical test results.

        Strength scale:
        - 0.0–0.2: Weak (high variance, small sample, marginal significance)
        - 0.2–0.5: Moderate
        - 0.5–0.8: Strong (p < 0.01, large effect size)
        - 0.8–1.0: Decisive (p < 0.001, very large effect size)

        Phase 1: heuristic. Phase 2: full statistical computation by DA agent.
        """
        strength = 0.5  # neutral baseline

        if p_value is not None:
            if p_value < 0.001:
                strength += 0.3
            elif p_value < 0.01:
                strength += 0.2
            elif p_value < 0.05:
                strength += 0.1
            elif p_value > 0.1:
                strength -= 0.2

        if effect_size is not None:
            if effect_size > 0.8:  # Cohen's d large
                strength += 0.2
            elif effect_size > 0.5:  # medium
                strength += 0.1
            elif effect_size < 0.2:  # negligible
                strength -= 0.2

        if sample_size > 100:
            strength += 0.1
        elif sample_size < 10:
            strength -= 0.1

        return max(0.0, min(1.0, strength))


# -- Helpers ---------------------------------------------------------------


def _entrypoint_for_step_type(step_type: str) -> str:
    """Map a logical step type to a UMRS pipeline entrypoint."""
    entrypoint_map = {
        "slurm_job": "research_steps.slurm:SlurmStep",
        "python_exec": "research_steps.python:PythonExecStep",
        "shell": "research_steps.shell:ShellStep",
        "ml_inference": "research_steps.ml:MLInferenceStep",
        "rag_query": "research_steps.rag:RAGQueryStep",
        "data_collection": "research_steps.data:DataCollectionStep",
        "analysis": "research_steps.analysis:AnalysisStep",
    }
    return entrypoint_map.get(step_type, f"research_steps.{step_type}:ResearchStep")


def _parse_timeout(time_str: str) -> int:
    """Parse a human-readable time string to seconds."""
    try:
        time_str = time_str.strip().lower()
        if time_str.endswith("h"):
            return int(float(time_str[:-1]) * 3600)
        elif time_str.endswith("m"):
            return int(float(time_str[:-1]) * 60)
        elif time_str.endswith("s"):
            return int(float(time_str[:-1]))
        return int(time_str)
    except (ValueError, TypeError):
        return 3600  # default 1h


def _placeholder_pipeline(name: str) -> dict[str, Any]:
    """Return a minimal placeholder UMRS pipeline definition."""
    return {
        "name": name or "placeholder-experiment",
        "version": "1.0",
        "description": "Placeholder pipeline — Phase 2 integration pending",
        "steps": [
            {
                "name": "placeholder_step",
                "entrypoint": "research_steps.shell:ShellStep",
                "depends_on": [],
                "config": {"command": "echo 'Phase 2 pipeline integration pending'"},
            }
        ],
    }
