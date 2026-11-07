"""Tests for r4 project knowledge workflow CLI commands."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

pytest.importorskip("umrs")

from r4._cli.project import cmd_project
from r4.project import ProjectConfig, ProjectManager


@pytest.fixture
def isolated_pm(tmp_path, monkeypatch):
    pm = ProjectManager(config_dir=tmp_path / "r4-config")
    monkeypatch.setattr("r4.project._project_manager", pm)
    monkeypatch.setattr("r4.project.get_project_manager", lambda: pm)
    return pm


@pytest.fixture
def sample_sources(tmp_path) -> Path:
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "paper1.pdf").write_bytes(
        b"%PDF-1.4\nThe goal of this paper is to test the R4 CLI bridge.\n"
        b"Keywords: cli, knowledge, extraction\n"
    )
    code = tmp_path / "code"
    code.mkdir()
    (code / "model.py").write_text(
        "def quicksort_algorithm(arr):\n"
        '    """Sort using the quicksort algorithm."""\n'
        "    return arr\n"
    )
    return tmp_path


class TestProjectKnowledgeCLI:
    def test_import_init_verify_flow(self, isolated_pm, sample_sources, capsys):
        isolated_pm.create(ProjectConfig(project_id="demo", name="Demo"))
        project_id = "demo"

        cmd_project(
            Namespace(
                project_command="import",
                project_id=project_id,
                paths=[str(sample_sources / "papers"), str(sample_sources / "code")],
                local=True,
                json=False,
            )
        )
        ingest_out = capsys.readouterr().out
        assert "Ingested" in ingest_out

        cmd_project(
            Namespace(
                project_command="init",
                project_id=project_id,
                json=True,
            )
        )
        understand_payload = json.loads(capsys.readouterr().out)
        assert understand_payload["extracted_count"] >= 2
        assert understand_payload["graph_nodes"] >= 2

        cmd_project(
            Namespace(
                project_command="verify",
                project_id=project_id,
                question="What algorithms are implemented?",
                json=True,
            )
        )
        verify_payload = json.loads(capsys.readouterr().out)
        assert verify_payload["answered"] is True
        assert "quicksort" in verify_payload["answer"].lower()

    def test_init_without_import_fails(self, isolated_pm, capsys):
        isolated_pm.create(ProjectConfig(project_id="empty", name="Empty"))

        with pytest.raises(SystemExit) as exc_info:
            cmd_project(
                Namespace(
                    project_command="init",
                    project_id="empty",
                    json=False,
                )
            )
        assert exc_info.value.code == 1
        assert "No artifacts" in capsys.readouterr().err

    def test_readiness_json(self, isolated_pm, sample_sources, capsys):
        isolated_pm.create(ProjectConfig(project_id="demo2", name="Demo 2"))
        project_id = "demo2"

        cmd_project(
            Namespace(
                project_command="import",
                project_id=project_id,
                paths=[str(sample_sources)],
                local=True,
                json=False,
            )
        )
        capsys.readouterr()

        cmd_project(
            Namespace(
                project_command="readiness",
                project_id="demo2",
                json=True,
            )
        )
        payload = json.loads(capsys.readouterr().out)
        assert "readiness_level" in payload
        assert "recommendation" in payload

    def test_plan_json(self, isolated_pm, sample_sources, capsys):
        isolated_pm.create(ProjectConfig(project_id="plan-demo", name="Plan Demo"))

        cmd_project(
            Namespace(
                project_command="import",
                project_id="plan-demo",
                paths=[str(sample_sources)],
                local=True,
                json=False,
            )
        )
        capsys.readouterr()

        cmd_project(
            Namespace(
                project_command="plan",
                project_id="plan-demo",
                research_goal="What research should we perform next?",
                json=True,
            )
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["research_goal"] == "What research should we perform next?"
        assert len(payload["objectives"]) >= 1
        assert len(payload["source_knowledge_ids"]) >= 1

    def test_run_verb_is_retired_in_favour_of_loop_start(self, isolated_pm, capsys):
        """WP-07: the local RC1 run retired; research runs are UMRS loops."""
        isolated_pm.create(ProjectConfig(project_id="run-demo", name="Run Demo"))
        with pytest.raises(SystemExit) as exc_info:
            cmd_project(Namespace(project_command="run", project_id="run-demo", json=False))
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Unknown project command" in err and "loop start" in err

    def test_plan_suggests_registered_workflows(
        self, isolated_pm, sample_sources, capsys, monkeypatch
    ):
        """G-P1-3: plan time names the registered workflows (or says the catalog is unreachable)."""
        from r4._cli import project_knowledge as pk

        isolated_pm.create(ProjectConfig(project_id="plan-wf", name="Plan WF"))
        cmd_project(
            Namespace(
                project_command="import",
                project_id="plan-wf",
                paths=[str(sample_sources)],
                local=True,
                json=False,
            )
        )
        capsys.readouterr()
        monkeypatch.setattr(
            pk, "_registered_workflow_names", lambda project_id: ["demo08_yield_eval"]
        )
        cmd_project(
            Namespace(
                project_command="plan", project_id="plan-wf", research_goal="Verify it", json=False
            )
        )
        out = capsys.readouterr().out
        assert "demo08_yield_eval" in out and "r4 project loop start plan-wf" in out

        monkeypatch.setattr(pk, "_registered_workflow_names", lambda project_id: None)
        cmd_project(
            Namespace(
                project_command="plan", project_id="plan-wf", research_goal="Verify it", json=True
            )
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["registered_workflows"] is None

    def test_project_data_dir(self, isolated_pm):
        isolated_pm.create(ProjectConfig(project_id="paths", name="Paths"))
        data_dir = isolated_pm.data_dir("paths")
        assert data_dir.name == "paths"
        assert data_dir.parent.name == "projects-data"
