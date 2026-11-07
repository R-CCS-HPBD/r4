"""Implicit service-model roles (ADR-106 D-10, 2026-11-03).

Users never pick a service model: R4 resolves decision and fast work to the
platform's live role alias, routed to the platform source, even when the chat
model is on a user-registered public endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4 import service_roles as sr

LIVE = {"decision": "svc-decision", "fast": "svc-fast", "fallback": ["svc-fast"]}


def _src(name: str, url: str, kind: str = "platform", roles=None):
    return SimpleNamespace(name=name, url=url, kind=kind, default_models=roles)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import r4._http

    # probe_api_prefix dials the URL to pick /gw or /umrs; keep tests offline.
    monkeypatch.setattr(r4._http, "probe_api_prefix", lambda url: "/gw")
    sr.clear_cache()
    for role in sr.ROLES:
        monkeypatch.delenv(f"R4_{role.upper()}_MODEL", raising=False)
    monkeypatch.delenv("R4_DECISION_MODEL", raising=False)
    yield
    sr.clear_cache()


def _sources(monkeypatch, sources, active=None):
    monkeypatch.setattr(
        sr, "_platform_sources", lambda: ([s for s in sources if s.kind == "platform"], active)
    )


def _fetch(roles):
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return {"default_models": roles}

    return fetch, calls


def test_public_chat_still_resolves_platform_roles(monkeypatch):
    # The public source is active; the gateway still points at the platform.
    rccs = _src("rccs-cloud", "https://data0.example/rccs")
    _sources(
        monkeypatch, [rccs, _src("my-openai", "https://api.example", kind="public")], "my-openai"
    )
    fetch, calls = _fetch(LIVE)
    config = SimpleNamespace(gateway_url="https://data0.example/rccs")
    assert sr.resolve_role_model(config, "decision", fetch=fetch) == "svc-decision@rccs-cloud"
    assert sr.resolve_role_model(config, "fast", fetch=fetch) == "svc-fast@rccs-cloud"
    assert calls == ["https://data0.example/rccs/gw/ops/connect-info"]


def test_a_role_nobody_serves_resolves_to_none(monkeypatch):
    _sources(monkeypatch, [_src("rccs-cloud", "https://f/rccs")], "rccs-cloud")
    fetch, _ = _fetch({"fast": "svc-fast"})
    assert sr.resolve_role_model(SimpleNamespace(gateway_url=None), "decision", fetch=fetch) is None


def test_explicit_env_wins(monkeypatch):
    _sources(monkeypatch, [_src("rccs-cloud", "https://f/rccs")], "rccs-cloud")
    monkeypatch.setenv("R4_FAST_MODEL", "my-small@my-openai")
    fetch, calls = _fetch(LIVE)
    assert sr.resolve_role_model(None, "fast", fetch=fetch) == "my-small@my-openai"
    assert calls == []


def test_unreachable_front_uses_the_map_stored_at_login(monkeypatch):
    _sources(
        monkeypatch,
        [_src("rccs-cloud", "https://f/rccs", roles={"fast": "svc-fast"})],
        "rccs-cloud",
    )

    def down(url):
        raise OSError("unreachable")

    assert sr.resolve_role_model(None, "fast", fetch=down) == "svc-fast@rccs-cloud"


def test_live_answer_is_cached(monkeypatch):
    _sources(monkeypatch, [_src("rccs-cloud", "https://f/rccs")], "rccs-cloud")
    fetch, calls = _fetch(LIVE)
    for _ in range(3):
        sr.resolve_role_model(None, "fast", fetch=fetch)
    assert len(calls) == 1


def test_no_platform_source_means_no_roles(monkeypatch):
    _sources(monkeypatch, [_src("a", "https://a"), _src("b", "https://b")], None)
    fetch, calls = _fetch(LIVE)
    assert sr.resolve_role_model(SimpleNamespace(gateway_url=None), "fast", fetch=fetch) is None
    assert calls == []


@pytest.mark.parametrize(
    ("model", "alias"),
    [
        ("svc-fast", True),
        ("svc-decision@rccs-cloud", True),
        ("svc-nope", False),
        ("Bespoke-Nimble-9B", False),
    ],
)
def test_is_role_alias(model, alias):
    assert sr.is_role_alias(model) is alias


# -- decisions ---------------------------------------------------------------


def _client(decision_model="Bespoke-Nimble-9B", listed=()):
    config = SimpleNamespace(decision_model=decision_model, gateway_url="https://f/rccs")
    return SimpleNamespace(config=config, list_gateway_models=lambda timeout=10: list(listed))


def test_default_decision_setting_follows_the_platform_role(monkeypatch):
    from r4.decisions.settings import configured_decision_model, decision_model_available

    monkeypatch.setattr(
        sr, "resolve_role_model", lambda config, role, **_: "svc-decision@rccs-cloud"
    )
    client = _client()
    assert configured_decision_model(client) == "svc-decision@rccs-cloud"
    # The aggregate model listing never carries aliases; the live role map is the proof.
    assert decision_model_available(client, "svc-decision@rccs-cloud") is True


def test_explicit_decision_choice_and_off_are_kept(monkeypatch):
    from r4.decisions.settings import configured_decision_model

    monkeypatch.setattr(
        sr, "resolve_role_model", lambda config, role, **_: "svc-decision@rccs-cloud"
    )
    assert configured_decision_model(_client("My-Judge")) == "My-Judge"
    assert configured_decision_model(_client("off")) == ""


def test_no_platform_role_keeps_the_packaged_default(monkeypatch):
    from r4.decisions.settings import configured_decision_model

    monkeypatch.setattr(sr, "resolve_role_model", lambda config, role, **_: None)
    assert configured_decision_model(_client()) == "Bespoke-Nimble-9B"


def test_source_qualified_decision_model_is_checked_bare():
    from r4.decisions.settings import decision_model_available

    client = _client(listed=("Bespoke-Nimble-9B",))
    assert decision_model_available(client, "Bespoke-Nimble-9B@rccs-cloud") is True


def test_the_decision_alias_is_never_a_chat_model():
    from r4.decisions.settings import is_decision_model

    assert is_decision_model("svc-decision@rccs-cloud") is True


# -- sub-agents ---------------------------------------------------------------


def test_subagent_defaults_to_the_fast_role(monkeypatch):
    from r4.companion import agent

    monkeypatch.setattr(
        sr, "resolve_role_model", lambda config, role, **_: f"svc-{role}@rccs-cloud"
    )
    assert agent._fast_role_model(SimpleNamespace(config=None)) == "svc-fast@rccs-cloud"


def test_subagent_role_lookup_never_raises(monkeypatch):
    from r4.companion import agent

    def boom(config, role, **_):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(sr, "resolve_role_model", boom)
    assert agent._fast_role_model(SimpleNamespace(config=None)) is None


# -- the user sees which model did the work (2026-11-03) --------------------

INFO = {
    "default_models": {"decision": "svc-decision", "fast": "svc-fast"},
    "role_models": {"decision": ["Bespoke-Nimble-9B"], "fast": ["Qwen3.8-27B-NVFP4"]},
}


def _labelled(monkeypatch):
    _sources(monkeypatch, [_src("rccs-cloud", "https://f/rccs")], "rccs-cloud")
    return lambda url: INFO


def test_a_role_alias_is_named_by_its_backing_model(monkeypatch):
    fetch = _labelled(monkeypatch)
    label = sr.model_label("svc-fast@rccs-cloud", fetch=fetch)
    assert label == "Qwen3.8-27B-NVFP4 (svc-fast, rccs-cloud)"
    assert sr.model_label("gpt-5@my-openai", fetch=fetch) == "gpt-5@my-openai"


def test_an_old_front_without_role_models_keeps_the_alias(monkeypatch):
    _sources(monkeypatch, [_src("rccs-cloud", "https://f/rccs")], "rccs-cloud")
    fetch = lambda url: {"default_models": {"fast": "svc-fast"}}  # noqa: E731
    assert sr.model_label("svc-fast@rccs-cloud", fetch=fetch) == "svc-fast@rccs-cloud"


def test_the_gate_names_the_decision_model(monkeypatch):
    from r4.decisions import harness

    monkeypatch.setattr(sr, "safe_model_label", lambda m: f"Bespoke-Nimble-9B ({m})")
    reason = harness._escalation_reason("shell", 0.9, "svc-decision@rccs-cloud")
    assert reason.startswith("decision model Bespoke-Nimble-9B (svc-decision@rccs-cloud): ")
    assert harness._escalation_reason("shell", 0.9).startswith("decision model: ")


def test_the_plan_reply_notice_names_the_model(monkeypatch):
    from r4.decisions import harness

    monkeypatch.setattr(sr, "safe_model_label", lambda m: "Bespoke-Nimble-9B")
    notice = harness.intent_notice(harness.PlanReplyHint("execute", 0.99, "svc-decision@x"))
    assert notice.endswith("_(Read by Bespoke-Nimble-9B.)_")
    assert (
        harness.intent_notice(harness.PlanReplyHint("execute", 0.99))
        == harness.INTENT_NOTICES["execute"]
    )


def test_subagent_start_names_its_model(monkeypatch):
    from r4.companion import agent

    monkeypatch.setattr(sr, "resolve_role_model", lambda config, role, **_: "svc-fast@rccs-cloud")
    monkeypatch.setattr(sr, "safe_model_label", lambda m: f"Qwen3.8-27B-NVFP4 ({m})")
    parent = SimpleNamespace(client=SimpleNamespace(config=None), model="gpt-5@my-openai")
    parent._subagent_model = lambda model=None: agent.ResearchCompanion._subagent_model(
        parent, model
    )
    label = agent.ResearchCompanion._subagent_label(parent)
    assert label == "Qwen3.8-27B-NVFP4 (svc-fast@rccs-cloud)"
    assert agent.ResearchCompanion._subagent_model(parent, "my-small") == "my-small"
