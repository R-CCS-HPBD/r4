"""An empty model reply is said, not stored as "" (companion sweep 2026-10-21).

A provider that answered 200 with no content (or only [DONE]) ended the
turn "successfully" with an empty bubble and an empty stored answer.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.agent import EMPTY_REPLY_NOTE, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


def _empty_client() -> MagicMock:
    client = MagicMock()
    client._config.model = "test-model"
    client._request.return_value = MagicMock(
        content="",
        model="test-model",
        usage=MagicMock(prompt_tokens=3, completion_tokens=0, total_tokens=3),
        raw_response={
            "choices": [{"message": {"content": "", "role": "assistant"}, "finish_reason": "stop"}]
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


def test_empty_reply_without_tools_is_named() -> None:
    assert _agent(_empty_client()).send("hi", stream=False) == EMPTY_REPLY_NOTE


def test_a_real_reply_is_untouched() -> None:
    client = _empty_client()
    client._request.return_value.raw_response["choices"][0]["message"]["content"] = "hello"
    client._request.return_value.content = "hello"
    assert _agent(client).send("hi", stream=False) == "hello"
