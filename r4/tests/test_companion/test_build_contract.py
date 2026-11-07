"""G16 (2026-10-29): durable build contracts — the agent-edited JSON feature
list (flip ``passes`` only), progress log, and startup-ritual orientation."""

from __future__ import annotations

import json

import pytest

from r4.companion.build_contract import (
    contract_orientation,
    contract_status,
    mark_feature_passed,
    start_build_contract,
)

GOAL = "2D retro game maker"
FEATURES = [
    {"description": "Level editor with tile painting", "steps": ["open editor", "paint"]},
    {"description": "Sprite editor with color picker"},
    {"description": "Playable test mode", "steps": ["press play", "entity moves"]},
]


def _start(root) -> dict:
    return start_build_contract(str(root), GOAL, FEATURES, smoke_command="make smoke")


def test_start_creates_an_all_failing_contract(tmp_path) -> None:
    result = _start(tmp_path)
    assert result["features"] == 3 and not result.get("error")
    data = json.loads((tmp_path / ".r4/build" / "2d-retro-game-maker/features.json").read_text())
    assert data["goal"] == GOAL
    assert [f["id"] for f in data["features"]] == ["F-01", "F-02", "F-03"]
    assert all(f["passes"] is False for f in data["features"])
    assert (tmp_path / ".r4/build/2d-retro-game-maker/init.sh").exists()


def test_start_refuses_to_clobber_an_active_contract(tmp_path) -> None:
    _start(tmp_path)
    again = start_build_contract(str(tmp_path), "other build", FEATURES)
    assert "already exists" in again["error"]
    forced = start_build_contract(str(tmp_path), "other build", FEATURES, force=True)
    assert forced["features"] == 3


def test_validation_rejects_empty_goal_and_features(tmp_path) -> None:
    assert "goal" in start_build_contract(str(tmp_path), " ", FEATURES)["error"]
    assert "features" in start_build_contract(str(tmp_path), GOAL, [])["error"]
    assert "description" in start_build_contract(str(tmp_path), GOAL, [{}])["error"]


def test_pass_flips_only_the_flag_and_logs_evidence(tmp_path) -> None:
    _start(tmp_path)
    result = mark_feature_passed(str(tmp_path), "F-01", "pytest level_editor -q: 12 green")
    assert result == {
        "passed": "F-01",
        "remaining": ["F-02", "F-03"],
        "complete": False,
    }
    status = contract_status(str(tmp_path))
    assert status["passing"] == 1 and status["next"] == "F-02"
    progress = (tmp_path / ".r4/build/2d-retro-game-maker/progress.md").read_text()
    assert "F-01 PASS — pytest level_editor -q: 12 green" in progress
    again = mark_feature_passed(str(tmp_path), "F-01", "duplicate")
    assert "one-way" in again["error"]
    unknown = mark_feature_passed(str(tmp_path), "F-99", "nope")
    assert "unknown feature" in unknown["error"]
    no_evidence = mark_feature_passed(str(tmp_path), "F-02", " ")
    assert "evidence is required" in no_evidence["error"]


def test_completion_and_orientation(tmp_path) -> None:
    assert contract_orientation(str(tmp_path)) == ""
    _start(tmp_path)
    orientation = contract_orientation(str(tmp_path))
    assert "0/3 features passing" in orientation
    assert "F-01" in orientation
    assert "smoke" in orientation
    for fid in ("F-01", "F-02", "F-03"):
        mark_feature_passed(str(tmp_path), fid, "checked")
    status = contract_status(str(tmp_path))
    assert status["active"] is False
    assert contract_orientation(str(tmp_path)) == ""


def test_tools_registered_and_functional(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    from r4.companion import tools as companion_tools

    core_names = {t["name"] for t in companion_tools._core_tools()}
    assert {"build_contract_start", "build_contract_status", "build_contract_pass"} <= core_names
    out = json.loads(companion_tools._build_contract_start_tool(GOAL, FEATURES, "make smoke"))
    assert out["features"] == 3
    passed = json.loads(companion_tools._build_contract_pass_tool("F-01", "verified"))
    assert passed["passed"] == "F-01"
    assert json.loads(companion_tools._build_contract_status_tool())["next"] == "F-02"
