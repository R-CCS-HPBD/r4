"""C3: session/cost payload contract + rail emission (acp_session, bridge)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.cost_event import cost_event_payload
from r4.cost import SessionCost


def _session_with_cost(turns, session_id="s-1"):
    cost = SessionCost()
    for t in turns:
        cost.record(**t)
    client = SimpleNamespace(session_cost=cost)
    record = SimpleNamespace(prompt_tokens=12_345)
    context = SimpleNamespace(max_context_tokens=131_072)
    return SimpleNamespace(
        session_id=session_id,
        client=client,
        usage_records=[record],
        context=context,
    )


def _turn(prompt, completion, price=0.0, model="qwen-ready"):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_per_1k": price,
        "model": model,
    }


class TestCostEventPayload:
    def test_no_turns_no_payload(self):
        session = SimpleNamespace(
            session_id="s-1",
            client=SimpleNamespace(session_cost=SessionCost()),
            usage_records=[],
            context=SimpleNamespace(max_context_tokens=0),
        )
        payload, cursor = cost_event_payload(session, 0)
        assert payload is None
        assert cursor == 0

    def test_first_turn_fields(self):
        session = _session_with_cost([_turn(9_000, 3_345, 0.0008)])
        envelope, cursor = cost_event_payload(session, 0)

        assert envelope["type"] == "cost"
        payload = envelope["payload"]
        assert payload["session_id"] == "s-1"
        assert payload["model"] == "qwen-ready"
        assert payload["turn_tokens"] == 12_345
        assert payload["total_tokens"] == 12_345
        assert payload["cost_usd_total"] == round(12.345 * 0.0008, 6)
        assert payload["cost_usd_turn"] == payload["cost_usd_total"]
        assert payload["price_known"] is True
        assert payload["context_used_tokens"] == 12_345
        assert payload["context_limit_tokens"] == 131_072
        assert cursor == 1

    def test_envelope_round_trips_control_schema(self):
        from r4.companion.control_schema import SessionCost, from_dict

        session = _session_with_cost([_turn(1_000, 100, 0.001), _turn(2_000, 100, 0.001)])
        envelope, _ = cost_event_payload(session, 0)
        decoded = from_dict(envelope)
        assert isinstance(decoded, SessionCost)
        assert decoded.session_id == "s-1"
        assert decoded.turn_tokens == 3_200

    def test_cursor_delta_sums_agentic_rounds(self):
        turns = [_turn(8_000, 2_000), _turn(4_000, 1_000), _turn(6_000, 1_000)]
        session = _session_with_cost(turns)
        _, cursor = cost_event_payload(session, 0)
        second, next_cursor = cost_event_payload(session, cursor)
        payload = second["payload"]

        # Second emission: no new turns → payload reports only the LAST turn
        # as the minimum token slice (goes through "or last" fallback).
        assert next_cursor == cursor
        assert payload["turn_tokens"] == 7_000  # falls back to last turn tokens
        assert payload["cost_usd_turn"] == 0.0

        # Simulate a new agentic round appended after last emission
        session.client.session_cost.record(
            prompt_tokens=100, completion_tokens=100, cost_per_1k=0.0, model="qwen-ready"
        )
        third, next_cursor = cost_event_payload(session, next_cursor)
        assert third["payload"]["turn_tokens"] == 200
        assert next_cursor == 4

    def test_free_provider_reports_unknown_price(self):
        session = _session_with_cost([_turn(1_000, 100, 0.0)])
        envelope, _ = cost_event_payload(session, 0)
        assert envelope["payload"]["price_known"] is False
        assert envelope["payload"]["cost_usd_turn"] == 0.0
        assert envelope["payload"]["cost_usd_total"] == 0.0

    def test_missing_context_limit_falls_back(self):
        session = SimpleNamespace(
            session_id="s",
            client=SimpleNamespace(session_cost=SessionCost()),
            usage_records=[],
            context=None,
        )
        session.client.session_cost.record(
            prompt_tokens=10, completion_tokens=5, cost_per_1k=0.0, model="m"
        )
        envelope, _ = cost_event_payload(session, 0)
        assert envelope["payload"]["context_limit_tokens"] == 128_000


class TestAcpSessionCostEmission:
    def test_response_done_emits_cost_payload(self):
        from r4.companion.acp_session import CompanionAcpSession
        from r4.companion.agent import CompanionEvent, CompanionEventType

        listeners = []
        session = _session_with_cost([_turn(1_000, 200, 0.001)])
        session.agent = SimpleNamespace(on_event=lambda cb: None)
        session.metadata = {}

        acp = object.__new__(CompanionAcpSession)
        acp._session = session
        acp._event_callback = listeners.append
        acp._stream_callback = None
        acp._on_event = None

        acp._handle_companion_event(CompanionEvent(CompanionEventType.RESPONSE_DONE, ""))
        assert any(envelope.get("type") == "cost" for envelope in listeners)
        cost = next(p["payload"] for p in listeners if p.get("type") == "cost")
        assert cost["session_id"] == "s-1"
        assert cost["turn_tokens"] == 1_200

    def test_no_callback_no_crash(self):
        from r4.companion.acp_session import CompanionAcpSession
        from r4.companion.agent import CompanionEvent, CompanionEventType

        session = _session_with_cost([_turn(1_000, 200)])
        session.agent = SimpleNamespace(on_event=lambda cb: None)
        session.metadata = {}
        acp = object.__new__(CompanionAcpSession)
        acp._session = session
        acp._event_callback = None
        acp._stream_callback = None
        acp._on_event = None
        acp._handle_companion_event(CompanionEvent(CompanionEventType.RESPONSE_DONE, ""))


class TestBridgeCostEmission:
    def test_send_message_emits_cost_line(self, monkeypatch):
        from r4.companion.ui import bridge_server as mod
        from r4.companion.ui.bridge_server import CompanionBridgeServer

        session = _session_with_cost([_turn(1_000, 200, 0.001)])
        session.name = "Demo"
        session.agent = SimpleNamespace(model="test-model", on_event=lambda cb: None)
        session.messages = []
        session.skills = MagicMock()
        session.metadata = {}
        session.permissions = MagicMock()
        session.context = MagicMock(_memory={})
        session.active_endpoints = []
        session.usage_records = [SimpleNamespace(prompt_tokens=1_000)]
        session.usage_report = MagicMock(return_value={})
        session.export_markdown = MagicMock(return_value="")
        session._turn_cancel = None
        session.send = MagicMock(return_value="done")
        session.created_at = "2026-01-01T00:00:00"
        session.updated_at = "2026-01-01T00:00:00"
        session.request_turn_cancel = MagicMock()
        session._harness = None
        session.client = SimpleNamespace(
            session_cost=session.client.session_cost,
            config=SimpleNamespace(
                profile="default",
                gateway_url="http://gateway.test",
                project_id="demo",
                model="config-model",
                endpoint_url=None,
                endpoint_provider=None,
                timeout=15.0,
            ),
            readiness=MagicMock(return_value={"ready": True}),
            list_endpoints=MagicMock(
                return_value={
                    "endpoints": [
                        {"instance_id": "ep-test", "status": "ready", "model": "test-model"}
                    ]
                }
            ),
        )

        emitted = []
        client = SimpleNamespace(
            config=SimpleNamespace(
                profile="default",
                gateway_url="http://g",
                project_id="p",
                model="m",
                endpoint_url=None,
                endpoint_provider=None,
                timeout=15.0,
            ),
            readiness=MagicMock(return_value={"ready": True}),
            list_endpoints=MagicMock(return_value={"endpoints": []}),
        )

        server = CompanionBridgeServer(client=client, store=MagicMock())
        server._session = session
        monkeypatch.setattr(mod, "ensure_model_ready", lambda _session: None)
        monkeypatch.setattr(server, "_emit_budget_state", lambda: None)
        monkeypatch.setattr(server, "_emit_line", lambda payload: emitted.append(payload))

        server.send_message({"text": "hello"})

        costs = [e for e in emitted if e.get("type") == "cost"]
        assert costs and costs[0]["payload"]["session_id"] == "s-1"
        assert costs[0]["payload"]["turn_tokens"] == 1_200
