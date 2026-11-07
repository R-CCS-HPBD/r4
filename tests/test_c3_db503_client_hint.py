"""Cycle-3 client-server-compat#1 (2026-10-22): a UMRS database outage
reaches r4 as "the platform's database service is unavailable", not as
"No active endpoint available. Run `r4 endpoint up`".

UMRS's unhandled-exception handler answered a Postgres outage with a 503
that carried no x-platform-error-code; r4 picks its 503 hint from that
header only, so every r4 version told the user to provision a model
endpoint. This pairs the real server handler with the real client error
path. Needs PYTHONPATH=umrs/src:r4/src.
"""

from __future__ import annotations

import pytest

pytest.importorskip("umrs")
psycopg2 = pytest.importorskip("psycopg2")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from umrs.api.error_responses import unhandled_exception_handler  # noqa: E402

from r4._http import raise_for_status  # noqa: E402
from r4.errors import APIError  # noqa: E402


def _outage_response():
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/umrs/jobs")
    def jobs() -> None:
        raise psycopg2.OperationalError("connection to server failed: Connection refused")

    return TestClient(app, raise_server_exceptions=False).get("/umrs/jobs")


def test_database_outage_hint_does_not_blame_endpoints() -> None:
    response = _outage_response()
    assert response.status_code == 503
    with pytest.raises(APIError) as info:
        raise_for_status(response)
    hint = info.value.details["hint"]
    assert "r4 endpoint up" not in hint
    assert "database" in hint


def _no_dsn_response(monkeypatch: pytest.MonkeyPatch):
    """The real no-DSN knowledge-pool path behind the real UMRS handlers."""
    from starlette.exceptions import HTTPException
    from umrs.api import _asyncpg_pool
    from umrs.api.error_responses import structured_http_exception_handler

    monkeypatch.setattr(_asyncpg_pool, "_resolve_knowledge_dsn", lambda: ("", False))
    app = FastAPI()
    app.add_exception_handler(HTTPException, structured_http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/umrs/knowledge/attrition")
    async def attrition(request: Request) -> None:
        await _asyncpg_pool.knowledge_asyncpg_pool(request)

    return TestClient(app, raise_server_exceptions=False).get("/umrs/knowledge/attrition")


def test_no_dsn_hint_does_not_blame_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 2: the no-DSN 503 (a sibling of the outage 503) is typed too."""
    response = _no_dsn_response(monkeypatch)
    assert response.status_code == 503
    with pytest.raises(APIError) as info:
        raise_for_status(response)
    assert info.value.details["code"] == "database.unavailable"
    hint = info.value.details["hint"]
    assert "r4 endpoint up" not in hint
    assert "database" in hint


def test_database_unavailable_code_is_registered() -> None:
    """The code UMRS sets as a literal is in the r4-contracts taxonomy."""
    from umrs.api.error_responses import DATABASE_UNAVAILABLE_CODE

    from r4_contracts.errors import known_error_codes

    assert known_error_codes().get(DATABASE_UNAVAILABLE_CODE) == 503
