"""RC1 Blank Project Test — full R4 CLI chain."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

pytest.importorskip("umrs")

# Reuse Blank Project fixture builder from umrs tests (cross-repo helper splice
# — allowlisted in tools/test_import_hygiene_allowlist.txt).
_UMRS_TESTS = Path(__file__).resolve().parents[2] / "umrs" / "tests"
if str(_UMRS_TESTS) not in sys.path:
    sys.path.insert(0, str(_UMRS_TESTS))

from knowledge.blank_project_fixture import (  # noqa: E402 — splice allowlisted
    materialize_blank_project,
)

from r4._cli.project import cmd_project  # noqa: E402
from r4.project import ProjectConfig, ProjectManager  # noqa: E402


@pytest.fixture
def isolated_pm(tmp_path, monkeypatch):
    pm = ProjectManager(config_dir=tmp_path / "r4-config")
    monkeypatch.setattr("r4.project._project_manager", pm)
    return pm


def _blank_cli_fixture(isolated_pm, tmp_path, project_id: str = "blank-cli") -> str:
    """Create the blank-cli project and ingest the Blank Project fixture."""
    isolated_pm.create(ProjectConfig(project_id=project_id, name="Blank CLI"))
    fixture_root = materialize_blank_project(tmp_path / "fixture")
    return str(fixture_root)


def test_blank_cli_ingest_and_understand(isolated_pm, tmp_path, capsys):
    """Ingest fixture then understand — counts match documented minimums."""
    fixture_root = _blank_cli_fixture(isolated_pm, tmp_path)
    cmd_project(
        Namespace(
            project_command="ingest",
            project_id="blank-cli",
            paths=[fixture_root],
            local=True,
            json=False,
        )
    )
    assert "Ingested" in capsys.readouterr().out

    cmd_project(Namespace(project_command="understand", project_id="blank-cli", json=True))
    understand = json.loads(capsys.readouterr().out)
    assert understand["extracted_count"] >= 15
    assert understand["graph_nodes"] >= 15


def test_blank_cli_verify_readiness_plan(isolated_pm, tmp_path, capsys):
    """Verify + readiness + plan — uses same fixture via fresh ingest."""
    fixture_root = _blank_cli_fixture(isolated_pm, tmp_path)
    cmd_project(
        Namespace(
            project_command="ingest",
            project_id="blank-cli",
            paths=[fixture_root],
            local=True,
            json=False,
        )
    )
    capsys.readouterr()
    cmd_project(
        Namespace(
            project_command="verify",
            project_id="blank-cli",
            question="Explain my project",
            json=True,
        )
    )
    verify = json.loads(capsys.readouterr().out)
    assert verify["answered"] is True

    cmd_project(Namespace(project_command="readiness", project_id="blank-cli", json=True))
    readiness = json.loads(capsys.readouterr().out)
    assert readiness["readiness_level"]

    cmd_project(
        Namespace(
            project_command="plan",
            project_id="blank-cli",
            research_goal="What GCMC MOF research should we perform next?",
            json=True,
        )
    )
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["objectives"]) >= 1
    assert len(plan["source_knowledge_ids"]) >= 1


def test_blank_cli_plan_points_at_the_umrs_loop(isolated_pm, tmp_path, capsys, monkeypatch):
    """The RC1 simulated run retired (WP-07): after ingest + plan the CLI hands off to
    `r4 project loop start` and names the registered workflows it could resolve."""
    from r4._cli import project_knowledge as pk

    fixture_root = _blank_cli_fixture(isolated_pm, tmp_path)
    cmd_project(
        Namespace(
            project_command="ingest",
            project_id="blank-cli",
            paths=[fixture_root],
            local=True,
            json=False,
        )
    )
    capsys.readouterr()
    monkeypatch.setattr(pk, "_registered_workflow_names", lambda project_id: ["demo08_yield_eval"])
    cmd_project(
        Namespace(
            project_command="plan",
            project_id="blank-cli",
            research_goal="GCMC MOF next steps",
            json=True,
        )
    )
    plan_payload = json.loads(capsys.readouterr().out)
    assert plan_payload["registered_workflows"] == ["demo08_yield_eval"]
    assert len(plan_payload["experiments"]) >= 1

    with pytest.raises(SystemExit):
        cmd_project(Namespace(project_command="run", project_id="blank-cli", json=True))
    assert "loop start" in capsys.readouterr().err
