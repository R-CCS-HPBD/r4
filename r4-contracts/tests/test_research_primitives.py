"""Tests for r4_contracts.research — research primitives.

Validates:
  - All 13 primitives are defined
  - Provenance is first-class (every primitive has a provenance field)
  - Status enums are defined per primitive
  - PrimitiveKind enum covers all primitives
  - Round-trip dict serialization works
"""

from __future__ import annotations

from r4_contracts.research import (
    Agent,
    AgentStatus,
    ComputeResource,
    ComputeResourceStatus,
    Conversation,
    ConversationStatus,
    Dataset,
    DatasetStatus,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    Knowledge,
    KnowledgeStatus,
    Notebook,
    NotebookStatus,
    PrimitiveKind,
    ProvenanceGraph,
    ProvenanceNode,
    Publication,
    PublicationStatus,
    ResearchGoal,
    ResearchGoalStatus,
    ResearchProject,
    ResearchProjectStatus,
    Simulation,
    SimulationStatus,
    WorkflowStatus,
)

# ---------------------------------------------------------------------------
# Provenance — first-class on every primitive
# ---------------------------------------------------------------------------


def test_provenance_node_creation() -> None:
    node = ProvenanceNode(
        primitive_id="exp-001",
        primitive_kind=PrimitiveKind.EXPERIMENT,
        relation="produced_by",
        label="Experiment 1",
    )
    assert node.primitive_id == "exp-001"
    assert node.primitive_kind == PrimitiveKind.EXPERIMENT
    assert node.relation == "produced_by"


def test_provenance_graph_add_node() -> None:
    graph = ProvenanceGraph()
    node = ProvenanceNode(
        primitive_id="ds-001",
        primitive_kind=PrimitiveKind.DATASET,
        relation="consumes",
    )
    graph.add(node)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].primitive_id == "ds-001"


def test_provenance_graph_to_dict() -> None:
    graph = ProvenanceGraph()
    graph.add(
        ProvenanceNode(
            primitive_id="h-001",
            primitive_kind=PrimitiveKind.HYPOTHESIS,
            relation="tests",
        )
    )
    d = graph.to_dict()
    assert "nodes" in d
    assert len(d["nodes"]) == 1
    assert d["nodes"][0]["primitive_id"] == "h-001"
    assert d["nodes"][0]["primitive_kind"] == "hypothesis"


def test_provenance_graph_from_dict() -> None:
    data = {
        "nodes": [
            {
                "primitive_id": "wf-001",
                "primitive_kind": "workflow",
                "relation": "executed_by",
                "label": "Main workflow",
            }
        ]
    }
    graph = ProvenanceGraph.from_dict(data)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].primitive_id == "wf-001"
    assert graph.nodes[0].primitive_kind == PrimitiveKind.WORKFLOW
    assert graph.nodes[0].label == "Main workflow"


def test_provenance_graph_from_dict_empty() -> None:
    graph = ProvenanceGraph.from_dict(None)
    assert graph.nodes == []


def test_provenance_graph_from_dict_invalid_kind() -> None:
    """Invalid primitive_kind should fall back to RESEARCH_PROJECT."""
    data = {
        "nodes": [
            {
                "primitive_id": "x-001",
                "primitive_kind": "not_a_real_kind",
                "relation": "unknown",
            }
        ]
    }
    graph = ProvenanceGraph.from_dict(data)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].primitive_kind == PrimitiveKind.RESEARCH_PROJECT


# ---------------------------------------------------------------------------
# ResearchProject — the root workspace
# ---------------------------------------------------------------------------


def test_research_project_creation() -> None:
    project = ResearchProject(
        id="proj-001",
        name="Catalyst Investigation",
        owner_id="user-001",
    )
    assert project.id == "proj-001"
    assert project.name == "Catalyst Investigation"
    assert project.status == ResearchProjectStatus.PLANNING
    assert project.goal_ids == []
    assert project.hypothesis_ids == []
    assert project.provenance.nodes == []


def test_research_project_status_values() -> None:
    assert ResearchProjectStatus.PLANNING == "planning"
    assert ResearchProjectStatus.ACTIVE == "active"
    assert ResearchProjectStatus.PAUSED == "paused"
    assert ResearchProjectStatus.COMPLETED == "completed"
    assert ResearchProjectStatus.ABANDONED == "abandoned"


# ---------------------------------------------------------------------------
# Hypothesis — testable claim with evidence
# ---------------------------------------------------------------------------


def test_hypothesis_creation() -> None:
    h = Hypothesis(
        id="h-001",
        project_id="proj-001",
        statement="Catalyst X reduces reaction time by 30%",
    )
    assert h.id == "h-001"
    assert h.status == HypothesisStatus.PROPOSED
    assert h.confidence == 0.0
    assert h.evidence_for == []
    assert h.evidence_against == []


def test_hypothesis_status_values() -> None:
    assert HypothesisStatus.PROPOSED == "proposed"
    assert HypothesisStatus.TESTING == "testing"
    assert HypothesisStatus.SUPPORTED == "supported"
    assert HypothesisStatus.REFUTED == "refuted"
    assert HypothesisStatus.INCONCLUSIVE == "inconclusive"


# ---------------------------------------------------------------------------
# Experiment — procedure to test hypothesis
# ---------------------------------------------------------------------------


def test_experiment_creation() -> None:
    exp = Experiment(
        id="exp-001",
        project_id="proj-001",
        hypothesis_id="h-001",
        protocol={"steps": ["prepare", "measure", "analyze"]},
    )
    assert exp.id == "exp-001"
    assert exp.status == ExperimentStatus.PENDING
    assert exp.protocol["steps"] == ["prepare", "measure", "analyze"]
    assert exp.workflow_ids == []
    assert exp.dataset_ids == []


def test_experiment_status_values() -> None:
    assert ExperimentStatus.PENDING == "pending"
    assert ExperimentStatus.RUNNING == "running"
    assert ExperimentStatus.COMPLETED == "completed"
    assert ExperimentStatus.FAILED == "failed"
    assert ExperimentStatus.CANCELLED == "cancelled"


# ---------------------------------------------------------------------------
# Knowledge — accumulated understanding (RAG index)
# ---------------------------------------------------------------------------


def test_knowledge_creation() -> None:
    k = Knowledge(
        id="k-001",
        project_id="proj-001",
        name="Catalyst literature",
        index_uri="s3://bucket/indexes/k-001/",
        document_count=42,
    )
    assert k.id == "k-001"
    assert k.status == KnowledgeStatus.EMPTY
    assert k.document_count == 42
    assert k.index_uri == "s3://bucket/indexes/k-001/"


def test_knowledge_status_values() -> None:
    assert KnowledgeStatus.EMPTY == "empty"
    assert KnowledgeStatus.INDEXING == "indexing"
    assert KnowledgeStatus.READY == "ready"
    assert KnowledgeStatus.STALE == "stale"
    assert KnowledgeStatus.ERROR == "error"


# ---------------------------------------------------------------------------
# Dataset — input data
# ---------------------------------------------------------------------------


def test_dataset_creation() -> None:
    ds = Dataset(
        id="ds-001",
        project_id="proj-001",
        name="Reaction measurements",
        s3_uri="s3://bucket/datasets/ds-001/",
        size_bytes=1024 * 1024,
    )
    assert ds.id == "ds-001"
    assert ds.status == DatasetStatus.REGISTERED
    assert ds.size_bytes == 1024 * 1024


def test_dataset_status_values() -> None:
    assert DatasetStatus.REGISTERED == "registered"
    assert DatasetStatus.UPLOADING == "uploading"
    assert DatasetStatus.READY == "ready"
    assert DatasetStatus.PROCESSING == "processing"
    assert DatasetStatus.ARCHIVED == "archived"


# ---------------------------------------------------------------------------
# Publication — output artifact
# ---------------------------------------------------------------------------


def test_publication_creation() -> None:
    pub = Publication(
        id="pub-001",
        project_id="proj-001",
        title="Investigation of Catalyst X",
        authors=["Alice", "Bob"],
    )
    assert pub.id == "pub-001"
    assert pub.status == PublicationStatus.DRAFT
    assert pub.authors == ["Alice", "Bob"]


def test_publication_catalog_ids_default_empty_and_stamped() -> None:
    """D-45 join: catalog_ids defaults empty; provenance round-trips CATALOG nodes."""
    pub = Publication(id="pub-001", project_id="proj-001", title="t")
    assert pub.catalog_ids == []
    pub.catalog_ids = ["scat-1", "scat-2"]
    prov = ProvenanceGraph.from_dict(pub.provenance.to_dict())
    node = ProvenanceNode(
        primitive_id="scat-1",
        primitive_kind=PrimitiveKind.CATALOG,
        relation="cataloged_in",
        label="scientific catalog",
    )
    prov.add(node)
    restored = ProvenanceGraph.from_dict(prov.to_dict())
    kinds = [n.primitive_kind for n in restored.nodes]
    assert PrimitiveKind.CATALOG in kinds


def test_publication_status_values() -> None:
    assert PublicationStatus.DRAFT == "draft"
    assert PublicationStatus.IN_REVIEW == "in_review"
    assert PublicationStatus.ACCEPTED == "accepted"
    assert PublicationStatus.PUBLISHED == "published"
    assert PublicationStatus.WITHDRAWN == "withdrawn"


# ---------------------------------------------------------------------------
# Notebook — interactive computation
# ---------------------------------------------------------------------------


def test_notebook_creation() -> None:
    nb = Notebook(
        id="nb-001",
        project_id="proj-001",
        name="Analysis notebook",
        content_uri="s3://bucket/notebooks/nb-001/",
    )
    assert nb.id == "nb-001"
    assert nb.status == NotebookStatus.DRAFT
    assert nb.cell_count == 0


def test_notebook_status_values() -> None:
    assert NotebookStatus.DRAFT == "draft"
    assert NotebookStatus.EXECUTING == "executing"
    assert NotebookStatus.COMPLETED == "completed"
    assert NotebookStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# Simulation — computational experiment
# ---------------------------------------------------------------------------


def test_simulation_creation() -> None:
    sim = Simulation(
        id="sim-001",
        project_id="proj-001",
        name="DFT simulation",
        workflow_id="wf-001",
        compute_resource_id="cr-001",
        parameters={"temperature": 300, "pressure": 1.0},
    )
    assert sim.id == "sim-001"
    assert sim.status == SimulationStatus.PENDING
    assert sim.parameters["temperature"] == 300


def test_simulation_status_values() -> None:
    assert SimulationStatus.PENDING == "pending"
    assert SimulationStatus.RUNNING == "running"
    assert SimulationStatus.COMPLETED == "completed"
    assert SimulationStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# ComputeResource — HPC allocation
# ---------------------------------------------------------------------------


def test_compute_resource_creation() -> None:
    cr = ComputeResource(
        id="cr-001",
        project_id="proj-001",
        name="GPU node allocation",
        slurm_partition="gpu",
        gpus=4,
        cpu_cores=32,
        memory_mb=128000,
        wall_time_hours=2.0,
    )
    assert cr.id == "cr-001"
    assert cr.status == ComputeResourceStatus.REQUESTED
    assert cr.gpus == 4
    assert cr.slurm_partition == "gpu"


def test_compute_resource_status_values() -> None:
    assert ComputeResourceStatus.REQUESTED == "requested"
    assert ComputeResourceStatus.ALLOCATED == "allocated"
    assert ComputeResourceStatus.RUNNING == "running"
    assert ComputeResourceStatus.RELEASED == "released"
    assert ComputeResourceStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# Agent — AI collaborator
# ---------------------------------------------------------------------------


def test_agent_creation() -> None:
    agent = Agent(
        id="agent-001",
        project_id="proj-001",
        name="Literature Agent",
        role="literature",
        status=AgentStatus.EXECUTING,
        current_task="Surveying 42 papers",
        tools=["search", "summarize"],
        model="claude-3-opus",
    )
    assert agent.id == "agent-001"
    assert agent.role == "literature"
    assert agent.status == AgentStatus.EXECUTING
    assert agent.current_task == "Surveying 42 papers"
    assert agent.tools == ["search", "summarize"]
    assert agent.model == "claude-3-opus"


def test_agent_status_values() -> None:
    assert AgentStatus.IDLE == "idle"
    assert AgentStatus.THINKING == "thinking"
    assert AgentStatus.EXECUTING == "executing"
    assert AgentStatus.WAITING == "waiting"
    assert AgentStatus.ERROR == "error"


# ---------------------------------------------------------------------------
# Conversation — one interaction modality
# ---------------------------------------------------------------------------


def test_conversation_creation() -> None:
    conv = Conversation(
        id="conv-001",
        project_id="proj-001",
        agent_id="agent-001",
        title="Discussion about catalyst X",
    )
    assert conv.id == "conv-001"
    assert conv.status == ConversationStatus.ACTIVE
    assert conv.agent_id == "agent-001"
    assert conv.message_count == 0


def test_conversation_status_values() -> None:
    assert ConversationStatus.ACTIVE == "active"
    assert ConversationStatus.COMPLETED == "completed"
    assert ConversationStatus.ABANDONED == "abandoned"


# ---------------------------------------------------------------------------
# PrimitiveKind enum covers all primitives
# ---------------------------------------------------------------------------


def test_primitive_kind_covers_all_primitives() -> None:
    """PrimitiveKind must include all 13 primitives (+ catalog for D-45 joins)."""
    expected = {
        "research_project",
        "research_goal",
        "hypothesis",
        "experiment",
        "workflow",
        "knowledge",
        "dataset",
        "publication",
        "notebook",
        "simulation",
        "compute_resource",
        "agent",
        "conversation",
        "catalog",
    }
    actual = {kind.value for kind in PrimitiveKind}
    assert actual == expected, f"Missing or extra primitive kinds: {actual ^ expected}"


def test_primitive_kind_values() -> None:
    assert PrimitiveKind.RESEARCH_PROJECT == "research_project"
    assert PrimitiveKind.RESEARCH_GOAL == "research_goal"
    assert PrimitiveKind.HYPOTHESIS == "hypothesis"
    assert PrimitiveKind.EXPERIMENT == "experiment"
    assert PrimitiveKind.WORKFLOW == "workflow"
    assert PrimitiveKind.KNOWLEDGE == "knowledge"
    assert PrimitiveKind.DATASET == "dataset"
    assert PrimitiveKind.PUBLICATION == "publication"
    assert PrimitiveKind.NOTEBOOK == "notebook"
    assert PrimitiveKind.SIMULATION == "simulation"
    assert PrimitiveKind.COMPUTE_RESOURCE == "compute_resource"
    assert PrimitiveKind.AGENT == "agent"
    assert PrimitiveKind.CONVERSATION == "conversation"


# ---------------------------------------------------------------------------
# Provenance is first-class on every primitive
# ---------------------------------------------------------------------------


def test_every_primitive_has_provenance_field() -> None:
    """Every primitive must have a `provenance` field."""
    primitives = [
        ("ResearchProject", ResearchProject),
        ("ResearchGoal", ResearchGoal),
        ("Hypothesis", Hypothesis),
        ("Experiment", Experiment),
        ("Knowledge", Knowledge),
        ("Dataset", Dataset),
        ("Publication", Publication),
        ("Notebook", Notebook),
        ("Simulation", Simulation),
        ("ComputeResource", ComputeResource),
        ("Agent", Agent),
        ("Conversation", Conversation),
    ]
    for name, cls in primitives:
        # Dataclass fields live in __dataclass_fields__, not as class attributes.
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        assert "provenance" in fields, f"{name} must have a provenance field"
        provenance_field = fields["provenance"]
        assert provenance_field.default_factory is not None, (
            f"{name}.provenance must use default_factory"
        )


# ---------------------------------------------------------------------------
# WorkflowStatus (referenced by other primitives)
# ---------------------------------------------------------------------------


def test_workflow_status_values() -> None:
    assert WorkflowStatus.ACCEPTED == "accepted"
    assert WorkflowStatus.PENDING == "pending"
    assert WorkflowStatus.RUNNING == "running"
    assert WorkflowStatus.COMPLETED == "completed"
    assert WorkflowStatus.FAILED == "failed"
    assert WorkflowStatus.CANCELLED == "cancelled"


# ---------------------------------------------------------------------------
# ResearchGoal — what we're trying to achieve
# ---------------------------------------------------------------------------


def test_research_goal_creation() -> None:
    goal = ResearchGoal(
        id="goal-001",
        project_id="proj-001",
        statement="Find a catalyst that reduces reaction time by 30%",
        priority="high",
    )
    assert goal.id == "goal-001"
    assert goal.status == ResearchGoalStatus.PROPOSED
    assert goal.priority == "high"
    assert goal.hypothesis_ids == []


def test_research_goal_status_values() -> None:
    assert ResearchGoalStatus.PROPOSED == "proposed"
    assert ResearchGoalStatus.ACTIVE == "active"
    assert ResearchGoalStatus.ACHIEVED == "achieved"
    assert ResearchGoalStatus.ABANDONED == "abandoned"
