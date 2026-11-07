"""A refused provision keeps the server's reason (2026-10-23, review F3).

UMRS refuses a provision whose last attempts of the same model failed with
``endpoints.provision_capped`` (HTTP 429, Retry-After, a body naming the failed
jobs and the retry time). raise_for_status turns a 429 into RateLimitError,
which carries no response body, and ``Client._provision_error`` built its
message only from the body: the user read a bare "Failed to provision
endpoint" and lost the wait.
"""

from __future__ import annotations

import httpx
import pytest

from r4._http import raise_for_status
from r4.client import Client
from r4.errors import APIError, R4Error, RateLimitError

_MESSAGE = (
    "Provisioning 'Qwen3.8-27B-NVFP4' on bdrt for project 'default' is paused until "
    "2026-10-23T03:05:00Z (in 300 s): the last 3 attempts failed within 30 min. Fix "
    "the cause and retry then; an admin may provision now with "
    "override_failure_cap=true. Failed: job 8292 at 2026-10-23T02:50:00Z: Backend job "
    "ended (FAILED); job 8291 ...; job 8290 ...."
)


def _raised(response: httpx.Response) -> R4Error:
    with pytest.raises(R4Error) as raised:
        raise_for_status(response)
    return raised.value


def _capped_response() -> httpx.Response:
    return httpx.Response(
        429,
        headers={"Retry-After": "300", "x-platform-error-code": "endpoints.provision_capped"},
        json={
            "detail": {
                "message": _MESSAGE,
                "code": "endpoints.provision_capped",
                "failed_jobs": ["8292", "8291", "8290"],
                "retry_at": "2026-10-23T03:05:00Z",
                "retry_after_sec": 300,
            }
        },
        request=httpx.Request("POST", "https://gw.example/umrs/endpoints/provision"),
    )


def test_a_capped_provision_keeps_the_reason_and_the_wait() -> None:
    error = Client._provision_error(_raised(_capped_response()))
    assert isinstance(error, RateLimitError)
    assert error.retry_after == 300
    assert str(error).startswith("Failed to provision endpoint: ")
    for part in ("paused until 2026-10-23T03:05:00Z", "in 300 s", "job 8292"):
        assert part in str(error)


def test_a_structured_detail_reads_as_its_message_not_as_json() -> None:
    error = _raised(_capped_response())
    assert str(error) == f"Rate limit exceeded: {_MESSAGE}"


def test_an_error_with_a_body_still_reads_the_body() -> None:
    response = httpx.Response(
        400,
        json={"detail": "model 'x' is not registered"},
        request=httpx.Request("POST", "https://gw.example/umrs/endpoints/provision"),
    )
    error = Client._provision_error(_raised(response))
    assert type(error) is R4Error
    assert str(error) == "Failed to provision endpoint: model 'x' is not registered"


def test_an_error_without_body_or_message_stays_plain() -> None:
    error = Client._provision_error(APIError(""))
    assert str(error) == "Failed to provision endpoint"
