"""ADR-037 family grouping — verb-cluster activation at selection (row 3).

Static prefix families (measured live: project 14, workflow 12, knowledge 7,
file 6, research 6, mcp 6, endpoint 5, job 4), lexical message-match
activation, core channel always offered; removals recorded as family_off.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry
from r4.companion.tool_selection import (
    _FAMILY_PREFIXES,
    classify_family,
    match_families_from_text,
    select_with_family,
)


def _tool(name: str) -> dict:
    return {"name": name, "description": name, "parameters": {"type": "object"}}


CATALOG = [
    _tool("file_read"),
    _tool("final_output"),
    _tool("ask_user"),
    _tool("workflow_run"),
    _tool("workflow_status"),
    _tool("project_list"),
    _tool("knowledge_query"),
    _tool("mcp_invoke"),
    _tool("shell_exec"),
    _tool("file_write"),
]


class TestClassifyAndMatch:
    def test_classify_uses_first_segment(self):
        assert classify_family("workflow_run") == "workflow"
        assert classify_family("mcp_invoke") == "mcp"
        assert classify_family("file_read") == "file"

    def test_lexical_match_message_side(self):
        matched = match_families_from_text("run my workflow now", CATALOG)
        assert matched == frozenset({"workflow"})
        matched = match_families_from_text("workflow and knowledge advice please", CATALOG)
        assert matched == frozenset({"workflow", "knowledge"})

    def test_generic_message_matches_nothing(self):
        assert match_families_from_text("tell me a joke", CATALOG) == frozenset()


class TestSelectWithFamily:
    def test_core_tool_always_offered(self):
        offered, rows, utility = select_with_family(tools=CATALOG, message_text="tell me a joke")
        names = {t["name"] for t in offered}
        assert "file_read" in names
        assert "final_output" in names
        assert "ask_user" in names
        assert "workflow_run" not in names
        assert rows["family_off:workflow"]["count"] == 2
        assert rows["family_off:workflow"]["sample"] == ["workflow_run", "workflow_status"]

    def test_matched_family_stays(self):
        offered, rows, _u = select_with_family(tools=CATALOG, message_text="run my workflow")
        names = {t["name"] for t in offered}
        assert "workflow_run" in names
        assert "workflow_status" in names
        assert "knowledge_query" not in names
        assert "family_off:workflow" not in rows

    def test_unmatched_family_prefix_excluded_and_measured(self):
        catalog = CATALOG + [_tool("hpc_submit")]
        offered, rows, _u = select_with_family(tools=catalog, message_text="generic ask")
        names = {t["name"] for t in offered}
        assert "hpc_submit" not in names
        assert rows["family_off:hpc"]["count"] == 1

    def test_utility_floor_never_drops_unfamilied(self):
        """Review challenge 3: unfamilied utilities must stay offered
        (measured floor — the filter can only reach tracked families)."""
        catalog = CATALOG + [_tool("tool_search"), _tool("list_dir"), _tool("platform_guide")]
        offered, rows, utility = select_with_family(
            tools=catalog, message_text="generic ask about the moon"
        )
        names = {t["name"] for t in offered}
        for utility_name in ("tool_search", "list_dir"):
            assert utility_name in names
        assert "platform_guide" not in names  # 'platform' is a tracked family, unmatched here
        assert utility["count"] >= 2
        assert rows["family_off:platform"]["count"] == 1
        assert set(rows) <= {f"family_off:{fam}" for fam in _FAMILY_PREFIXES}


def _assert_family_record_matches(record: dict, catalog: list[dict]) -> None:
    """Shared assertions for family-record shape (complexity budget)."""
    assert any(key.startswith("family_off:") for key in record["filtered_out"])
    # Family buckets only: without a live platform the availability gate drops the
    # workflow_* tools too (2026-10-06), which is not the family strategy's doing.
    removed_names = {
        name
        for key, bucket in record["filtered_out"].items()
        if key.startswith("family_off:")
        for name in bucket.get("sample", [])
    }
    assert "workflow_run" not in removed_names  # workflow was mentioned
    assert record["available_tools"] == len(catalog) > record["candidate_tools"]


# End-to-end through the agent, env flag on
def _chat_response(content):
    message = {"content": content, "role": "assistant"}
    return MagicMock(
        content=content,
        model="test-model",
        usage=MagicMock(prompt_tokens=10, completion_tokens=2, total_tokens=12),
        raw_response={"choices": [{"message": message, "finish_reason": "stop"}]},
    )


class TestAgentFamilyStrategy:
    def test_env_flag_family_records_strategy_and_removals(self, monkeypatch):
        from unittest.mock import MagicMock as _MM

        mock_client = _MM()
        mock_client._config.model = "test-model"
        mock_client._request.return_value = _chat_response("done")

        monkeypatch.setenv("R4_TOOL_SELECTION_STRATEGY", "family")
        agent = ResearchCompanion(
            mock_client,
            context_manager=ContextManager(client=mock_client),
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        captured: list = []
        agent.on_event(captured.append)
        agent.send("help me with a workflow now", tools=CATALOG, stream=False)

        selections = [e for e in captured if e.event_type is CompanionEventType.TOOL_SELECTION]
        assert selections, "expected at least one selection event"
        record = selections[0].data
        assert record["strategy"] == "family"
        _assert_family_record_matches(record, CATALOG)

    def test_passthrough_default_unaffected(self, monkeypatch):
        from unittest.mock import MagicMock as _MM

        mock_client = _MM()
        mock_client._config.model = "test-model"
        mock_client._request.return_value = _chat_response("done")

        monkeypatch.delenv("R4_TOOL_SELECTION_STRATEGY", raising=False)
        agent = ResearchCompanion(
            mock_client,
            context_manager=ContextManager(client=mock_client),
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        )
        captured: list = []
        agent.on_event(captured.append)
        agent.send("help me", tools=CATALOG, stream=False)
        selections = [e for e in captured if e.event_type is CompanionEventType.TOOL_SELECTION]
        assert selections
        record = selections[0].data
        assert record["strategy"] == "passthrough"
        assert not any(k.startswith("family_off:") for k in record["filtered_out"])
