"""Compatibility contract tests for companion session APIs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion import CompanionSessionStore as package_store
from r4.companion.session import CompanionSession
from r4.companion.session import CompanionSessionStore as session_store
from r4.companion.session_store import CompanionSessionStore as direct_store


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._config.model = "test-model"
    client.list_endpoints.return_value = {"endpoints": [], "total": 0}
    return client


def test_session_store_import_aliases_resolve_same_class():
    assert package_store is session_store
    assert session_store is direct_store


def test_session_store_uses_session_db_path_override(tmp_path, monkeypatch):
    import r4.companion.session as session_module

    override = tmp_path / "override.db"
    monkeypatch.setattr(session_module, "DB_PATH", override)

    store = direct_store()

    assert store.db_path == override


def test_session_constructor_accepts_legacy_budget_keywords(mock_client):
    session = CompanionSession(
        session_id="compat-test",
        name="Compat Test",
        client=mock_client,
        max_llm_calls_per_turn=2,
        max_llm_calls_per_session=5,
    )

    assert session.max_llm_calls_per_turn == 2
    assert session.max_llm_calls_per_session == 5
