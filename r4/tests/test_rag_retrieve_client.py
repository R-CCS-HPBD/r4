"""RAGClient.retrieve / fetch_passage over the retrieve verb (ADR-093 C10, D-2).

The client is thin: it sends the knobs it was given (None omitted), parses the
JSON as ``r4_contracts.retrieval.RetrieveResult`` and applies no cut of its
own. An older server without ``/rag/retrieve`` (404/405) gets exactly one
fallback call to ``/gw/rag/query``, reported in ``lanes["fallback"]``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from r4_contracts.models import RETRIEVAL_PROFILES
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

from r4.client import Client
from r4.config import Config
from r4.errors import APIError, R4Error
from r4.rag import RAGClient, _SimpleBM25

GATEWAY = "http://gateway.test"


def _response(payload, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    response.text = "" if status_code < 400 else '{"detail": "Not Found"}'
    response.json.return_value = payload
    return response


def _server_result() -> RetrieveResult:
    return RetrieveResult(
        query="what filters P waves?",
        passages=(
            RetrievedPassage(
                rank=1,
                source_id="node-1",
                artifact_id="art-1",
                source_name="mermaid.pdf",
                text="A 1 Hz high-pass isolates P-wave onsets.",
                section="Methods",
                span=(10, 52),
                scores={"dense": 0.91},
                lanes=("dense", "lex"),
            ),
        ),
        sufficiency=RetrievalSufficiency(
            status="sufficient", basis="z_null", value=4.2, threshold=3.0
        ),
        searched=9412,
        space="e5-base-v2",
        lanes={"dense": {"status": "ok", "n": 50}},
    )


class _Gateway:
    """Patches httpx.Client; GET and POST answer with the given responses."""

    def __init__(self, get=None, post=None):
        self._patch = patch("httpx.Client")
        self._get = get
        self._post = post

    def __enter__(self):
        mock = self._patch.start()
        self.http = mock.return_value.__enter__.return_value
        self.http.get.return_value = self._get
        self.http.post.return_value = self._post
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def _client(project_id: str | None = "project-a") -> Client:
    return Client(config=Config(gateway_url=GATEWAY, api_key="k", project_id=project_id))


# -- the verb ------------------------------------------------------------------


def test_retrieve_gets_the_verb_and_parses_the_contract():
    with _Gateway(get=_response(_server_result().to_dict())) as gw:
        result = _client().rag.retrieve("what filters P waves?")

    assert isinstance(result, RetrieveResult)
    assert result == _server_result()
    call = gw.http.get.call_args
    assert call.args[0] == f"{GATEWAY}/gw/rag/retrieve"
    assert call.kwargs["params"] == {"q": ["what filters P waves?"], "project_id": "project-a"}
    gw.http.post.assert_not_called()


def test_retrieve_sends_only_the_knobs_given():
    with _Gateway(get=_response(_server_result().to_dict())) as gw:
        _client().rag.retrieve(
            ["q1", "q2"],
            project_id="other",
            graphs=["g-a", "g-b"],
            exclude_graphs="g-c",
            k_max=40,
            per_artifact=1,
            lexical="off",
            rerank="on",
            cutoff="fixed",
            threshold=0.6,
            trace=True,
        )

    params = gw.http.get.call_args.kwargs["params"]
    assert params == {
        "q": ["q1", "q2"],
        "project_id": "other",
        "graphs": "g-a,g-b",
        "exclude_graphs": "g-c",
        "k_max": 12,  # clamped to the verb's 1-12
        "per_artifact": 1,
        "lexical": "off",
        "rerank": "on",
        "cutoff": "fixed",
        "threshold": 0.6,
        "trace": 1,
    }


def test_query_variants_go_on_the_wire_as_repeated_q():
    request = httpx.Request("GET", f"{GATEWAY}/gw/rag/retrieve", params={"q": ["a b", "c"]})

    assert request.url.query == b"q=a+b&q=c"


def test_retrieve_requires_a_project():
    with pytest.raises(R4Error, match="project_id is required"):
        _client(project_id=None).rag.retrieve("q")


def test_retrieve_refuses_more_than_three_variants():
    with pytest.raises(R4Error, match="at most 3"):
        _client().rag.retrieve(["a", "b", "c", "d"])


def test_retrieve_passes_turn_cancellation_through():
    parent = MagicMock()
    parent.config.gateway_url = GATEWAY
    parent.config.project_id = "p"
    parent.http.get.return_value = _server_result().to_dict()
    cancel = MagicMock()

    RAGClient(parent).retrieve("q", _turn_cancel=cancel)

    kwargs = parent.http.get.call_args.kwargs
    assert kwargs["cancel_check"] is cancel.check
    assert kwargs["register_abort"] is cancel.register_close


# -- version skew: one fallback to /rag/query ------------------------------------


_LEGACY_QUERY = {
    "answer": "A 1 Hz filter.",
    "sources": [
        {
            "document_id": "node-1",
            "content": "A 1 Hz high-pass isolates P-wave onsets.",
            "score": 0.88,
            "metadata": {
                "source_name": "mermaid.pdf",
                "model_name": "e5-base-v2",
                "text_source": "artifact",
            },
        }
    ],
}


@pytest.mark.parametrize("status", [404, 405])
def test_older_server_gets_exactly_one_fallback_call(status):
    with _Gateway(get=_response({}, status), post=_response(_LEGACY_QUERY)) as gw:
        _client().rag.retrieve("what filters P waves?", k_max=3)

    assert gw.http.get.call_count == 1
    assert gw.http.post.call_count == 1
    post = gw.http.post.call_args
    assert post.args[0] == f"{GATEWAY}/gw/rag/query"
    assert post.kwargs["json"]["top_k"] == 3
    assert "threshold" not in post.kwargs["json"]


def test_fallback_result_is_reported_and_carries_the_sources():
    with _Gateway(get=_response({}, 404), post=_response(_LEGACY_QUERY)):
        result = _client().rag.retrieve("what filters P waves?")

    assert result.lanes == {"fallback": {"status": "ok", "reason": "server has no /rag/retrieve"}}
    assert result.sufficiency.status == "unknown"
    (passage,) = result.passages
    assert (passage.rank, passage.source_id, passage.source_name) == (1, "node-1", "mermaid.pdf")
    assert passage.scores == {"dense": 0.88}
    assert result.space == "e5-base-v2"


def test_fallback_with_nothing_past_the_legacy_cut_is_insufficient():
    legacy = {"answer": "No knowledge in this project answers this (no vectors).", "sources": []}
    with _Gateway(get=_response({}, 404), post=_response(legacy)):
        result = _client().rag.retrieve("q")

    assert result.passages == ()
    assert result.sufficiency.status == "insufficient"
    assert "no vectors" in result.sufficiency.detail


def test_server_error_is_raised_without_a_fallback():
    with _Gateway(get=_response({}, 500), post=_response(_LEGACY_QUERY)) as gw:
        with pytest.raises(APIError) as excinfo:
            _client().rag.retrieve("q")

    assert excinfo.value.status_code == 500
    gw.http.post.assert_not_called()


# -- the fetch handle -------------------------------------------------------------


def test_fetch_passage_gets_the_handle_with_window_and_cap():
    payload = {"source_id": "node/1", "text": "full chunk", "window": 1}
    with _Gateway(get=_response(payload)) as gw:
        out = _client().rag.fetch_passage("node/1", window=1, max_chars=2000)

    assert out == payload
    call = gw.http.get.call_args
    assert call.args[0] == f"{GATEWAY}/gw/rag/passages/node%2F1"
    assert call.kwargs["params"] == {"window": 1, "max_chars": 2000, "project_id": "project-a"}


def test_fetch_passage_requires_a_node_id():
    with pytest.raises(R4Error, match="node_id is required"):
        _client().rag.fetch_passage("")


def test_fetch_passage_sends_the_project_the_handle_came_from():
    """A handle from ``retrieve(project_id=other)`` is fetched in that project:
    the server checks the caller may read it (the shared-corpus grant)."""
    with _Gateway(get=_response({"source_id": "node-1", "text": "t"})) as gw:
        _client().rag.fetch_passage("node-1", project_id="shared-corpus")

    assert gw.http.get.call_args.kwargs["params"]["project_id"] == "shared-corpus"


def test_fetch_passage_without_any_project_leaves_it_to_the_server():
    with _Gateway(get=_response({"source_id": "node-1", "text": "t"})) as gw:
        _client(project_id=None).rag.fetch_passage("node-1")

    assert "project_id" not in gw.http.get.call_args.kwargs["params"]


# -- a stopped turn, and the verb's own time bound -----------------------------------


def _parent(timeout: int = 300) -> MagicMock:
    parent = MagicMock()
    parent.config.gateway_url = GATEWAY
    parent.config.project_id = "p"
    parent.config.timeout = timeout
    parent.http.get.return_value = _server_result().to_dict()
    return parent


def _stopped_turn():
    from r4.companion.cancellation import TurnCancellation

    token = TurnCancellation()
    token.request()
    return token


def _http_checks_the_stop(*_args, **kwargs):
    """What ``_http.request`` does on an aborted request: run cancel_check."""
    kwargs["cancel_check"]()
    raise AssertionError("cancel_check should have raised")


def test_a_stopped_turn_surfaces_as_cancelled_not_as_a_failed_retrieve():
    from r4.companion.cancellation import TurnCancelledError

    parent = _parent()
    parent.http.get.side_effect = _http_checks_the_stop

    with pytest.raises(TurnCancelledError):
        RAGClient(parent).retrieve("q", _turn_cancel=_stopped_turn())


def test_a_stopped_turn_in_the_fallback_call_surfaces_as_cancelled():
    from r4.companion.cancellation import TurnCancelledError

    parent = _parent()
    parent.http.get.side_effect = APIError("Not Found", status_code=404)
    parent.http.post.side_effect = _http_checks_the_stop

    with pytest.raises(TurnCancelledError):
        RAGClient(parent).retrieve("q", _turn_cancel=_stopped_turn())


def test_a_failure_on_a_live_turn_is_still_an_r4_error():
    parent = _parent()
    parent.http.get.side_effect = OSError("connection reset")
    live = MagicMock()

    with pytest.raises(R4Error, match="RAG retrieve failed: connection reset"):
        RAGClient(parent).retrieve("q", _turn_cancel=live)


def test_retrieve_waits_its_own_bound_not_the_client_timeout(monkeypatch):
    monkeypatch.delenv("R4_RAG_RETRIEVE_TIMEOUT", raising=False)
    parent = _parent(timeout=300)

    RAGClient(parent).retrieve("q")

    assert parent.http.get.call_args.kwargs["timeout"] <= 10


def test_retrieve_bound_follows_the_knob_and_an_explicit_timeout(monkeypatch):
    monkeypatch.setenv("R4_RAG_RETRIEVE_TIMEOUT", "4")
    parent = _parent()
    rag = RAGClient(parent)

    rag.retrieve("q")
    knob = parent.http.get.call_args.kwargs["timeout"]
    rag.retrieve("q", timeout=2.5)
    explicit = parent.http.get.call_args.kwargs["timeout"]
    rag.fetch_passage("node-1", timeout=1.5)

    assert (knob, explicit) == (4.0, 2.5)
    assert parent.http.get.call_args.kwargs["timeout"] == 1.5


def test_retrieve_bound_never_exceeds_the_client_timeout(monkeypatch):
    monkeypatch.setenv("R4_RAG_RETRIEVE_TIMEOUT", "nope")
    parent = _parent(timeout=3)

    RAGClient(parent).retrieve("q")

    assert parent.http.get.call_args.kwargs["timeout"] == 3


def test_fallback_call_keeps_the_same_bound(monkeypatch):
    monkeypatch.delenv("R4_RAG_RETRIEVE_TIMEOUT", raising=False)
    parent = _parent()
    parent.http.get.side_effect = APIError("Not Found", status_code=404)
    parent.http.post.return_value = _LEGACY_QUERY

    RAGClient(parent).retrieve("q", timeout=6)

    assert parent.http.post.call_args.kwargs["timeout"] == 6


def test_router_pre_retrieval_is_bounded(monkeypatch):
    """The router's retrieve (C12, ~0.2-0.5 s) never waits R4_TIMEOUT (300 s)."""
    from types import SimpleNamespace

    from r4.companion.context_budget import RetrievalProfile
    from r4.companion.evidence_ledger import EvidenceLedger
    from r4.companion.retrieval_router import RetrievalRouter

    monkeypatch.delenv("R4_RAG_RETRIEVE_TIMEOUT", raising=False)
    parent = _parent(timeout=300)
    verdict = RetrievalSufficiency(status="insufficient", basis="z_null")
    parent.http.get.return_value = RetrieveResult("q", (), verdict, searched=40).to_dict()
    clean = SimpleNamespace(scan=lambda *_a, **_k: SimpleNamespace(risk_level="none"))
    router = RetrievalRouter(RAGClient(parent).retrieve, EvidenceLedger(), scanner=clean)

    router.route(
        "Which filter isolates P-wave onsets in MERMAID seismic records?",
        mode="on",
        project_id="p",
        profile=RetrievalProfile(size_class="small", k_max=3, max_tokens=1_200),
    )

    assert parent.http.get.call_count >= 1
    assert all(call.kwargs["timeout"] <= 10 for call in parent.http.get.call_args_list)


# -- a loaded local bundle ----------------------------------------------------------


def test_local_bundle_is_searched_without_http_and_claims_no_sufficiency():
    parent = MagicMock()
    rag = RAGClient(parent)
    chunks = [
        {"document_id": "d1", "content": "seismic P wave picks", "metadata": {"source": "a.pdf"}},
        {"document_id": "d2", "content": "unrelated pasta recipe", "metadata": {}},
    ]
    rag._bundle_chunks = chunks
    rag._local_bm25 = _SimpleBM25(chunks)

    result = rag.retrieve("seismic P wave")

    parent.http.get.assert_not_called()
    assert result.sufficiency.status == "unknown"
    assert [p.source_name for p in result.passages] == ["a.pdf"]  # no-match row dropped
    assert "local_bundle" in result.lanes


# -- the reader's profile (ADR-093 D-14, C19) ----------------------------------------


def test_retrieve_sends_the_profile_and_the_reading_model():
    with _Gateway(get=_response(_server_result().to_dict())) as gw:
        _client().rag.retrieve("q", profile=" Large ", reader_model="kimi-k3@rikyu")

    params = gw.http.get.call_args.kwargs["params"]
    assert params == {
        "q": ["q"],
        "project_id": "project-a",
        "profile": "large",
        "reader_model": "kimi-k3@rikyu",
    }


def test_a_reader_name_is_clamped_to_the_routes_bound():
    """The route takes at most 200 characters; a longer session model name is
    clamped rather than turning every routed turn into a 422 (integrator)."""
    from r4.rag import READER_MODEL_MAX_CHARS

    name = "kimi-k3@" + "r" * 300
    with _Gateway(get=_response(_server_result().to_dict())) as gw:
        _client().rag.retrieve("q", reader_model=name)

    sent = gw.http.get.call_args.kwargs["params"]["reader_model"]
    assert (READER_MODEL_MAX_CHARS, sent) == (200, name[:200])


def test_an_unset_profile_and_reader_are_omitted():
    with _Gateway(get=_response(_server_result().to_dict())) as gw:
        _client().rag.retrieve("q", profile="", reader_model="  ")

    assert gw.http.get.call_args.kwargs["params"] == {"q": ["q"], "project_id": "project-a"}


def test_retrieve_refuses_an_unknown_profile():
    with _Gateway(get=_response(_server_result().to_dict())) as gw:
        with pytest.raises(R4Error, match="profile must be one of auto, small, medium, large"):
            _client().rag.retrieve("q", profile="huge")

    gw.http.get.assert_not_called()


def test_the_legacy_fallback_carries_no_profile():
    with _Gateway(get=_response({}, 404), post=_response(_LEGACY_QUERY)) as gw:
        _client().rag.retrieve("q", k_max=10, profile="large", reader_model="glm-5.3")

    body = gw.http.post.call_args.kwargs["json"]
    assert body["top_k"] == 10
    assert "profile" not in body and "reader_model" not in body


def test_a_local_bundle_takes_the_reader_options_without_reporting_them():
    parent = MagicMock()
    rag = RAGClient(parent)
    chunks = [{"document_id": "d1", "content": "seismic P wave picks", "metadata": {}}]
    rag._bundle_chunks = chunks
    rag._local_bm25 = _SimpleBM25(chunks)

    result = rag.retrieve("seismic P wave", k_max=10, profile="large", reader_model="kimi-k3")

    parent.http.get.assert_not_called()
    assert "unapplied" not in result.lanes["local_bundle"]
    assert len(result.passages) == 1


def test_the_router_sends_its_profile_and_reader_on_the_wire():
    """A large reader's routed turn asks the verb for 10 passages sized for
    the large profile, naming the session model (rikyu glm-5.3)."""
    from types import SimpleNamespace

    from r4.companion.evidence_ledger import EvidenceLedger
    from r4.companion.retrieval_router import RetrievalRouter, pack_profile

    parent = _parent()
    verdict = RetrievalSufficiency(status="insufficient", basis="z_null")
    parent.http.get.return_value = RetrieveResult("q", (), verdict, searched=40).to_dict()
    clean = SimpleNamespace(scan=lambda *_a, **_k: SimpleNamespace(risk_level="none"))
    router = RetrievalRouter(RAGClient(parent).retrieve, EvidenceLedger(), scanner=clean)

    router.route(
        "Which filter isolates P-wave onsets in MERMAID seismic records?",
        mode="on",
        project_id="p",
        profile=pack_profile("glm-5.3@rikyu", 1_000_000),
        reader_model="glm-5.3@rikyu",
    )

    params = parent.http.get.call_args.kwargs["params"]
    # k_max is the large pack's passages (10 until ADR-093 MD-14; the medium
    # 4 since, 2026-10-13)
    assert (params["profile"], params["reader_model"], params["k_max"]) == (
        "large",
        "glm-5.3@rikyu",
        RETRIEVAL_PROFILES["large"].pack_passages,
    )
