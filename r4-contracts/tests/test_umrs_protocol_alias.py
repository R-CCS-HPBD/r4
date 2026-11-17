"""U2.0b: the 16 moved modules are aliases of umrs_protocol (ADR-117 D-2).

Each ``r4_contracts/<m>.py`` in MOVED is a ``sys.modules`` shim, so
``r4_contracts.<m> is umrs_protocol.<m>``: private names, monkeypatched globals,
``__file__`` and ``hpc_gate.gate_sha256()`` all reach the one real module. Every
other module stays a real ``r4_contracts`` module. A new module must be
classified here (MOVED or STAYING), so nothing slips between the two packages.
U2.0 spec §2 (closure), §4.1 (shim), §7 (this test).
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import r4_contracts

# U2.0 spec §2: the closure that moved to umrs_protocol.
MOVED = (
    "a2l",
    "ask_policy",
    "context_pack",
    "errors",
    "handshake_hmac",
    "hpc_gate",
    "models",
    "protocol",
    "provenance",
    "recipes",
    "research",
    "retrieval",
    "security",
    "skills",
    "workflow_package",
    "workflows",
)

# Real r4_contracts modules: knowledge, the U3a D9 set and the never-used ones.
STAYING = (
    "agent_tool_playbook",
    "components",
    "dto",
    "extensibility",
    "knowledge",
    "loop_plan",
    "mcp",
    "scheduler",
    "spaces",
    "taint",
    "team_engine",
    "team_engine.capabilities",
    "team_engine.events",
    "team_engine.plan",
    "team_engine.plan_patterns",
    "team_engine.results",
    "team_engine.services",
    "team_engine.spec",
    "workflow_params",
    "workspace",
)

SHIM_DIR = Path(r4_contracts.__file__).resolve().parent
SHIM_MAX_BYTES = 1024

# r4_contracts/__init__.py is byte-unchanged by U2.0b: the same public surface.
ALL_SNAPSHOT = (
    "__version__",
    "AUTH_ERRORS",
    "CURVE_ERRORS",
    "ERROR_CODE_HEADER",
    "PRESIGN_ERRORS",
    "PROVIDER_ERRORS",
    "SESSION_ERRORS",
    "PlatformError",
    "known_error_codes",
    "ArtifactRef",
    "FindingRef",
    "IngestSourceSpec",
    "KnowledgeEvent",
    "KnowledgeSummary",
    "LintDiagnostic",
    "StepDraft",
    "VerificationReportRef",
    "WorkflowDraft",
    "Agent",
    "AgentStatus",
    "ComputeResource",
    "ComputeResourceStatus",
    "Conversation",
    "ConversationStatus",
    "Dataset",
    "DatasetStatus",
    "Experiment",
    "ExperimentStatus",
    "Hypothesis",
    "HypothesisStatus",
    "Knowledge",
    "KnowledgeStatus",
    "Notebook",
    "NotebookStatus",
    "PrimitiveKind",
    "ProvenanceGraph",
    "ProvenanceNode",
    "Publication",
    "PublicationStatus",
    "ResearchGoal",
    "ResearchGoalStatus",
    "ResearchProject",
    "ResearchProjectStatus",
    "Simulation",
    "SimulationStatus",
    "WorkflowStatus",
    "ArtifactRef",
    "FindingRef",
    "IngestSourceSpec",
    "KnowledgeEvent",
    "KnowledgeSummary",
    "LintDiagnostic",
    "StepDraft",
    "VerificationReportRef",
    "WorkflowDraft",
)


@pytest.mark.parametrize("module", MOVED)
def test_moved_module_is_the_umrs_protocol_module(module: str) -> None:
    alias = importlib.import_module(f"r4_contracts.{module}")
    real = importlib.import_module(f"umrs_protocol.{module}")
    assert alias is real
    assert getattr(r4_contracts, module) is real
    assert Path(alias.__file__).resolve().parent.name == "umrs_protocol"


@pytest.mark.parametrize("module", MOVED)
def test_moved_module_file_is_only_a_shim(module: str) -> None:
    raw = (SHIM_DIR / f"{module}.py").read_bytes()
    assert len(raw) < SHIM_MAX_BYTES
    assert b"sys.modules[__name__] = _impl" in raw
    assert f"from umrs_protocol import {module} as _impl".encode() in raw
    assert b"def " not in raw and b"class " not in raw


@pytest.mark.parametrize("module", STAYING)
def test_staying_module_is_a_real_r4_contracts_module(module: str) -> None:
    loaded = importlib.import_module(f"r4_contracts.{module}")
    assert loaded.__name__ == f"r4_contracts.{module}"
    assert Path(loaded.__file__).resolve().is_relative_to(SHIM_DIR)


def test_every_module_is_classified() -> None:
    found = {
        info.name.removeprefix("r4_contracts.")
        for info in pkgutil.walk_packages(r4_contracts.__path__, "r4_contracts.")
    }
    assert set(MOVED).isdisjoint(STAYING)
    assert found == set(MOVED) | set(STAYING)


def test_public_surface_is_unchanged() -> None:
    assert tuple(r4_contracts.__all__) == ALL_SNAPSHOT
    assert (len(ALL_SNAPSHOT), len(set(ALL_SNAPSHOT))) == (55, 46)
    for name in set(ALL_SNAPSHOT):
        assert hasattr(r4_contracts, name), name


def test_shared_types_are_one_object() -> None:
    from r4_contracts import hpc_gate as gate_alias
    from r4_contracts import research
    from umrs_protocol import errors, hpc_gate, provenance

    assert r4_contracts.PlatformError is errors.PlatformError
    assert gate_alias.GateError is hpc_gate.GateError
    assert research.ArtifactRef is provenance.ArtifactRef


def test_monkeypatching_through_the_alias_reaches_the_real_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from r4_contracts import hpc_gate as gate_alias
    from umrs_protocol import hpc_gate

    monkeypatch.setattr(gate_alias, "LOCK_WAIT", 0.25)
    assert hpc_gate.LOCK_WAIT == 0.25
