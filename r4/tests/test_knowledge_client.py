"""
Tests for the R4 KnowledgeClient — Phase 13 UI Integration.

Tests the R4 SDK methods for the UMRS knowledge modules (Phases 3-12).
Uses mock HTTP responses to verify the client correctly proxies
through the Gateway to UMRS.

Coverage:
- Phase 3: ingest(), ingest_status()
- Phase 3: ingest(), ingest_status()
- Phase 4: extract() — RETIRED 2026-10-20 (never mounted; pins the refusal)
- Phase 5: link() — RETIRED (pins the refusal); get_graph()
- Phase 6: verify(); verify_status() — RETIRED (pins the refusal)
- Phase 7: coverage(), coverage_gaps() — RETIRED (pins the refusals)
- Phase 8: external_search()
- Phase 9: memory(), memory_add() — RETIRED (pins the refusals)
- Phase 10: readiness(), readiness_approve()
- Phase 11: plan(), get_plan(), list_plans(), approve_plan() — RETIRED (pins the refusals)
- Phase 12: start_loop(), stop_loop(), get_loop(), list_loops()
- Phase 13: correct() — RETIRED (pins the refusal)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("umrs")

from r4.errors import R4Error
from r4.knowledge import KnowledgeClient


@pytest.fixture
def mock_client() -> MagicMock:
    """Create a mock R4 client for testing."""
    client = MagicMock()
    client._api_prefix = "/gw"
    return client


@pytest.fixture
def knowledge_client(mock_client: MagicMock) -> KnowledgeClient:
    """Create a KnowledgeClient with a mock parent client."""
    return KnowledgeClient(mock_client)


class TestKnowledgeClientPhase3Ingestion:
    """Phase 3: Unified Ingestion."""

    def test_ingest_rides_the_durable_path_planner(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock, tmp_path
    ) -> None:
        """Compat ``ingest(files=...)`` sends file CONTENT through the durable
        k2 route — never the retired ``{"files": [paths]}`` body."""
        paper = tmp_path / "paper1.md"
        paper.write_text("# results")
        mock_client.post.return_value = {"job_id": "job-9", "status": "queued"}
        result = knowledge_client.ingest(project_id="test-project", files=[str(paper)])
        url = mock_client.post.call_args[0][0]
        body = mock_client.post.call_args[1]["json"]
        assert url.endswith("/knowledge/ingest/ingest")
        assert body["sources"] == [
            {"kind": "inline_text", "uri": "paper1.md", "content": "# results"}
        ]
        assert result["job_id"] == "job-9"

    def test_ingest_status(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"status": "completed"}
        result = knowledge_client.ingest_status(project_id="test-project")
        assert result["status"] == "completed"

    def test_ingest_sources_posts_to_k2_route(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        """Durable ingest (ADR-015) goes through /gw/knowledge/ingest/ingest."""
        mock_client.post.return_value = {"job_id": "job-1", "status": "queued"}
        result = knowledge_client.ingest_sources(
            "test-project",
            [
                {"kind": "http_url", "uri": "https://example.com/page"},
                {"kind": "inline_text", "uri": "a.md", "content": "# a"},
            ],
            allow_domains=["example.com"],
        )
        mock_client.post.assert_called_once()
        url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
        assert url == "/gw/knowledge/ingest/ingest"
        assert kwargs["json"]["project_id"] == "test-project"
        assert kwargs["json"]["allow_domains"] == ["example.com"]
        assert kwargs["json"]["sources"][0]["kind"] == "http_url"
        assert result["job_id"] == "job-1"

    def test_ingest_sources_omits_empty_allow_list(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.post.return_value = {"job_id": "job-2", "status": "queued"}
        knowledge_client.ingest_sources("p", [{"kind": "http_url", "uri": "https://x.org"}])
        payload = mock_client.post.call_args[1]["json"]
        assert "allow_domains" not in payload


class TestKnowledgeClientPhase4Extraction:
    """Phase 4: Knowledge Extraction."""

    def test_extract_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        # RETIRED 2026-10-20 (plan H-10.2): no server route ever existed.
        mock_client.post.return_value = {"status": "ok", "extracted_entities": []}
        with pytest.raises(R4Error, match="never mounted"):
            knowledge_client.extract(
                project_id="test-project",
                source_id="source-1",
            )
        mock_client.post.assert_not_called()


class TestKnowledgeClientPhase5Linking:
    """Phase 5: Knowledge Linking."""

    def test_link_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        # RETIRED 2026-10-20 (plan H-10.2): refusals name the proposals lane.
        mock_client.post.return_value = {"status": "linked"}
        with pytest.raises(R4Error, match="graph_propose"):
            knowledge_client.link(
                project_id="test-project",
                source_id="node-1",
                target_id="node-2",
                relationship_type="cites",
            )
        mock_client.post.assert_not_called()

    def test_get_graph(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"nodes": [], "edges": []}
        result = knowledge_client.get_graph(project_id="test-project")
        assert "nodes" in result
        assert "edges" in result


class TestKnowledgeClientPhase6Verification:
    """Phase 6: Knowledge Verification."""

    def test_verify_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.post.return_value = {
            "answer": "Test answer",
            "confidence": 0.85,
            "reasoning_path": ["Step 1", "Step 2"],
        }
        result = knowledge_client.verify(
            project_id="test-project",
            question="What supports hypothesis H7?",
        )
        mock_client.post.assert_called_once()
        assert result["confidence"] == 0.85

    def test_verify_status(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        # RETIRED 2026-10-20 (plan H-10.2): the durable-job lane is named.
        mock_client.get.return_value = {"status": "verified"}
        with pytest.raises(R4Error, match="submit_verify"):
            knowledge_client.verify_status(project_id="test-project")
        mock_client.get.assert_not_called()


class TestKnowledgeClientPhase7Coverage:
    """Phase 7: Coverage Dashboard."""

    def test_coverage_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        # RETIRED 2026-10-20 (plan H-10.2).
        mock_client.get.return_value = {"coverage_percentage": 0.75, "gaps": []}
        with pytest.raises(R4Error, match="readiness"):
            knowledge_client.coverage(project_id="test-project")
        mock_client.get.assert_not_called()

    def test_coverage_gaps(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        # RETIRED 2026-10-20 (plan H-10.2).
        mock_client.get.return_value = {"gaps": [{"area": "methodology", "severity": "high"}]}
        with pytest.raises(R4Error, match="readiness"):
            knowledge_client.coverage_gaps(project_id="test-project")
        mock_client.get.assert_not_called()


class TestKnowledgeClientPhase8External:
    """Phase 8: External Knowledge."""

    def test_external_search_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.post.return_value = {"results": [{"title": "Paper 1", "url": "..."}]}
        result = knowledge_client.external_search(
            project_id="test-project",
            query="MOF CO2 capture",
        )
        mock_client.post.assert_called_once()
        assert len(result["results"]) == 1


class TestKnowledgeClientPhase9Memory:
    """Phase 9: Scientific Memory."""

    def test_memory_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        # RETIRED 2026-10-20 (plan H-10.2; TUI retired /memory 2026-10-17).
        mock_client.get.return_value = {"entries": [], "timeline": []}
        with pytest.raises(R4Error, match="never mounted"):
            knowledge_client.memory(project_id="test-project")
        mock_client.get.assert_not_called()

    def test_memory_add_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        # RETIRED 2026-10-20 (plan H-10.2).
        mock_client.post.return_value = {"status": "added", "memory_id": "mem-1"}
        with pytest.raises(R4Error, match="ingest_paths"):
            knowledge_client.memory_add(
                project_id="test-project",
                memory_type="experiment",
                content="Experiment E17 completed with positive results",
            )
        mock_client.post.assert_not_called()


class TestKnowledgeClientPhase10Readiness:
    """Phase 10: Readiness Gate."""

    def test_readiness_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.get.return_value = {
            "ready": True,
            "readiness_level": "high",
            "readiness_checks": [],
        }
        result = knowledge_client.readiness(project_id="test-project")
        assert result["ready"] is True
        assert result["readiness_level"] == "high"

    def test_readiness_approve_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.post.return_value = {"status": "approved"}
        result = knowledge_client.readiness_approve(project_id="test-project")
        assert result["status"] == "approved"


class TestKnowledgeClientPhase11Planner:
    """Phase 11: Research Planner."""

    def test_plan_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.post.return_value = {
            "plan_id": "plan-1",
            "objectives": [],
            "hypotheses": [],
            "experiments": [],
        }
        # RETIRED 2026-10-20 (plan H-10.2): research planning is a research loop.
        with pytest.raises(R4Error, match="start_loop"):
            knowledge_client.plan(
                project_id="test-project",
                research_goal="Optimize MOF-5 for CO2 capture",
            )
        mock_client.post.assert_not_called()

    def test_get_plan(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"plan_id": "plan-1", "objectives": []}
        with pytest.raises(R4Error, match="loop"):
            knowledge_client.get_plan(project_id="test-project", plan_id="plan-1")
        mock_client.get.assert_not_called()

    def test_list_plans(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.get.return_value = {"plans": []}
        with pytest.raises(R4Error, match="list_loops"):
            knowledge_client.list_plans(project_id="test-project")
        mock_client.get.assert_not_called()

    def test_approve_plan(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.post.return_value = {"status": "approved"}
        with pytest.raises(R4Error, match="loop"):
            knowledge_client.approve_plan(project_id="test-project", plan_id="plan-1")
        mock_client.post.assert_not_called()


class TestKnowledgeClientPhase12Loop:
    """Phase 12: Autonomous Research Loop — delegates onto r4.Client loop API."""

    def test_start_loop_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        mock_client.start_loop.return_value = {
            "loop_id": "loop-1",
            "status": "running",
            "phase": "planning",
        }
        result = knowledge_client.start_loop(
            project_id="test-project",
            research_goal="Optimize MOF-5 for CO2 capture",
            max_iterations=10,
        )
        mock_client.start_loop.assert_called_once_with(
            "test-project",
            "Optimize MOF-5 for CO2 capture",
            max_iterations=10,
            approved=False,
        )
        assert result["loop_id"] == "loop-1"

    def test_stop_loop(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.stop_loop.return_value = {"status": "stopped"}
        result = knowledge_client.stop_loop(project_id="test-project", loop_id="loop-1")
        mock_client.stop_loop.assert_called_once_with("loop-1")
        assert result["status"] == "stopped"

    def test_get_loop(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.get_loop_status.return_value = {"loop_id": "loop-1", "status": "running"}
        result = knowledge_client.get_loop(project_id="test-project", loop_id="loop-1")
        mock_client.get_loop_status.assert_called_once_with("loop-1")
        assert result["loop_id"] == "loop-1"

    def test_list_loops(self, knowledge_client: KnowledgeClient, mock_client: MagicMock) -> None:
        mock_client.list_loops.return_value = {"loops": []}
        result = knowledge_client.list_loops(project_id="test-project")
        mock_client.list_loops.assert_called_once_with()
        assert "loops" in result


class TestKnowledgeClientPhase13Correction:
    """Phase 13: Knowledge Correction."""

    def test_correct_calls_gateway(
        self, knowledge_client: KnowledgeClient, mock_client: MagicMock
    ) -> None:
        # RETIRED 2026-10-20 (plan H-10.2).
        mock_client.post.return_value = {"status": "corrected"}
        with pytest.raises(R4Error, match="graph_propose"):
            knowledge_client.correct(
                project_id="test-project",
                node_id="node-1",
                correction="Updated relationship type from CITES to IMPLEMENTS",
            )
        mock_client.post.assert_not_called()


class TestKnowledgeClientKnowledgeVerification:
    """Tests for TASK-025: knowledge verification durable job layer."""

    def test_submit_verify_calls_gateway(self, mock_client, knowledge_client):
        """submit_verify calls the k2/verify endpoint."""
        knowledge_client.submit_verify("proj-1")
        mock_client.post.assert_called_once_with(
            f"{mock_client._api_prefix}/knowledge/ingest/verify",
            json={"project_id": "proj-1", "verifier_names": None},
        )

    def test_submit_verify_with_verifier_names(self, mock_client, knowledge_client):
        """submit_verify passes verifier_names to gateway."""
        knowledge_client.submit_verify("proj-1", verifier_names=["duplicate_knowledge"])
        mock_client.post.assert_called_once_with(
            f"{mock_client._api_prefix}/knowledge/ingest/verify",
            json={
                "project_id": "proj-1",
                "verifier_names": ["duplicate_knowledge"],
            },
        )

    def test_verify_job_status_calls_gateway(self, mock_client, knowledge_client):
        """verify_job_status calls the jobs endpoint."""
        knowledge_client.verify_job_status("job-123")
        mock_client.get.assert_called_once_with(
            f"{mock_client._api_prefix}/jobs/job-123",
        )

    def test_transition_finding_calls_gateway(self, mock_client, knowledge_client):
        """transition_finding calls the findings PATCH endpoint."""
        knowledge_client.transition_finding(
            project_id="proj-1",
            finding_key="find-1",
            from_status="open",
            new_status="acknowledged",
            evidence="Reviewed",
        )
        mock_client.patch.assert_called_once_with(
            f"{mock_client._api_prefix}/knowledge/findings/find-1",
            json={
                "finding_key": "find-1",
                "from_status": "open",
                "new_status": "acknowledged",
                "evidence": "Reviewed",
            },
        )


class TestKnowledgeClientLineage:
    """Artifact provenance chain (workstream §7 surface)."""

    def test_lineage_calls_route_with_project_and_depth(self, mock_client, knowledge_client):
        """lineage GETs /knowledge/lineage/{id} with project + depth params."""
        knowledge_client.lineage("art-1", project_id="proj-1", depth=5)
        mock_client.get.assert_called_once_with(
            f"{mock_client._api_prefix}/knowledge/lineage/art-1",
            params={"project_id": "proj-1", "depth": 5},
        )

    def test_lineage_empty_chain_is_not_error(self, mock_client, knowledge_client):
        """Empty ancestors/descendants pass through unchanged (honest absence)."""
        mock_client.get.return_value = {
            "artifact_id": "art-1",
            "ancestors": [],
            "descendants": [],
        }
        result = knowledge_client.lineage("art-1", project_id="proj-1")
        assert result["ancestors"] == []
        assert result["descendants"] == []
