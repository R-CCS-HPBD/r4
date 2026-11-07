from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.model_resolution import (
    ensure_model_ready,
    model_is_provisioned,
    resolve_initial_model,
    session_model_info,
)
from r4.errors import R4Error


def _client(
    *,
    model: str = "rivault-gpt-oss-static",
    endpoint_url: str | None = None,
    list_endpoints: dict | None = None,
) -> SimpleNamespace:
    client = SimpleNamespace(
        config=SimpleNamespace(model=model, endpoint_url=endpoint_url, endpoint_provider=None),
        list_endpoints=MagicMock(
            return_value=list_endpoints or {"endpoints": []},
        ),
    )
    return client


def _session(*, agent_model: str | None, client: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(model=agent_model),
        client=client,
    )


def test_session_model_info_without_explicit_model() -> None:
    info = session_model_info(_session(agent_model=None, client=_client()))

    assert info["model"] is None
    assert info["profile_default"] == "rivault-gpt-oss-static"
    assert info["needs_selection"] is True
    assert info["provisioned"] is False


def test_resolve_initial_model_auto_selects_single_ready_endpoint() -> None:
    client = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "qwen-7b"},
            ]
        }
    )

    assert resolve_initial_model(client, explicit=None, saved=None) == "qwen-7b"


def test_resolve_initial_model_prefers_profile_default_when_ready() -> None:
    client = _client(
        model="qwen-7b",
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "qwen-7b"},
                {"instance_id": "ep-2", "status": "ready", "model": "llama-8b"},
            ]
        },
    )

    assert resolve_initial_model(client, explicit=None, saved=None) == "qwen-7b"


def test_resolve_initial_model_leaves_unset_when_unprovisioned_default() -> None:
    client = _client()

    assert resolve_initial_model(client, explicit=None, saved=None) is None


def test_model_is_provisioned_for_ready_endpoint() -> None:
    client = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "qwen-7b"},
            ]
        }
    )

    assert model_is_provisioned(client, "qwen-7b") is True
    assert model_is_provisioned(client, "rivault-gpt-oss-static") is False


def test_model_is_provisioned_for_degraded_endpoint() -> None:
    client = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "degraded", "model": "qwen-7b"},
            ]
        }
    )

    assert model_is_provisioned(client, "qwen-7b") is True


def test_model_is_not_provisioned_for_failed_endpoint() -> None:
    client = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "failed", "model": "qwen-7b"},
            ]
        }
    )

    assert model_is_provisioned(client, "qwen-7b") is False


def test_ensure_model_ready_requires_selection() -> None:
    session = _session(agent_model=None, client=_client())

    with pytest.raises(R4Error, match="No model selected") as exc:
        ensure_model_ready(session)

    assert "hint" in exc.value.details


def test_ensure_model_ready_falls_back_to_provisioned_profile_default() -> None:
    client = _client(
        model="qwen-7b",
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "qwen-7b"},
            ]
        },
    )
    session = _session(agent_model=None, client=client)

    assert ensure_model_ready(session) == "qwen-7b"


def test_ensure_model_ready_blocks_unprovisioned_model() -> None:
    session = _session(agent_model="rivault-gpt-oss-static", client=_client())

    with pytest.raises(R4Error, match="not provisioned") as exc:
        ensure_model_ready(session)

    assert "/endpoint up rivault-gpt-oss-static" in exc.value.details["hint"]


def test_resolve_initial_model_honors_provisioned_saved_model() -> None:
    client = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "qwen-7b"},
            ]
        }
    )

    assert resolve_initial_model(client, explicit=None, saved="qwen-7b") == "qwen-7b"


def test_resolve_initial_model_drops_stale_saved_model_for_ready_endpoint() -> None:
    """A saved model targeting a retired deployment must not pin the session.

    Regression for the AI4S->RCCS migration 502: a resumed session whose saved
    model is no longer reachable should self-heal to a live ready endpoint
    instead of routing every turn to the dead gateway.
    """
    client = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-1", "status": "ready", "model": "qwen-7b"},
            ]
        }
    )

    resolved = resolve_initial_model(client, explicit=None, saved="retired-model@ai4s")

    assert resolved == "qwen-7b"


def test_resolve_initial_model_keeps_saved_when_no_ready_endpoints() -> None:
    """With no live endpoints, keep the saved name so the user gets a hint."""
    client = _client()

    resolved = resolve_initial_model(client, explicit=None, saved="retired-model@ai4s")

    assert resolved == "retired-model@ai4s"


def test_select_from_ready_prefers_active_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ready-model selection should target the active source, like `r4 chat`."""
    from r4.companion import model_resolution

    monkeypatch.setattr(model_resolution, "_active_source_name", lambda: "rccs")
    ready = ["llama3.2:latest@ollama", "Qwen3.6-35B@rccs"]

    assert model_resolution._select_from_ready(ready, profile_default=None) == "Qwen3.6-35B@rccs"


def test_select_from_ready_falls_back_to_first_without_active_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from r4.companion import model_resolution

    monkeypatch.setattr(model_resolution, "_active_source_name", lambda: None)
    ready = ["llama3.2:latest@ollama", "Qwen3.6-35B@rccs"]

    assert model_resolution._select_from_ready(ready, profile_default=None) == ready[0]


def test_resolve_initial_model_never_auto_selects_a_decision_model() -> None:
    """2026-10-02 (ADR-076): rccs served only Bespoke-Nimble-9B, which became
    every new session's chat model — a decision model scores choices and
    never chats, so auto-selection skips it."""
    only_nimble = _client(
        list_endpoints={
            "endpoints": [{"instance_id": "ep-n", "status": "ready", "model": "Bespoke-Nimble-9B"}]
        }
    )
    assert resolve_initial_model(only_nimble, explicit=None, saved=None) is None

    beside_chat = _client(
        list_endpoints={
            "endpoints": [
                {"instance_id": "ep-n", "status": "ready", "model": "Bespoke-Nimble-9B"},
                {"instance_id": "ep-q", "status": "ready", "model": "qwen-7b"},
            ]
        }
    )
    assert resolve_initial_model(beside_chat, explicit=None, saved=None) == "qwen-7b"


def test_a_configured_decision_model_is_skipped_too_but_explicit_choice_wins() -> None:
    client = _client(
        list_endpoints={
            "endpoints": [{"instance_id": "ep-j", "status": "ready", "model": "Other-Judge-1B"}]
        }
    )
    client.config.decision_model = "Other-Judge-1B"
    assert resolve_initial_model(client, explicit=None, saved=None) is None
    assert resolve_initial_model(client, explicit="Other-Judge-1B", saved=None) == "Other-Judge-1B"


def test_decision_evaluate_never_asks_for_approval() -> None:
    from r4.companion.permissions import RISKY_TOOLS, SAFE_TOOLS

    assert "decision_evaluate" in SAFE_TOOLS and "decision_evaluate" not in RISKY_TOOLS
