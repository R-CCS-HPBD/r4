"""Tests for r4 project status CLI (+ --watch)."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from r4._cli.project import cmd_project
from r4.project import ProjectConfig, ProjectManager


@pytest.fixture
def isolated_pm(tmp_path, monkeypatch):
    pm = ProjectManager(config_dir=tmp_path / "r4-config")
    monkeypatch.setattr("r4.project._project_manager", pm)
    monkeypatch.setattr("r4.project.get_project_manager", lambda: pm)
    return pm


class TestProjectStatusCLI:
    """Tests for `r4 project status` subcommand."""

    def test_status_basic(self, isolated_pm, capsys):
        """Status without --watch prints once."""
        isolated_pm.create(ProjectConfig(project_id="demo", name="Demo"))

        with patch("r4.client.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.knowledge.status.return_value = {
                "artifacts_ingested": 5,
                "knowledge_nodes": 12,
                "knowledge_edges": 8,
                "extraction_state": "complete",
                "verifications_pending": 2,
                "readiness_level": "ready",
                "coverage_score": 0.85,
            }
            mock_client.workflows.list_workflows.return_value = []
            mock_client.knowledge.list_plans.return_value = {"plans": []}
            MockClient.return_value = mock_client

            cmd_project(
                Namespace(
                    project_command="status",
                    project_id="demo",
                    json=False,
                    watch=False,
                    interval=1.0,
                )
            )

        out = capsys.readouterr().out
        assert "Project: demo" in out
        assert "Artifacts ingested: 5" in out
        assert "Extraction: complete" in out
        assert "Graph: 12 nodes, 8 edges" in out
        assert "Coverage: 0.85" in out
        assert "Verifications pending: 2" in out
        assert "Readiness: ready" in out
        assert "Registered workflows: 0" in out
        assert "Research plans: 0" in out

    def test_status_json(self, isolated_pm, capsys):
        """Status with --json returns raw data dict."""
        isolated_pm.create(ProjectConfig(project_id="json-demo", name="Json"))

        with patch("r4.client.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.knowledge.status.return_value = {
                "artifacts_ingested": 3,
                "knowledge_nodes": 6,
                "knowledge_edges": 4,
                "extraction_state": "running",
                "verifications_pending": 0,
                "readiness_level": "planning",
                "coverage_score": 0.5,
            }
            mock_client.workflows.list_workflows.return_value = [MagicMock()]
            mock_client.knowledge.list_plans.return_value = {"plans": []}
            MockClient.return_value = mock_client

            cmd_project(
                Namespace(
                    project_command="status",
                    project_id="json-demo",
                    json=True,
                    watch=False,
                    interval=1.0,
                )
            )

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "knowledge" in data
        assert data["knowledge"]["artifacts_ingested"] == 3
        assert data["workflow_count"] == 1

    def test_status_with_plans(self, isolated_pm, capsys):
        """Status shows plan count and latest plan ID."""
        isolated_pm.create(ProjectConfig(project_id="plan-demo", name="Plan"))

        with patch("r4.client.Client") as MockClient:
            mock_client = MagicMock()
            mock_client.knowledge.status.return_value = {
                "artifacts_ingested": 1,
                "knowledge_nodes": 2,
                "knowledge_edges": 1,
                "extraction_state": "complete",
                "verifications_pending": 0,
                "readiness_level": "ready",
                "coverage_score": 1.0,
            }
            mock_client.workflows.list_workflows.return_value = []
            mock_client.knowledge.list_plans.return_value = {
                "plans": [
                    {"plan_id": "plan-abc", "objectives": 3},
                ]
            }
            MockClient.return_value = mock_client

            cmd_project(
                Namespace(
                    project_command="status",
                    project_id="plan-demo",
                    json=False,
                    watch=False,
                    interval=1.0,
                )
            )

        out = capsys.readouterr().out
        assert "Research plans: 1" in out
        assert "Latest plan: plan-abc" in out

    def test_status_resolves_current_project(self, isolated_pm, capsys):
        """Status without project_id fails cleanly when no current set."""
        with pytest.raises(SystemExit) as exc_info:
            cmd_project(
                Namespace(
                    project_command="status",
                    project_id=None,
                    json=False,
                    watch=False,
                    interval=1.0,
                )
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "No project specified" in err

    def test_status_missing_project_fails(self, isolated_pm, capsys):
        """Status on non-existent project exits with error."""
        isolated_pm.create(ProjectConfig(project_id="ghost", name="Ghost"))

        with pytest.raises(SystemExit) as exc_info:
            cmd_project(
                Namespace(
                    project_command="status",
                    project_id="nonexistent",
                    json=False,
                    watch=False,
                    interval=1.0,
                )
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_watch_mode_runs_once_then_interrupts(self, isolated_pm, capsys):
        """Watch mode prints, then KeyboardInterrupt stops it."""
        isolated_pm.create(ProjectConfig(project_id="watch-demo", name="Watch"))

        # Use patch to make time.sleep raise KeyboardInterrupt after first print
        with patch("r4.client.Client") as MockClient:
            with patch("time.sleep", side_effect=KeyboardInterrupt()):
                mock_client = MagicMock()
                mock_client.knowledge.status.return_value = {
                    "artifacts_ingested": 2,
                    "knowledge_nodes": 4,
                    "knowledge_edges": 3,
                    "extraction_state": "complete",
                    "verifications_pending": 0,
                    "readiness_level": "ready",
                }
                mock_client.workflows.list_workflows.return_value = []
                mock_client.knowledge.list_plans.return_value = {"plans": []}
                MockClient.return_value = mock_client

                cmd_project(
                    Namespace(
                        project_command="status",
                        project_id="watch-demo",
                        json=False,
                        watch=True,
                        interval=0.1,
                    )
                )

        out = capsys.readouterr().out
        assert "Project: watch-demo" in out
        assert "Watching stopped." in out

    def test_watch_json_mode(self, isolated_pm, capsys):
        """Watch mode with --json returns full data dict."""
        isolated_pm.create(ProjectConfig(project_id="json-watch", name="JW"))

        with patch("r4.client.Client") as MockClient:
            with patch("time.sleep", side_effect=KeyboardInterrupt()):
                mock_client = MagicMock()
                mock_client.knowledge.status.return_value = {
                    "artifacts_ingested": 10,
                    "knowledge_nodes": 20,
                    "knowledge_edges": 15,
                    "extraction_state": "complete",
                    "verifications_pending": 5,
                    "readiness_level": "ready",
                    "coverage_score": 0.92,
                }
                mock_client.workflows.list_workflows.return_value = [MagicMock()]
                mock_client.knowledge.list_plans.return_value = {"plans": []}
                MockClient.return_value = mock_client

                cmd_project(
                    Namespace(
                        project_command="status",
                        project_id="json-watch",
                        json=True,
                        watch=True,
                        interval=0.1,
                    )
                )

        out = capsys.readouterr().out
        # In watch mode with JSON, first output is the data dict
        data = json.loads(out)
        assert "knowledge" in data
        assert data["knowledge"]["artifacts_ingested"] == 10
        assert data["workflow_count"] == 1
