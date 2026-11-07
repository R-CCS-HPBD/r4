"""W4 context fixes (2026-10-05): reasoning window, window resolver, tool_result_fetch,
published endpoint limits."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from r4.companion.agent import (
    _REASONING_PLACEHOLDER,
    TOOL_RESULT_FETCH,
    ResearchCompanion,
    _bound_reasoning_history,
)
from r4.companion.context_budget import endpoint_model_limits, sync_session_context_budget


def _agent(model: str = "glm-5.3@rikyu") -> ResearchCompanion:
    return ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)


def _rounds(n: int) -> list[dict]:
    msgs = [{"role": "user", "content": "u"}]
    for i in range(n):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": f"r{i}",
                "tool_calls": [{"id": f"c{i}"}],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    return msgs


class TestReasoningWindow:
    def test_only_latest_rounds_keep_reasoning(self) -> None:
        out = _bound_reasoning_history(_rounds(4), keep=2)
        reasoning = [m["reasoning_content"] for m in out if m.get("role") == "assistant"]
        assert reasoning == [_REASONING_PLACEHOLDER, _REASONING_PLACEHOLDER, "r2", "r3"]

    def test_keep_all_and_short_histories_untouched(self) -> None:
        msgs = _rounds(3)
        assert _bound_reasoning_history(msgs, keep=-1) == msgs
        assert _bound_reasoning_history(msgs, keep=5) == msgs

    def test_payload_applies_window_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("R4_COMPANION_REASONING_KEEP", "1")
        payload = _agent()._chat_payload(_rounds(3), None)
        kept = [m["reasoning_content"] for m in payload["messages"] if m.get("role") == "assistant"]
        assert kept == [_REASONING_PLACEHOLDER, _REASONING_PLACEHOLDER, "r2"]


class TestWindowResolver:
    def test_env_then_published_then_table(self, monkeypatch) -> None:
        agent = _agent()
        monkeypatch.delenv("R4_MODEL_CONTEXT_WINDOW", raising=False)
        assert agent._context_window() == 1_048_576  # glm-5 name table
        agent.model_context_window = 200_000
        assert agent._context_window() == 200_000
        monkeypatch.setenv("R4_MODEL_CONTEXT_WINDOW", "65536")
        assert agent._context_window() == 65_536

    def test_history_budget_is_capped(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_MODEL_CONTEXT_WINDOW", raising=False)
        agent = _agent()
        big = agent._tool_history_budget_chars()
        agent.model_context_window = 32_768
        assert agent._tool_history_budget_chars() < big


class TestToolResultFetch:
    def test_offered_beside_tools_and_reads_journal(self) -> None:
        agent = _agent()
        tools = agent._with_tool_result_fetch([{"name": "shell_exec"}])
        fetch = [t for t in tools if t["name"] == TOOL_RESULT_FETCH][0]
        assert fetch["concurrency_safe"] is True
        agent._record_tool_result("call_1", "shell_exec", "A" * 50 + "B" * 50)
        page = fetch["execute"](tool_call_id="call_1", offset=0, limit=50)
        assert "A" * 50 in page and "offset=50" in page
        assert "B" * 50 in fetch["execute"](tool_call_id="call_1", offset=50)
        assert "no stored result" in fetch["execute"](tool_call_id="missing")

    def test_not_added_to_an_empty_offer(self) -> None:
        assert _agent()._with_tool_result_fetch([]) == []


class _Client:
    def __init__(self, endpoints):
        self._endpoints = endpoints

    def list_endpoints(self, include_stopped=False):
        return {"endpoints": self._endpoints}


class TestPublishedLimits:
    def test_limits_from_listing(self) -> None:
        client = _Client([{"model": "m1", "context_window": 32_768, "max_output_tokens": 8_192}])
        assert endpoint_model_limits(client, "m1") == {
            "context_window": 32_768,
            "max_output_tokens": 8_192,
        }
        legacy = _Client([{"model": "m2", "max_model_len": 16_384}])
        assert endpoint_model_limits(legacy, "m2") == {
            "context_window": 16_384,
            "max_output_tokens": None,
        }
        assert endpoint_model_limits(legacy, "other") == {
            "context_window": None,
            "max_output_tokens": None,
        }

    def test_sync_hands_limits_to_the_agent(self, monkeypatch) -> None:
        client = _Client([{"model": "m1", "context_window": 32_768, "max_output_tokens": 8_192}])
        agent = SimpleNamespace(model="m1")
        session = SimpleNamespace(
            client=client, agent=agent, context=SimpleNamespace(max_context_tokens=0)
        )
        monkeypatch.setattr(
            "r4.companion.model_resolution.session_model_info", lambda _s: {"model": "m1"}
        )
        sync_session_context_budget(session)
        assert (agent.model_context_window, agent.model_output_limit) == (32_768, 8_192)
        assert session.context.max_context_tokens > 0

    def test_published_output_limit_reaches_the_payload(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
        agent = _agent("my-model")
        agent.model_context_window, agent.model_output_limit = 32_768, 8_192
        assert agent._chat_payload([{"role": "user", "content": "hi"}], None)["max_tokens"] == 8_192
        json.dumps(agent._chat_payload([{"role": "user", "content": "hi"}], None))


class _ModelsClient(_Client):
    """Endpoint listing without output limits plus a ``/v1/models`` route."""

    def __init__(self, endpoints, entries):
        super().__init__(endpoints)
        self._entries = entries
        self.model_calls = 0

    def model_entries(self):
        self.model_calls += 1
        return self._entries


class TestModelsRouteLimits:
    """W5/U3 seam: UMRS publishes limits on /v1/models, not on the listing."""

    def test_output_limit_from_models_route(self) -> None:
        client = _ModelsClient(
            [{"model": "m1", "max_model_len": 65_536}],
            [{"id": "m1", "context_window": 65_536, "max_output_tokens": 16_384}],
        )
        assert endpoint_model_limits(client, "m1") == {
            "context_window": 65_536,
            "max_output_tokens": 16_384,
        }

    def test_listing_wins_and_route_fills_gaps(self) -> None:
        client = _ModelsClient(
            [{"model": "m1", "context_window": 32_768, "max_output_tokens": 8_192}],
            [{"id": "m1", "context_window": 1, "max_output_tokens": 1}],
        )
        assert endpoint_model_limits(client, "m1")["max_output_tokens"] == 8_192
        assert client.model_calls == 0  # nothing unknown, no second lookup

    def test_public_source_matches_bare_model_id(self) -> None:
        client = _ModelsClient([], [{"id": "glm-5.3", "max_model_len": 202_752}])
        assert endpoint_model_limits(client, "glm-5.3@rikyu") == {
            "context_window": 202_752,
            "max_output_tokens": None,
        }

    def test_route_errors_and_junk_are_unknown(self) -> None:
        class _Broken(_Client):
            def model_entries(self):
                raise RuntimeError("route down")

        assert endpoint_model_limits(_Broken([]), "m1") == {
            "context_window": None,
            "max_output_tokens": None,
        }
        junk = _ModelsClient([], [{"id": "m1", "max_output_tokens": True}, "x"])
        assert endpoint_model_limits(junk, "m1")["max_output_tokens"] is None


def test_client_model_entries_cached(monkeypatch) -> None:
    from r4.client import Client
    from r4.config import Config

    client = Client(Config(gateway_url="http://gw.invalid", model="m1"))
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return {"data": [{"id": "m1", "max_output_tokens": 4_096}, "junk"]}

    monkeypatch.setattr(client.platform_http, "get", fake_get)
    assert client.model_entries() == [{"id": "m1", "max_output_tokens": 4_096}]
    assert client.model_entries() == [{"id": "m1", "max_output_tokens": 4_096}]
    assert len(calls) == 1 and calls[0].endswith("/v1/models")


class TestReplayedReasoningCap:
    """A kept round replays only the end of a huge reasoning (2026-10-05: one
    116k-token reasoning grew the next TB 4.0 prompt from 15k to 131k tokens)."""

    def test_kept_rounds_are_clipped_older_ones_placeheld(self) -> None:
        from r4.companion.agent import _REASONING_PLACEHOLDER, _bound_reasoning_history

        msgs = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": None, "reasoning_content": "old " * 10},
            {"role": "assistant", "content": None, "reasoning_content": "a" * 50_000 + "END"},
        ]
        out = _bound_reasoning_history(msgs, keep=1, max_chars=1_000)
        assert out[1]["reasoning_content"] == _REASONING_PLACEHOLDER
        kept = out[2]["reasoning_content"]
        assert kept.endswith("END")
        assert kept.startswith("[… 49,003 chars of earlier reasoning omitted …]")
        assert msgs[2]["reasoning_content"].startswith("aaa")  # input untouched

    def test_no_cap_and_keep_all(self) -> None:
        from r4.companion.agent import _bound_reasoning_history

        msgs = [{"role": "assistant", "content": None, "reasoning_content": "r" * 30_000}]
        assert _bound_reasoning_history(msgs, keep=-1, max_chars=0) == msgs
        assert (
            len(_bound_reasoning_history(msgs, keep=-1, max_chars=100)[0]["reasoning_content"])
            < 200
        )

    def test_payload_uses_the_default_cap(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_COMPANION_REASONING_MAX_CHARS", raising=False)
        agent = _agent("my-model")
        msgs = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "x", "reasoning_content": "r" * 200_000},
            {"role": "user", "content": "go on"},
        ]
        replayed = agent._chat_payload(msgs, None)["messages"][1]["reasoning_content"]
        assert 24_000 <= len(replayed) < 24_100
        monkeypatch.setenv("R4_COMPANION_REASONING_MAX_CHARS", "0")
        assert len(agent._chat_payload(msgs, None)["messages"][1]["reasoning_content"]) == 200_000


# --- Tool-call arguments in history (2026-10-06, TB 4.0 photonic: 390k of a
# 510k-char prompt were the model's own earlier heredoc/file_write arguments) ---


def _call_round(i: int, command: str) -> list[dict]:
    call = {
        "id": f"c{i}",
        "type": "function",
        "function": {"name": "shell_exec", "arguments": json.dumps({"command": command})},
    }
    return [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "ok"},
    ]


def test_old_long_tool_arguments_are_clipped_but_stay_valid_json() -> None:
    from r4.companion.agent import _bound_tool_call_args

    big = "cat > /app/solve.py <<'EOF'\n" + "x = 1\n" * 2000 + "EOF"
    messages = [{"role": "user", "content": "go"}, *_call_round(1, big), *_call_round(2, big)]
    out = _bound_tool_call_args(messages, keep_rounds=1, limit=2000)
    old = json.loads(out[1]["tool_calls"][0]["function"]["arguments"])["command"]
    assert len(old) < 2200 and old.startswith("cat > /app/solve.py") and old.endswith("EOF")
    assert "chars of this earlier call omitted" in old
    assert out[3] is messages[3]  # the latest round keeps its arguments
    assert (
        out[2] is messages[2]
        and messages[1]["tool_calls"][0]["function"]["arguments"]
        != (out[1]["tool_calls"][0]["function"]["arguments"])
    )


@pytest.mark.parametrize("limit", [0, 10_000])
def test_short_or_unbounded_arguments_are_untouched(limit: int) -> None:
    from r4.companion.agent import _bound_tool_call_args

    messages = [*_call_round(1, "make " * 500), *_call_round(2, "ls")]
    assert _bound_tool_call_args(messages, keep_rounds=1, limit=limit) == messages


def test_non_json_arguments_are_left_alone() -> None:
    from r4.companion.agent import _clip_call_arguments

    call = {"id": "c", "function": {"name": "shell_exec", "arguments": "not json " * 500}}
    assert _clip_call_arguments(call, 100) is call


# --- Old rounds (2026-10-07, TB 4.0 layout: 229 rounds kept a 2k head+tail each,
# ~150k-token prompts on every request) ---


def _tool_rounds(n: int, size: int = 3000) -> list[dict]:
    out: list[dict] = [{"role": "user", "content": "go"}]
    for i in range(n):
        call = {
            "id": f"c{i}",
            "type": "function",
            "function": {"name": "shell_exec", "arguments": json.dumps({"command": "x" * size})},
        }
        out += [
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "r" * size},
        ]
    return out


@pytest.mark.parametrize(("rounds", "compacted"), [(45, 0), (55, 10), (69, 20), (70, 30)])
def test_old_rounds_shrink_in_blocks_of_ten(rounds: int, compacted: int) -> None:
    from r4.companion.agent import _compact_old_rounds

    out = _compact_old_rounds(_tool_rounds(rounds), keep=40, limit=400)
    small = [m for m in out if m["role"] == "tool" and len(m["content"]) < 600]
    assert len(small) == compacted
    if compacted:
        assert 'tool_result_fetch(tool_call_id="c0")' in out[2]["content"]
        args = json.loads(out[1]["tool_calls"][0]["function"]["arguments"])["command"]
        assert len(args) < 600
    assert out[-1]["content"] == "r" * 3000  # recent rounds untouched


def test_old_round_tier_off() -> None:
    from r4.companion.agent import _compact_old_rounds

    messages = _tool_rounds(60)
    assert _compact_old_rounds(messages, keep=0) is messages
