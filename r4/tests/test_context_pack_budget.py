"""ADR-093 D-8 (C12): the pack is sized by the model profile and counted.

The model-size class sets only k_max and the token cap (never more than 12%
of the context budget); the pack reaches ``fit_payload`` as user tokens; an
oversized pack drops its last passages until the payload fits; and it is
never replayed into a later turn's history.

Lead decision for the maintainer (2026-10-10): R4 keeps no profile table of
its own. ``retrieval_profile`` reads the shared D-14 table
(``r4_contracts.models``: ``pack_passages``/``pack_tokens``), so the large
pack was 10 passages in 8,000 tokens (C12's table said 6 in 3,000; since
ADR-093 MD-14, 2026-10-13, it is the medium 4 in 2,000); the class
is the ONE effective-size rule (a 30B-A3B is medium); and a model of unknown
scale reads with the medium pack.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.context_pack import estimate_tokens as pack_tokens
from r4_contracts.context_pack import render_pack
from r4_contracts.models import RETRIEVAL_PROFILES
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

from r4.companion import context_budget
from r4.companion.agent import COMPANION_SYSTEM_PROMPT
from r4.companion.context_budget import RAG_BUDGET_SHARE, retrieval_profile
from r4.companion.session import CompanionSessionStore
from r4.model_context import model_param_billions, model_size_class

QUESTION = "Which trials measured the effect of vitamin D on hip fractures?"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("R4_RAG_ROUTER", "R4_RAG_PROFILE", "R4_RAG_EXPAND", "R4_RAG_VERIFY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("R4_HARNESS_ABLATE_KNOWLEDGE", raising=False)


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("llama3.2:3b", "small"),
        ("qwen2.5:0.5b", "small"),
        ("meta-llama/Llama-3.2-3B-Instruct", "small"),
        ("smollm2-360m", "small"),
        ("llama-3.1-8b-instruct", "medium"),
        ("qwen2.5-7b-instruct-1m", "medium"),
        ("qwen3.8-27b", "large"),
        ("Qwen3-30B-A3B", "medium"),  # the effective size (lead decision 2026-10-10)
        ("gpt-oss-20b", "medium"),  # 21B total, 3.6B active: ~8.7B effective
        ("mixtral-8x7b", "large"),
        ("gpt-oss-120b", "large"),
        ("claude-sonnet-4", "large"),
        ("kimi-k3", "large"),
        ("test-model", "unknown"),
        ("gemma-3n-e4b", "unknown"),
    ],
)
def test_model_size_class(name, size):
    assert model_size_class(name) == size


def test_an_active_expert_tag_is_not_the_model_size():
    assert model_param_billions("Qwen3-30B-A3B") == 30.0
    assert model_param_billions("mixtral-8x7b") == 56.0
    assert model_param_billions("kimi-k3") is None


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("llama3.2:3b", ("small", "small", 3, 1_200)),
        ("llama-3.1-8b-instruct", ("medium", "medium", 4, 2_000)),
        # The shared D-14 table: since MD-14 (2026-10-13) the large pack is the
        # medium size; the effective size, and unknown -> medium (the reader
        # keeps its class).
        ("qwen3.8-27b", ("large", "large", 4, 2_000)),
        ("Qwen3-30B-A3B", ("medium", "medium", 4, 2_000)),
        ("test-model", ("unknown", "medium", 4, 2_000)),
    ],
)
def test_the_profile_sets_k_max_and_the_token_cap(model, expected):
    profile = retrieval_profile(model, 128_000)
    assert (profile.size_class, profile.profile, profile.k_max, profile.max_tokens) == expected


@pytest.mark.parametrize("name", ["small", "medium", "large"])
def test_the_pack_is_the_shared_d14_profiles_pack(name):
    shared = RETRIEVAL_PROFILES[name]
    profile = retrieval_profile("any-model", 10_000_000, override=name)
    assert (profile.k_max, profile.max_tokens) == (shared.pack_passages, shared.pack_tokens)
    assert profile.reader == (name, shared)


def test_r4_keeps_no_profile_table_of_its_own():
    assert not hasattr(context_budget, "RAG_PROFILES")
    assert not hasattr(context_budget, "RAG_DEFAULT_PROFILE")


def test_the_cap_never_exceeds_twelve_percent_of_the_context_budget():
    profile = retrieval_profile("qwen3.8-27b", 8_000)
    assert profile.max_tokens == int(8_000 * RAG_BUDGET_SHARE) == 960
    assert profile.k_max == RETRIEVAL_PROFILES["large"].pack_passages


def test_r4_rag_profile_overrides_the_name(monkeypatch):
    monkeypatch.setenv("R4_RAG_PROFILE", "large")
    assert retrieval_profile("llama3.2:3b", 128_000).profile == "large"
    monkeypatch.setenv("R4_RAG_PROFILE", "enormous")
    assert retrieval_profile("llama3.2:3b", 128_000).size_class == "small"


def test_a_detected_class_sizes_the_pack_over_the_name():
    profile = retrieval_profile("house-model", 128_000, size_class="large")
    assert (profile.size_class, profile.profile) == ("large", "large")


# ------------------------------------------------------------ the session ---


def _passages(count: int = 3, chars: int = 400) -> list[RetrievedPassage]:
    return [
        RetrievedPassage(
            rank=i + 1,
            source_id=f"node-{i + 1}",
            artifact_id=f"art-{i + 1}",
            source_name=f"trial{i + 1}.pdf",
            text=(f"Trial {i + 1} randomised older women to vitamin D or placebo. " * 20)[:chars],
        )
        for i in range(count)
    ]


def _result(passages) -> RetrieveResult:
    return RetrieveResult(
        query=QUESTION,
        passages=tuple(passages),
        sufficiency=RetrievalSufficiency(status="sufficient", basis="z_null"),
        searched=1_000,
    )


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_RAG_ROUTER", "on")
    client = MagicMock()
    client._config.model = "test-model"
    client.config = SimpleNamespace(
        profile="default", gateway_url="http://gw.test", project_id="proj-1", model="test-model"
    )
    client.list_endpoints.return_value = {
        "endpoints": [{"instance_id": "ep", "model": "test-model", "status": "ready"}],
        "total": 1,
    }
    # The budget a test sets must survive the per-send sync from the listing.
    monkeypatch.setattr(
        context_budget, "sync_session_context_budget", lambda s: s.context.max_context_tokens
    )
    client.rag.retrieve = MagicMock(return_value=_result(_passages()))
    created = CompanionSessionStore(db_path=tmp_path / "s.db").create_session(client, name="B")
    created._harness = MagicMock()
    created._harness.feedforward.return_value = ""
    sent: list[dict] = []

    def fake_send(user_message, *, history=None, **_kwargs):
        sent.append({"user_message": user_message, "history": list(history or [])})
        return "An answer."

    created.agent.send = fake_send
    created.sent = sent
    return created


def test_the_pack_is_counted_as_user_tokens(session):
    counted: list[int] = []
    real = session.context.fit_payload

    def spy(**kwargs):
        extra, history, tools, budget = real(**kwargs)
        counted.append(budget.user_tokens)
        return extra, history, tools, budget

    session.context.fit_payload = spy
    session.send(QUESTION)
    pack = session.sent[0]["user_message"]
    assert pack.startswith("<passages") and pack.count("trial") >= 3
    assert counted == [session.context.estimate_tokens(pack)]
    assert counted[0] > session.context.estimate_tokens(QUESTION) * 10


def test_an_oversized_pack_shrinks_until_the_payload_fits(session):
    one, _ = render_pack(QUESTION, _passages()[:1], label="S", start=1, max_tokens=None)
    estimate = session.context.estimate_tokens
    reserve = session.context.response_reserve_tokens
    # Room for the system prompt, the reserve and a one-passage pack only.
    session.context.max_context_tokens = (
        estimate(COMPANION_SYSTEM_PROMPT) + reserve + estimate(one) + 40
    )
    # The profile's cap alone would keep all three: the refit is what drops two.
    three, _ = render_pack(QUESTION, _passages(), label="S", start=1, max_tokens=None)
    cap = retrieval_profile("test-model", session.context.max_context_tokens).max_tokens
    assert pack_tokens(three) <= cap
    reply = session.send(QUESTION)
    assert session.sent[0]["user_message"] == one
    assert reply.startswith("An answer.")
    assert "Context budget exceeded" not in reply


def test_the_pack_is_never_replayed_into_later_history(session):
    session.send(QUESTION)
    session.client.rag.retrieve.return_value = _result(_passages())
    session.send("What did trial 2 conclude about falls in care homes?")
    later_history = session.sent[1]["history"]
    assert [m["content"] for m in later_history if m["role"] == "user"] == [QUESTION]
    assert all("<passages" not in str(m["content"]) for m in later_history)
