"""Evaluate decision questions against the platform decision model.

One ``/v1/completions`` request per question (``max_tokens=1``,
``logprob_token_ids`` = the candidate letters), sent concurrently; the shared
context+schema prefix makes every request after the first a prefix-cache hit
on the serving engine. The candidate logprobs are renormalized with the
release temperature: ``softmax(candidate_logits / T)``.

The result is a prediction. It never grants permission to act: callers apply
their own thresholds (calibrated on their own labelled cases) and policy.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from r4.errors import RateLimitError

from . import nimble
from .questions import DecisionError, Question, State, answer_for, option_keys, parse_questions

if TYPE_CHECKING:
    from r4.client import Client

_MAX_PARALLEL = 8
#: Gateway answers worth one more try (TB 4.0 decision lane 2026-10-06: a 503
#: in 0.5 s from the platform's nginx, the next call fine). A scoring call is
#: idempotent; the retry only runs while half the timeout is still left.
_RETRY_STATUS = frozenset({502, 503, 504})


def _retry_delay(exc: BaseException) -> float | None:
    """Seconds to wait before the one retry, or ``None`` when ``exc`` is final.

    nginx answers its rate-limit rejections 429 + ``Retry-After`` (it used to
    be the 503 above); r4 maps that to ``RateLimitError``, which carries no
    ``status_code`` (stab cycle 3, client-server-compat#0).
    """
    if isinstance(exc, RateLimitError):
        return float(max(exc.retry_after or 0, 0))
    return 0.0 if getattr(exc, "status_code", None) in _RETRY_STATUS else None


@dataclass
class Evaluation:
    """Typed answers keyed by question name (Jev ``systemone`` response shape)."""

    model: str
    answers: dict[str, dict[str, Any]]
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "answers": self.answers,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
        }


def _normalize(logprobs: list[float], temperature: float) -> list[float]:
    finite = [x for x in logprobs if math.isfinite(x)]
    if not finite:
        raise DecisionError("decision model returned no finite candidate logprobs")
    peak = max(finite)
    weights = [math.exp((x - peak) / temperature) if math.isfinite(x) else 0.0 for x in logprobs]
    total = math.fsum(weights)
    return [w / total for w in weights]


def candidate_logprobs(response: dict[str, Any], count: int) -> list[float]:
    """Logprobs of candidates A.. from a vLLM completions response.

    With ``logprob_token_ids`` vLLM returns the requested tokens (plus the
    sampled one) in ``top_logprobs[0]``, keyed by the decoded token: the
    single letters themselves (``token_id:<id>`` when the server runs with
    ``--return-tokens-as-token-ids``). A missing letter scores -inf.
    """
    try:
        top = response["choices"][0]["logprobs"]["top_logprobs"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise DecisionError(
            "decision endpoint returned no candidate logprobs — is it a vLLM >= 0.26 "
            "endpoint honouring logprob_token_ids?"
        ) from exc
    if not isinstance(top, dict):
        raise DecisionError("unexpected logprobs shape from the decision endpoint")
    return [
        float(top.get(code, top.get(f"token_id:{token_id}", -math.inf)))
        for code, token_id in zip(nimble.CANDIDATE_CODES[:count], nimble.CANDIDATE_TOKEN_IDS)
    ]


class DecisionModel:
    """Client-side Nimble scorer over the platform inference API."""

    def __init__(
        self,
        client: "Client",
        model: str,
        *,
        temperature: float = nimble.RELEASE["temperature"],
        timeout: float = 60.0,
    ):
        if not model:
            raise DecisionError("no decision model configured (set decision_model)")
        if not (
            isinstance(temperature, (int, float)) and math.isfinite(temperature) and temperature > 0
        ):
            raise DecisionError("temperature must be a positive finite number")
        self._client = client
        self.model = model
        self.temperature = float(temperature)
        self.timeout = timeout

    def _score(self, prompt: str, question: Question) -> tuple[list[float], dict[str, Any]]:
        ids = nimble.candidate_token_ids(question)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": 1,
            "logprob_token_ids": ids,
            # The rendered ChatML already carries every control token.
            "add_special_tokens": False,
        }
        response = self._complete(payload)
        return _normalize(candidate_logprobs(response, len(ids)), self.temperature), response

    def _complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One scoring request; a fast gateway 5xx or rate-limit 429 is retried once."""
        started = time.monotonic()
        try:
            return self._client.text_completion(payload, timeout=self.timeout)
        except Exception as exc:
            delay = _retry_delay(exc)
            left = self.timeout - (time.monotonic() - started) - (delay or 0.0)
            if delay is None or left < self.timeout / 2:
                raise
            if delay:
                time.sleep(delay)
            return self._client.text_completion(payload, timeout=left)

    def evaluate(self, state: State, questions: dict[str, Any]) -> Evaluation:
        parsed = parse_questions(questions)
        try:
            prompts = nimble.render_prompts(state, parsed)
        except (TypeError, ValueError) as exc:
            raise DecisionError(f"state: {exc}") from exc
        started = time.monotonic()
        names = list(parsed)
        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL, len(names))) as pool:
            results = list(pool.map(lambda n: self._score(prompts[n], parsed[n]), names))
        answers: dict[str, dict[str, Any]] = {}
        input_tokens = 0
        for name, (probabilities, response) in zip(names, results):
            if len(probabilities) != len(option_keys(parsed[name])):  # pragma: no cover
                raise DecisionError(f"{name}: candidate count mismatch")
            answers[name] = answer_for(parsed[name], probabilities)
            input_tokens += int((response.get("usage") or {}).get("prompt_tokens") or 0)
        return Evaluation(
            model=self.model,
            answers=answers,
            usage={"input_tokens": input_tokens, "output_tokens": len(names)},
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def decision_model_for(client: "Client") -> DecisionModel | None:
    """The configured decision model for this client, or ``None`` when unset."""
    from .settings import configured_decision_model

    model = configured_decision_model(client)
    return DecisionModel(client, model) if model else None
