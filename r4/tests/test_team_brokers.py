"""Client-side team brokers (ADR-020 §1, ADR-092 slice-1 fixes).

Pins the three broker defects the plan engine leans on: the model broker's
usage read, the token meter under parallel agents, and the event log's
sequence across resumed attempts.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from r4_contracts.team_engine.services import ModelRequest
from r4_contracts.team_engine.spec import BudgetSpec
from r4_teamengine_spi.errors import BudgetExhausted

from r4.chat_types import ChatResponse, Usage
from r4.teams.brokers import ClientModelBroker, FileEventSink, TokenBudgetMeter


class _UsageClient:
    """Answers like ``Client.chat``: a ChatResponse carrying the Usage dataclass."""

    def __init__(self, usage) -> None:
        self._usage = usage

    def chat(self, **_kwargs):
        response = ChatResponse(content="three word answer", model="m")
        response.usage = self._usage
        return response


async def _drain(broker: ClientModelBroker) -> str:
    request = ModelRequest(messages=({"role": "user", "content": "hi"},))
    return "".join([chunk.text async for chunk in broker.stream(request)])


class TestClientModelBroker:
    async def test_client_model_broker_accounts_usage_dataclass(self) -> None:
        usage = Usage(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        broker = ClientModelBroker(_UsageClient(usage))
        assert await _drain(broker) == "three word answer"
        assert (broker.llm_calls, broker.input_tokens, broker.output_tokens) == (1, 11, 7)

    async def test_dict_usage_still_accounted(self) -> None:
        broker = ClientModelBroker(_UsageClient({"prompt_tokens": 5, "completion_tokens": 2}))
        await _drain(broker)
        assert (broker.input_tokens, broker.output_tokens) == (5, 2)

    async def test_missing_usage_falls_back_to_word_count(self) -> None:
        broker = ClientModelBroker(_UsageClient(None))
        await _drain(broker)
        assert (broker.input_tokens, broker.output_tokens) == (0, 3)


class TestUsageTokens:
    def test_reads_dataclass_dict_and_none(self) -> None:
        from r4.teams.brokers import usage_tokens

        assert usage_tokens(Usage(prompt_tokens=4), "prompt_tokens") == 4
        assert usage_tokens({"completion_tokens": "6"}, "completion_tokens") == 6
        assert usage_tokens(None, "prompt_tokens") == 0
        assert usage_tokens({}, "prompt_tokens") == 0


class TestTokenBudgetMeter:
    def test_token_meter_is_thread_safe_under_concurrent_spends(self) -> None:
        meter = TokenBudgetMeter(BudgetSpec(max_tokens=None))
        threads, spends = 8, 5000
        barrier = threading.Barrier(threads)

        def _spend() -> None:
            barrier.wait()
            for _ in range(spends):
                meter.spend_tokens(1)

        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # force interleaving inside the read-modify-write
        try:
            workers = [threading.Thread(target=_spend) for _ in range(threads)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            sys.setswitchinterval(previous)
        assert meter.spent == threads * spends
        assert meter.remaining() == {"max_tokens": None}

    def test_spend_past_ceiling_raises_and_remaining_clamps(self) -> None:
        meter = TokenBudgetMeter(BudgetSpec(max_tokens=10))
        meter.spend_tokens(6)
        assert meter.remaining() == {"max_tokens": 4}
        with pytest.raises(BudgetExhausted):
            meter.spend_tokens(5)
        assert meter.spent == 11
        assert meter.remaining() == {"max_tokens": 0}


class TestFileEventSink:
    def test_file_event_sink_continues_seq_from_existing_log(self, tmp_path: Path) -> None:
        first = FileEventSink("run-1", tmp_path)
        seqs = [first.emit("agent_message", ("engine", "plan"), {"n": i}).seq for i in range(3)]
        assert seqs == [0, 1, 2]

        resumed = FileEventSink("run-1", tmp_path)  # attempt 2, same run directory
        event = resumed.emit("run_started", ("engine", "plan"), {})
        assert event.seq == 3
        lines = (tmp_path / "events.jsonl").read_text().splitlines()
        assert len(lines) == 4

    def test_fresh_run_starts_at_zero(self, tmp_path: Path) -> None:
        sink = FileEventSink("run-2", tmp_path)
        assert sink.emit("run_started", ("engine", "native"), {}).seq == 0

    def test_concurrent_emits_get_distinct_seq(self, tmp_path: Path) -> None:
        sink = FileEventSink("run-3", tmp_path)

        def _emit() -> None:
            for _ in range(50):
                sink.emit("agent_message", ("agent", "a"), {})

        workers = [threading.Thread(target=_emit) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        seqs = sorted(e.seq for e in sink.events)
        assert seqs == list(range(200))
