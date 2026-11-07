"""Live reasoning reaches the TUI's Thought block (TUI audit r2 #22, 2026-10-17).

The JSONL bridge never emitted thinking_delta/thinking_done (only the ACP rail
did), so v3's Thought block stayed empty: reasoning_content deltas were only
stored on the round. The agent now emits reasoning_delta per delta and one
reasoning_done per round; the bridge maps them to the canonical envelopes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.ui import bridge_server as bs


class _ReasoningClient:
    def __init__(self, reasoning: list[str], text: list[str]):
        self._config = MagicMock(model="test-model", endpoint_url="")
        self._reasoning = reasoning
        self._text = text
        self.order: list[str] = []

    def _stream_request_collect(self, payload, acc, *, reasoning_acc=None, turn_cancel=None, **_kw):
        for piece in self._reasoning:
            self.order.append(f"r:{piece}")
            reasoning_acc.append(piece)  # what client._collect_reasoning does
        for chunk in self._text:
            self.order.append(f"t:{chunk}")
            yield chunk


def _events(agent: ResearchCompanion) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    agent.on_event(lambda e: seen.append((e.event_type.value, e.message)))
    return seen


def test_reasoning_streams_live_and_closes_once():
    agent = ResearchCompanion(
        _ReasoningClient(["Let me ", "think."], ["Answer"]), model="m", call_type="main"
    )
    seen = _events(agent)
    payload = agent._stream_agentic_round([{"role": "user", "content": "q"}], None)
    kinds = [kind for kind, _ in seen]
    deltas = [msg for kind, msg in seen if kind == "reasoning_delta"]
    assert deltas == ["Let me ", "think."]
    assert kinds.count("reasoning_done") == 1
    # live: the deltas precede the answer chunk, the block closes after it
    assert (
        kinds.index("reasoning_delta")
        < kinds.index("response_chunk")
        < kinds.index("reasoning_done")
    )
    # the round still carries the whole reasoning
    assert payload["choices"][0]["message"]["reasoning_content"] == "Let me think."


def test_no_reasoning_means_no_thought_events():
    agent = ResearchCompanion(_ReasoningClient([], ["Answer"]), model="m", call_type="main")
    seen = _events(agent)
    agent._stream_agentic_round([{"role": "user", "content": "q"}], None)
    assert not [kind for kind, _ in seen if kind.startswith("reasoning")]


def _bare_server():
    server = bs.CompanionBridgeServer.__new__(bs.CompanionBridgeServer)
    server._session = SimpleNamespace(session_id="s-1")
    server._event_log = MagicMock()
    lines: list[dict] = []
    server._emit_line = lines.append
    return server, lines


def _event(kind: CompanionEventType, message: str = ""):
    return SimpleNamespace(event_type=kind, message=message, data={})


def test_bridge_maps_reasoning_to_canonical_thinking_envelopes():
    server, lines = _bare_server()
    server._forward_event(_event(CompanionEventType.REASONING_DELTA, "Let me "))
    server._forward_event(_event(CompanionEventType.REASONING_DELTA, "think."))
    server._forward_event(_event(CompanionEventType.REASONING_DONE))
    server._forward_event(_event(CompanionEventType.REASONING_DELTA, "Next round"))
    assert [line["type"] for line in lines] == [
        "thinking_delta",
        "thinking_delta",
        "thinking_done",
        "thinking_delta",
    ]
    assert [line["payload"].get("seq") for line in lines] == [0, 1, None, 0]
    assert lines[0]["payload"]["text"] == "Let me " and lines[0]["payload"]["session_id"] == "s-1"
    # one line per streamed token must not swamp the persisted event log
    server._event_log.append.assert_not_called()
