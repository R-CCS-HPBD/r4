"""A 503 hint names the cause the platform reported, not always "no endpoint".

Every 503 used to say "No active endpoint available. Run `r4 endpoint up`" —
including a knowledge job runtime or a RAG service that was down, where
provisioning a model endpoint does nothing (seen on the stand-alone stack,
2026-10-09). The typed ``x-platform-error-code`` says which service it was.
"""

from __future__ import annotations

import pytest

from r4.errors import APIError


@pytest.mark.parametrize(
    "code",
    ["llm.unavailable", "endpoints.unavailable", "models.unavailable", "chatapi.unavailable"],
)
def test_endpoint_codes_keep_the_provisioning_hint(code):
    hint = APIError("x", status_code=503, error_code=code).details["hint"]
    assert "r4 endpoint up" in hint


@pytest.mark.parametrize(
    "code", ["knowledge2.unavailable", "rag.unavailable", "jobs.unavailable", "mcp.unavailable"]
)
def test_other_services_are_named_not_blamed_on_endpoints(code):
    hint = APIError("x", status_code=503, error_code=code).details["hint"]
    assert "r4 endpoint up" not in hint
    assert code.split(".")[0] in hint


def test_untyped_503_keeps_the_old_hint():
    """Older servers send no code; the old hint stays for them."""
    hint = APIError("x", status_code=503).details["hint"]
    assert "r4 endpoint up" in hint
