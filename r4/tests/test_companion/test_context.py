"""Tests for ContextManager."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.context import ContextManager


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.chat.return_value = MagicMock(
        content="Summary of conversation.",
        model="test-model",
        usage=MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )
    return client


@pytest.fixture
def ctx(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("R4_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("R4_COMPANION_MEMORY_DIR", str(tmp_path / "legacy"))
    return ContextManager(client=mock_client, max_context_tokens=1000, response_reserve_tokens=100)


def test_estimate_tokens():
    ctx = ContextManager(client=MagicMock())
    tokens = ctx.estimate_tokens("Hello world")
    assert tokens > 0
    assert isinstance(tokens, int)


def test_needs_compaction(ctx):
    # Under threshold
    messages = [{"role": "user", "content": "Hi"}]
    assert not ctx.needs_compaction(messages)

    # Over threshold (80% of 1000 tokens ≈ 2800 chars)
    big_msg = {"role": "user", "content": "x" * 3500}
    assert ctx.needs_compaction([big_msg])


def test_estimate_messages_tokens_counts_tool_calls_and_reasoning(ctx):
    """Tool-call arguments and reasoning echoes ride the wire on every
    agentic round — the estimator must see them, or budget fitting and
    compaction triggers stay blind until the provider 400s."""
    import json

    args = {"path": "src/foo.py", "old_text": "x\n" * 500, "new_text": "y\n" * 500}
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "file_edit", "arguments": json.dumps(args)},
                }
            ],
            "reasoning_content": "thinking... " * 200,
        },
        {"role": "tool", "tool_call_id": "c1", "content": "edited ok"},
    ]
    estimate = ctx.estimate_messages_tokens(messages)
    wire_floor = len(json.dumps(messages)) / 3.5
    # Must land in the same order of magnitude as the serialized payload —
    # the old content-only estimate was ~500x under on this shape.
    assert estimate > wire_floor * 0.5
    assert estimate > ctx.estimate_messages_tokens(
        [{"role": "assistant", "content": ""}, {"role": "tool", "content": "edited ok"}]
    )


def test_estimate_messages_tokens_handles_multimodal_content(ctx):
    """Content may be a part list (multimodal) — estimate, don't crash."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in this image?"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        }
    ]
    tokens = ctx.estimate_messages_tokens(messages)
    assert tokens > 4


def test_compact(ctx, mock_client):
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second question"},
        {"role": "assistant", "content": "Second answer"},
        {"role": "user", "content": "Third question"},
        {"role": "assistant", "content": "Third answer"},
        {"role": "user", "content": "Fourth question"},
        {"role": "assistant", "content": "Fourth answer"},
    ]
    result = ctx.compact(messages, keep_recent=2)
    # Should contain system + summary + 2 recent
    assert result[0]["role"] == "system"
    assert len(result) <= 5  # system + summary + 2 pairs max


def test_memory_crud(ctx, tmp_path):
    ctx.remember("name", "Alice", source="user")
    assert ctx.get_memory("name") == "Alice"

    items = ctx.list_memories()
    assert len(items) == 1

    ctx.forget("name")
    assert ctx.get_memory("name") is None
    assert len(ctx.list_memories()) == 0


def test_memory_persistence(ctx, tmp_path, mock_client, monkeypatch):
    ctx.remember("key1", "value1", source="user")

    # Create new context manager, should load persisted memory
    ctx2 = ContextManager(client=mock_client)
    item = ctx2.get_memory("key1")
    assert item is not None
    assert item == "value1"


def test_memory_as_context(ctx):
    ctx.remember("tool", "SPECFEM3D", source="agent")
    ctx.remember("domain", "seismology", source="user")
    prompt = ctx.memory_as_context()
    assert "seismology" in prompt
    assert "SPECFEM3D" in prompt
    # ADR-111: a capped index, one line per fact, never the bodies alone.
    lines = prompt.splitlines()
    assert lines[0].startswith("[Saved memory:")
    assert lines[1:] == [
        "## project",
        "- domain [project]: domain: seismology",
        "- tool [project]: tool: SPECFEM3D",
    ]


def test_session_context(ctx):
    ctx.set_session_context("toolchain", "slurm")
    ctx.set_session_context("project", "earthquake-sim")
    prompt = ctx.session_context_as_prompt()
    assert "earthquake-sim" in prompt
    assert prompt.splitlines() == [
        "[Session context]",
        "- project: earthquake-sim",
        "- toolchain: slurm",
    ]

    ctx.clear_session_context()
    assert ctx.session_context_as_prompt() == ""


def test_estimate_payload_budget_includes_all_components(ctx):
    history = [
        {"role": "user", "content": "history " * 40},
        {"role": "assistant", "content": "response " * 40},
    ]
    tools = [
        {
            "name": "file_read",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            "execute": lambda path="": "ignored",
        }
    ]

    budget = ctx.estimate_payload_budget(
        system_prompt="system prompt",
        extra_system="extra guidance",
        history=history,
        user_message="what happened?",
        tools=tools,
    )

    assert budget.system_tokens > 0
    assert budget.extra_system_tokens > 0
    assert budget.history_tokens > 0
    assert budget.user_tokens > 0
    assert budget.tool_tokens > 0
    assert budget.reserve_tokens == 100


def test_fit_payload_trims_extra_system_and_history(ctx):
    history = [
        {"role": "user", "content": "old context " * 80},
        {"role": "assistant", "content": "assistant context " * 80},
        {"role": "user", "content": "recent context " * 80},
        {"role": "assistant", "content": "recent answer " * 80},
    ]
    extra_system = "guide line\n" * 250

    fitted_extra_system, fitted_history, fitted_tools, budget = ctx.fit_payload(
        system_prompt="system prompt",
        extra_system=extra_system,
        history=history,
        user_message="short question",
        tools=None,
    )

    assert len(fitted_history) <= len(history)
    assert len(fitted_extra_system) <= len(extra_system)
    assert fitted_tools == []
    assert not budget.hard_limit_exceeded
