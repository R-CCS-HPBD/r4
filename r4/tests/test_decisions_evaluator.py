"""Decision model evaluation over a fake /v1/completions (ADR-076)."""

from __future__ import annotations

import json
import math
import threading

import pytest

from r4.companion.decision_tools import build_decision_tools
from r4.decisions import DecisionError, DecisionModel, decision_model_for
from r4.decisions.evaluator import candidate_logprobs


class FakeClient:
    """Answers each completion with logprobs chosen by the requested field."""

    def __init__(self, logprobs_by_field, decision_model="Bespoke-Nimble-9B"):
        self.logprobs_by_field = logprobs_by_field
        self.payloads = []
        self.config = type("Cfg", (), {"decision_model": decision_model})()
        self._lock = threading.Lock()

    def text_completion(self, payload, *, timeout=None):
        with self._lock:
            self.payloads.append(payload)
        field = payload["prompt"].rsplit('Requested field: "', 1)[1].split('"', 1)[0]
        letters = self.logprobs_by_field[field]
        return {
            "choices": [{"text": "A", "logprobs": {"top_logprobs": [letters]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1},
        }


QUESTIONS = {
    "refund": {"type": "noul", "instructions": "Does the user request a refund?"},
    "team": {
        "type": "choice",
        "instructions": "Which team?",
        "criteria": {"billing": "Charges", "technical": "Bugs"},
    },
    "urgency": {"type": "score", "instructions": "How urgent?", "criteria": ["low", "mid", "high"]},
}


def _model(**logprobs):
    client = FakeClient(logprobs)
    return DecisionModel(client, "Bespoke-Nimble-9B"), client


def test_typed_answers_from_candidate_logprobs():
    dm, client = _model(
        refund={"A": math.log(0.1), "B": math.log(0.9)},
        team={"A": math.log(0.8), "B": math.log(0.2)},
        urgency={"A": math.log(0.5), "B": math.log(0.3), "C": math.log(0.2)},
    )
    ev = dm.evaluate("I was charged twice", QUESTIONS)
    assert ev.answers["refund"] == {"type": "noul", "noul": pytest.approx(0.9)}
    team = ev.answers["team"]
    assert team["choice"] == "billing"
    assert team["probabilities"] == {"billing": pytest.approx(0.8), "technical": pytest.approx(0.2)}
    assert 0 < team["confidence"] < 1
    urgency = ev.answers["urgency"]
    assert urgency["score"] == pytest.approx(0 * 0.5 + 1 * 0.3 + 2 * 0.2)
    assert urgency["legend"] == {"0": "low", "1": "mid", "2": "high"}
    assert ev.usage == {"input_tokens": 300, "output_tokens": 3}


def test_request_scores_candidates_without_generating():
    dm, client = _model(refund={"A": -1.0, "B": -0.5})
    dm.evaluate("x", {"refund": QUESTIONS["refund"]})
    (payload,) = client.payloads
    assert payload["model"] == "Bespoke-Nimble-9B"
    assert payload["max_tokens"] == 1
    assert payload["logprob_token_ids"] == [32, 33]
    assert payload["logprobs"] == 1
    assert payload["add_special_tokens"] is False
    assert payload["prompt"].startswith("<|im_start|>system\n")


def test_renormalizes_over_candidates_only():
    # The sampled token (a non-candidate) and extra keys must not leak in.
    dm, _ = _model(refund={"A": -3.0, "B": -3.0, "Hello": -0.01})
    ev = dm.evaluate("x", {"refund": QUESTIONS["refund"]})
    assert ev.answers["refund"]["noul"] == pytest.approx(0.5)


def test_temperature_flattens_without_moving_the_argmax():
    lp = {"A": math.log(0.8), "B": math.log(0.2)}
    sharp = DecisionModel(FakeClient({"team": lp}), "m").evaluate("x", {"team": QUESTIONS["team"]})
    flat = DecisionModel(FakeClient({"team": lp}), "m", temperature=2.0).evaluate(
        "x", {"team": QUESTIONS["team"]}
    )
    assert sharp.answers["team"]["choice"] == flat.answers["team"]["choice"] == "billing"
    assert flat.answers["team"]["probabilities"]["billing"] == pytest.approx(2 / 3)


def test_missing_candidate_scores_zero_but_all_missing_is_an_error():
    assert (
        candidate_logprobs({"choices": [{"logprobs": {"top_logprobs": [{"A": -1.0}]}}]}, 2)[1]
        == -math.inf
    )
    dm, _ = _model(refund={"Z": -1.0})
    with pytest.raises(DecisionError, match="no finite"):
        dm.evaluate("x", {"refund": QUESTIONS["refund"]})


def test_token_id_keys_are_understood():
    top = {"token_id:32": -2.0, "token_id:33": -0.5}
    assert candidate_logprobs({"choices": [{"logprobs": {"top_logprobs": [top]}}]}, 2) == [
        -2.0,
        -0.5,
    ]


def test_endpoint_without_logprobs_is_named():
    with pytest.raises(DecisionError, match="logprob_token_ids"):
        candidate_logprobs({"choices": [{"text": "A"}]}, 2)


@pytest.mark.parametrize(
    "questions",
    [
        {},
        {"q": {"type": "maybe", "instructions": "x"}},
        {"q": {"type": "choice", "instructions": "x", "criteria": {"only": "one"}}},
        {
            "q": {
                "type": "choice",
                "instructions": "x",
                "criteria": {f"k{i}": None for i in range(27)},
            }
        },
        {"q": {"type": "score", "instructions": "x", "criteria": ["one"]}},
        {"q": {"type": "noul", "instructions": "  "}},
        {"q": {"type": "noul", "instructions": "x", "criteria": {"maybe": "?"}}},
        {"q": {"type": "noul", "instructions": "x", "extra": 1}},
    ],
)
def test_invalid_questions_fail_before_any_call(questions):
    dm, client = _model()
    with pytest.raises(DecisionError):
        dm.evaluate("x", questions)
    assert client.payloads == []


def test_empty_state_is_refused():
    dm, client = _model()
    with pytest.raises(DecisionError):
        dm.evaluate("   ", {"refund": QUESTIONS["refund"]})
    assert client.payloads == []


def test_no_decision_model_configured():
    assert decision_model_for(FakeClient({}, decision_model="")) is None
    assert build_decision_tools(FakeClient({}, decision_model="")) == []
    with pytest.raises(DecisionError):
        DecisionModel(FakeClient({}), "")


def test_agent_tool_returns_typed_json():
    client = FakeClient({"refund": {"A": math.log(0.25), "B": math.log(0.75)}})
    (tool,) = build_decision_tools(client)
    assert tool["name"] == "decision_evaluate"
    out = json.loads(
        tool["execute"](state="charged twice", questions={"refund": QUESTIONS["refund"]})
    )
    assert out["type"] == "decision"
    assert out["answers"]["refund"]["noul"] == pytest.approx(0.75)
    bad = json.loads(tool["execute"](state="x", questions={"q": {"type": "nope"}}))
    assert (bad["type"], bad["code"]) == ("decision_error", "bad_request")


def test_agent_tool_reports_an_unreachable_endpoint():
    class Down(FakeClient):
        def text_completion(self, payload, *, timeout=None):
            raise ConnectionError("No active endpoint for model 'Bespoke-Nimble-9B'")

    (tool,) = build_decision_tools(Down({}))
    out = json.loads(tool["execute"](state="x", questions={"refund": QUESTIONS["refund"]}))
    assert out["code"] == "decision_model_unavailable"


class _Flaky(FakeClient):
    """Fails the first ``fails`` calls with ``status``, then answers."""

    def __init__(self, status, fails=1):
        super().__init__({"refund": {"A": math.log(0.2), "B": math.log(0.8)}})
        self.status, self.fails, self.timeouts = status, fails, []

    def text_completion(self, payload, *, timeout=None):
        from r4.errors import APIError

        self.timeouts.append(timeout)
        if self.fails:
            self.fails -= 1
            raise APIError(f"API error: {self.status}", status_code=self.status)
        return super().text_completion(payload, timeout=timeout)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_fast_gateway_5xx_is_retried_once(status):
    client = _Flaky(status)
    ev = DecisionModel(client, "Bespoke-Nimble-9B", timeout=5.0).evaluate(
        "x", {"refund": QUESTIONS["refund"]}
    )
    assert ev.answers["refund"]["noul"] == pytest.approx(0.8)
    assert len(client.timeouts) == 2 and client.timeouts[1] <= 5.0


@pytest.mark.parametrize(("status", "fails"), [(400, 1), (503, 2)])
def test_other_errors_and_a_second_5xx_are_not_retried_again(status, fails):
    client = _Flaky(status, fails)
    with pytest.raises(Exception, match=str(status)):
        DecisionModel(client, "Bespoke-Nimble-9B").evaluate("x", {"refund": QUESTIONS["refund"]})
    assert len(client.timeouts) == (1 if status == 400 else 2)


class TestTextCompletionRoute:
    """Client.text_completion rides the same gated route as chat (ADR-062)."""

    @staticmethod
    def _client(**over):
        from types import SimpleNamespace

        from r4.client import Client

        cfg = SimpleNamespace(
            model="chat-model",
            gateway_url="https://gw.example",
            api_key="jwt",
            endpoint_url=None,
            endpoint_provider=None,
            model_routes={},
        )
        for key, value in over.items():
            setattr(cfg, key, value)
        return Client(config=cfg)

    def test_platform_route_posts_to_gw_llm_completions(self, monkeypatch):
        from r4.client import Client

        client = self._client()
        sent = {}

        class _Http:
            def post(self, url, json_body=None, **kwargs):
                sent.update(url=url, body=json_body, **kwargs)
                return {"choices": []}

        monkeypatch.setattr(Client, "_http_for_route", lambda self, route: _Http())
        client.text_completion({"model": "Bespoke-Nimble-9B", "prompt": "p"}, timeout=5)
        assert sent["url"] == "https://gw.example/gw/llm/v1/completions"
        assert sent["body"]["model"] == "Bespoke-Nimble-9B"
        assert (sent["provider_request"], sent["timeout"]) == (False, 5)

    def test_sensitive_project_never_reaches_a_public_decision_route(self, monkeypatch):
        from r4.client import Client
        from r4.errors import R4Error

        monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
        client = self._client(endpoint_url="https://api.public.example/v1")
        with pytest.raises(R4Error, match="ADR-062"):
            client.text_completion({"model": "jev", "prompt": "p"})


class _RateLimited(FakeClient):
    """The first call meets nginx's rate-limit rejection (429 + Retry-After: 1)."""

    def __init__(self, fails=1):
        super().__init__({"refund": {"A": math.log(0.2), "B": math.log(0.8)}})
        self.fails, self.timeouts = fails, []

    def text_completion(self, payload, *, timeout=None):
        import httpx

        from r4._http import raise_for_status

        self.timeouts.append(timeout)
        if self.fails:
            self.fails -= 1
            raise_for_status(
                httpx.Response(
                    429,
                    headers={"Retry-After": "1"},
                    json={"error": "Too Many Requests", "message": "Rate limit exceeded"},
                )
            )
        return super().text_completion(payload, timeout=timeout)


def test_an_nginx_rate_limit_429_is_retried_once_after_retry_after(monkeypatch):
    """Stab cycle 3 (client-server-compat#0): nginx now rejects with 429, not 503."""
    from r4.decisions import evaluator

    slept = []
    monkeypatch.setattr(evaluator.time, "sleep", slept.append)
    client = _RateLimited()
    ev = DecisionModel(client, "Bespoke-Nimble-9B", timeout=5.0).evaluate(
        "x", {"refund": QUESTIONS["refund"]}
    )
    assert ev.answers["refund"]["noul"] == pytest.approx(0.8)
    assert slept == [1.0]
    assert len(client.timeouts) == 2 and client.timeouts[1] <= 4.0


def test_a_second_429_or_a_retry_after_past_half_the_timeout_is_final(monkeypatch):
    from r4.decisions import evaluator
    from r4.errors import RateLimitError

    monkeypatch.setattr(evaluator.time, "sleep", lambda _s: None)
    client = _RateLimited(fails=2)
    with pytest.raises(RateLimitError):
        DecisionModel(client, "Bespoke-Nimble-9B").evaluate("x", {"refund": QUESTIONS["refund"]})
    assert len(client.timeouts) == 2
    client = _RateLimited()
    with pytest.raises(RateLimitError):
        DecisionModel(client, "Bespoke-Nimble-9B", timeout=1.5).evaluate(
            "x", {"refund": QUESTIONS["refund"]}
        )
    assert len(client.timeouts) == 1
