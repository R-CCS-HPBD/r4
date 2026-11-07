"""Tests for companion context budget resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.context_budget import (
    effective_context_budget,
    resolve_model_context_limit,
    sync_session_context_budget,
)


def test_effective_context_budget_leaves_headroom():
    assert effective_context_budget(131_072) < 131_072
    assert effective_context_budget(131_072) >= 100_000


def test_resolve_model_context_limit_uses_endpoint_max_model_len():
    client = MagicMock()
    client.list_endpoints.return_value = {
        "endpoints": [
            {
                "model": "kimi-k2.6",
                "status": "ready",
                "max_model_len": 131_072,
            }
        ]
    }
    limit = resolve_model_context_limit(client, "kimi-k2.6")
    assert limit == effective_context_budget(131_072)


def test_resolve_model_context_limit_public_source_falls_back_to_known_table():
    """Public sources (e.g. rikyu) have no platform endpoints — the known
    table must resolve kimi-k3's 1M window instead of the 120k default."""
    client = MagicMock()
    client.list_endpoints.side_effect = RuntimeError("public source has no endpoints")
    limit = resolve_model_context_limit(client, "kimi-k3@rikyu")
    assert limit == effective_context_budget(1_048_576)
    assert limit > 900_000


def test_sync_session_context_budget_updates_context_manager():
    session = MagicMock()
    session.client = MagicMock()
    session.client.list_endpoints.return_value = {
        "endpoints": [
            {
                "model": "kimi-k2.6",
                "status": "ready",
                "max_model_len": 131_072,
            }
        ]
    }
    session.agent.model = "kimi-k2.6"
    session.context = MagicMock()

    limit = sync_session_context_budget(session)

    assert session.context.max_context_tokens == limit
    assert limit < 131_072
