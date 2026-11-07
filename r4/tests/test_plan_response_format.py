"""Provider ``response_format`` is an optimisation only (ADR-092 slice 9).

Where the route honours it, the broker's one repair call carries the step's
schema as an OpenAI ``json_schema`` response format; the reply is validated
by the stdlib validator exactly as a plain reply is, so the result is the
same whether or not the format was used. A route that does not honour it,
or a call that fails with it, runs plain (logged). Fakes as the broker's own
tests use them.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from test_plan_agent_broker import SCHEMA, _answer, _broker, _Client, _request

from r4.chat_types import ChatResponse
from r4.client import Client


class FormatClient(_Client):
    """``_Client`` whose route honours (or not) ``response_format``."""

    def __init__(self, replies, *, honours=True, refuse_format=False, on_chat=None):
        super().__init__(replies)
        self.honours = honours
        self.refuse_format = refuse_format
        self.on_chat = on_chat

    def honours_response_format(self, model, *, cancellable=True):
        return self.honours

    def chat(self, **kwargs: Any) -> ChatResponse:
        if self.on_chat is not None:
            self.on_chat(kwargs)
        if self.refuse_format and "response_format" in kwargs:
            self.chats.append(kwargs)
            raise RuntimeError("400: response_format is not supported by this model")
        return super().chat(**kwargs)


def _outcome(result):
    return (result.ok, result.output, result.repaired, result.reason.split(":")[0])


@pytest.mark.parametrize(
    "reply",
    [
        '{"findings": ["fixed"]}',  # valid
        '{"findings": "still not a list"}',  # schema-invalid: a named null either way
        "no JSON here",  # no value: a named null either way
        '```json\n{"findings": []}\n```',  # fenced: parsed the same way
    ],
)
async def test_the_result_is_the_same_with_or_without_the_format(reply):
    outcomes = []
    for honours in (True, False):
        client = FormatClient([reply], honours=honours)
        broker, _meter, _f, _c = _broker(_answer('{"findings": "bad"}'), client=client)
        outcomes.append(_outcome(await broker.run_turn(_request(schema=SCHEMA))))
        assert len(client.chats) == 1
        assert ("response_format" in client.chats[0]) is honours
    assert outcomes[0] == outcomes[1]


async def test_the_repair_call_carries_the_steps_schema_where_honoured():
    client = FormatClient(['{"findings": ["fixed"]}'])
    broker, meter, _f, _c = _broker(_answer('{"findings": "bad"}'), client=client)
    result = await broker.run_turn(_request(schema=SCHEMA))
    (call,) = client.chats
    assert call["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "plan_step_output", "schema": SCHEMA, "strict": False},
    }
    assert (result.ok, result.output, meter.spent) == (True, {"findings": ["fixed"]}, 50)


async def test_a_refused_format_falls_back_to_plain_and_says_so(caplog):
    caplog.set_level(logging.INFO, logger="r4.teams.agent_broker")
    client = FormatClient(['{"findings": ["fixed"]}'], refuse_format=True)
    broker, _meter, _f, _c = _broker(_answer('{"findings": "bad"}'), client=client)
    result = await broker.run_turn(_request(schema=SCHEMA))
    assert [("response_format" in c) for c in client.chats] == [True, False]
    assert (result.ok, result.output, result.repaired) == (True, {"findings": ["fixed"]}, True)
    assert "response_format refused (RuntimeError); repairing plain" in caplog.text


async def test_a_cancelled_turn_is_not_repaired_plain():
    holder: dict[str, Any] = {}
    client = FormatClient(
        ['{"findings": ["fixed"]}'],
        refuse_format=True,
        on_chat=lambda _kw: holder["broker"].cancel("stop"),
    )
    broker, _meter, _f, _c = _broker(_answer('{"findings": "bad"}'), client=client)
    holder["broker"] = broker
    result = await broker.run_turn(_request(schema=SCHEMA))
    assert result.ok is False and len(client.chats) == 1


async def test_a_step_without_a_schema_never_sends_a_format():
    client = FormatClient([])
    broker, _meter, _f, _c = _broker(_answer("plain answer"), client=client)
    result = await broker.run_turn(_request())
    assert (result.ok, result.text, client.chats) == (True, "plain answer", [])


# ── the client: which routes honour it, and the payload ────────────────────


def _client_self(provider: Any, model: str = "m"):
    return SimpleNamespace(
        _config=SimpleNamespace(model=model),
        _resolve_gated_route=lambda _m: SimpleNamespace(kind="public", source_name="s"),
        _provider_for_route=lambda _r: provider,
        _should_bypass_provider_for_cancel=Client._should_bypass_provider_for_cancel,
    )


def test_which_routes_honour_it(monkeypatch):
    from r4.providers.openai_compat import OpenAICompatibleProvider

    compat = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    other = object()
    honours = Client.honours_response_format
    assert honours(_client_self(None), "m") is True  # the HTTP path posts it verbatim
    assert honours(_client_self(compat), "m") is True  # a cancellable call rides HTTP
    assert honours(_client_self(compat), "m", cancellable=False) is False
    assert honours(_client_self(other), "m") is False  # a provider drops unknown keys
    monkeypatch.setenv("R4_RESPONSE_FORMAT", "off")
    assert honours(_client_self(None), "m") is False


def test_the_payload_carries_it_only_when_given():
    sent: list[dict[str, Any]] = []
    fake = SimpleNamespace(
        _config=SimpleNamespace(model="m"),
        _request=lambda payload, **_kw: sent.append(payload) or "ok",
    )
    from r4.chat_types import ChatMessage

    messages = [ChatMessage.user("hi")]
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    Client.chat_completion(fake, messages, response_format=fmt)
    Client.chat_completion(fake, messages)
    assert sent[0]["response_format"] == fmt and "response_format" not in sent[1]
