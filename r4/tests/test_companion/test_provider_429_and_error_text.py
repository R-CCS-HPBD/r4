"""Rate limits retry, and provider errors read once (companion sweep 2026-10-21).

Through the real bridge against an OpenAI-compatible source:

- a streamed 429 (RateLimitError, which has no status_code) was raised
  after ONE request although the agent treats 429 as transient;
- the console error repeated the provider's reason and showed it as a
  Python dict: "API error: 400: too long — {'message': 'too long'}";
- a RateLimitError carried no hint at all.
"""

from __future__ import annotations

import json

from r4.companion.agent import _is_transient_provider_error, _status_is_transient
from r4.companion.ui.bridge_server import _bridge_error_response
from r4.errors import APIError, RateLimitError


def test_rate_limit_error_is_transient() -> None:
    assert _status_is_transient(RateLimitError("Rate limit exceeded: slow down"))
    assert _is_transient_provider_error(RateLimitError(retry_after=None)) is not None


def test_rate_limit_retry_after_is_honoured() -> None:
    assert _is_transient_provider_error(RateLimitError(retry_after=7)) == 7.0


def test_provider_reason_is_shown_once_and_not_as_a_dict() -> None:
    body = json.dumps({"error": {"message": "maximum context length is 8192 tokens"}})
    exc = APIError(
        "API error: 400: maximum context length is 8192 tokens",
        status_code=400,
        response_body=body,
    )
    message = _bridge_error_response(1, exc)["error"]["message"]
    assert message == "API error: 400: maximum context length is 8192 tokens"


def test_an_unparsed_body_still_adds_its_detail() -> None:
    exc = APIError("API error: 500", status_code=500, response_body="Internal Server Error")
    assert _bridge_error_response(1, exc)["error"]["message"] == (
        "API error: 500 — Internal Server Error"
    )


def test_openai_error_body_detail_is_its_message() -> None:
    body = json.dumps({"error": {"message": "upstream down"}})
    exc = APIError("API error: 502", status_code=502, response_body=body)
    assert _bridge_error_response(1, exc)["error"]["message"] == "API error: 502 — upstream down"


def test_rate_limit_error_gets_a_hint() -> None:
    error = _bridge_error_response(1, RateLimitError("Rate limit exceeded"))["error"]
    assert "Rate limited" in error["hint"]
