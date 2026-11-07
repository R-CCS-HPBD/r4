"""G15 (2026-10-29): externalized sprint evaluation — fresh-context grader,
rubric + hard thresholds, verdicts recorded on the contract."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import ResearchCompanion
from r4.companion.build_contract import start_build_contract
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry
from r4.companion.sprint_review import evaluator_tools, parse_verdict

FEATURES = [
    {"description": "Level editor", "steps": ["open", "paint"]},
    {"description": "Play mode"},
]


def _verdict_client(payload: str) -> MagicMock:
    client = MagicMock()
    client._config.model = "test-model"
    client._request.return_value = MagicMock(
        content=payload,
        model="test-model",
        usage=MagicMock(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        raw_response={
            "choices": [
                {"message": {"content": payload, "role": "assistant"}, "finish_reason": "stop"}
            ]
        },
    )
    return client


def _agent(client: MagicMock) -> ResearchCompanion:
    return ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )


def test_parse_verdict_shapes() -> None:
    clean = parse_verdict('{"verdict": "pass", "findings": [], "summary": "works"}')
    assert clean["verdict"] == "pass"
    fenced = parse_verdict('Some prose\n```json\n{"verdict": "fail", "findings": []}\n```')
    assert fenced["verdict"] == "fail"
    broken = parse_verdict("no json here")
    assert broken["verdict"] == "error"
    assert "no parseable" in broken["summary"]


def test_evaluator_tool_subset() -> None:
    wire = [
        {"name": "shell_exec"},
        {"name": "file_read"},
        {"name": "file_write"},
        {"name": "grep_search"},
        {"name": "apply_patch"},
        {"name": "list_dir"},
        {"name": "glob_search"},
    ]
    names = {tool["name"] for tool in evaluator_tools(wire)}
    assert names == {"shell_exec", "file_read", "grep_search", "glob_search", "list_dir"}


VERDICT_JSON = json.dumps(
    {
        "verdict": "fail",
        "findings": [
            {"criterion": "Functionality", "severity": "fail", "detail": "play button dead"}
        ],
        "summary": "core loop does not run",
    }
)


def _fresh_tool(name: str) -> dict:
    return {
        "name": name,
        "description": f"{name} tool",
        "parameters": {"type": "object", "properties": {}},
    }


def _review_agent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> ResearchCompanion:
    monkeypatch.chdir(tmp_path)
    start_build_contract(str(tmp_path), "retro maker", FEATURES, smoke_command="make smoke")
    agent = _agent(_verdict_client(VERDICT_JSON))
    agent._last_wire_tools = [
        _fresh_tool(name) for name in ("shell_exec", "file_read", "file_write")
    ]
    return agent


def test_review_verdict_is_returned_and_recorded(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _review_agent(tmp_path, monkeypatch)
    out = json.loads(agent.run_sprint_review("the play mode"))
    assert out["verdict"] == "fail"
    assert out["next"] == "F-01"
    assert "fix the findings" in out["note"]
    reviews = (tmp_path / ".r4/build/retro-maker/reviews.jsonl").read_text()
    assert '"verdict": "fail"' in reviews
    progress = (tmp_path / ".r4/build/retro-maker/progress.md").read_text()
    assert "REVIEW FAIL" in progress


def test_evaluator_gets_fresh_context_and_read_only_tools(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _review_agent(tmp_path, monkeypatch)
    agent.run_sprint_review("the play mode")
    child_payload = agent.client._request.call_args_list[-1].args[0]
    texts = json.dumps(child_payload["messages"])
    assert "skeptical" in texts or "QA evaluator" in texts
    assert "Level editor" in texts
    child_tools = {t["function"]["name"] for t in child_payload.get("tools") or []}
    assert "file_write" not in child_tools
    assert "file_read" in child_tools or child_tools == set()


def test_review_without_contract_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = _agent(_verdict_client("{}"))
    out = json.loads(agent.run_sprint_review())
    assert "no build contract" in out["error"]


def test_completed_contract_is_not_reviewed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.companion.build_contract import mark_feature_passed

    monkeypatch.chdir(tmp_path)
    start_build_contract(str(tmp_path), "tiny", [{"description": "one"}])
    mark_feature_passed(str(tmp_path), "F-01", "checked")
    agent = _agent(_verdict_client("{}"))
    out = json.loads(agent.run_sprint_review())
    assert "complete" in out["error"]


def test_review_tool_wiring_and_profile(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("R4_AGENT_PROFILE", raising=False)
    agent = _agent(_verdict_client("{}"))
    wired = agent._with_async_agent_tools([{"name": "file_read"}])
    assert "build_contract_review" in {tool["name"] for tool in wired}
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    wired = agent._with_async_agent_tools([{"name": "file_read"}])
    assert "build_contract_review" not in {tool["name"] for tool in wired}
