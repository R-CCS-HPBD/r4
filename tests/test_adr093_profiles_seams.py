"""ADR-093 wave C (D-14): one design, sized by the reading model, end to end.

The maintainer (2026-10-10): "in the full remote deployment (with rikyu
endpoints), we will use kimi-k3 and glm-5.3 full models". The stand-alone
reader is ``llama3.2:3b``. This file drives R4's real client, router and
search_docs tool, and the eval harness's ``retrieve:large`` preset, against
UMRS's real ``/umrs/rag/retrieve`` and ``/umrs/rag/query`` routes (through the
gateway's ``/gw/rag/*`` rewrite, in process) and the REAL retrieval pipeline:
profile resolution, fusion, the cut, sufficiency, the partial cap, the merge
gate, the graph bridge stage, compose and the lane reports. Only what touches
Postgres, the encoder or a model is faked: the dense lane's rows, the lexical
lanes, hydration, the calibrated null, the merge's neighbour reads, the graph
lane's statements (its pure route and bridge rules run) and the chat call.

Pinned for a small and a large reader:

- R4's profile and reader travel on the wire and size the server's cut,
  pool, merge and partial cap; the small profile is the pre-D-14 request;
- the large profile's query expansion reaches the server as fused variants;
- graph bridges on a large-extraction site reach a large reader after a full
  cut (integrator fix: the router's passage bound also dropped the bridges);
- /rag/query sizes from the registry's answer model, and a large one gets no
  output limit (integrator fix: it was sent as ``"max_tokens": null``);
- search_docs sizes the server's cut for the session model, read per call
  (integrator fix: it sent no profile, so a large reader got the small cut);
- an over-long reader name is clamped, never refused (integrator fix);
- the harness's ``retrieve:large`` preset is served as the large profile.

(integrator, 2026-10-10)

Wave D (lead decisions for the maintainer, 2026-10-10): ONE size rule in
both repositories, the EFFECTIVE size (a ``qwen3-30b-a3b`` or ``gpt-oss-20b``
reader is medium in R4's pack, on the wire and in UMRS's cut, and as UMRS's
own answer model); a reader of unknown scale is medium too, with one notice
per session. R4's router sizes by the DETECTED scale (``r4.model_scale``);
its probes are served a 404 here, so the shared name rule decides.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from umrs.api import rag as rag_api
from umrs.api import rag_retrieve, rag_spine
from umrs.jobs import knowledge_adapters
from umrs.knowledge.retrieval import calibrate, dense, expand, graph_lane, lexical
from umrs.knowledge.retrieval import hydrate as hydrate_stage
from umrs.knowledge.retrieval.types import Candidate, LaneResult

from r4 import model_scale
from r4 import rag as r4_rag
from r4._http import HTTPCore
from r4.client import Client
from r4.companion.retrieval_router import route_turn_decision
from r4.companion.tools import _client_tools
from r4.config import Config
from r4_contracts.models import RETRIEVAL_PROFILES
from r4_contracts.retrieval import is_graph_bridge

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks" / "retrieval"))

import arms  # noqa: E402
import backends  # noqa: E402

GATEWAY = "http://gw.test"
PROJECT = "p-verified"
QUESTION = "What slows moisture sorption in hydrogels?"
SPACE = "e5-base-v2"
SMALL = "llama3.2:3b"  # the stand-alone reader
RIKYU = ("kimi-k3@rikyu", "glm-5.3")  # the remote deployment's readers
STANDALONE_WINDOW = 32_768  # ollama num_ctx on the stand-alone stack
RIKYU_WINDOW = 1_000_000  # frontier MoE windows
TOP_COSINE = 0.90
NULL_MU = 0.80
SUFFICIENT_SIGMA = 0.02  # z = 5.0, above UMRS_RAG_Z_SUFFICIENT (3.0)
PARTIAL_SIGMA = 0.04  # z = 2.5, between UMRS_RAG_Z_PARTIAL (2.0) and 3.0
VARIANTS = ["hydrogel swelling kinetics", "water uptake of polymer networks"]
SITE_KNOBS = (
    "UMRS_RAG_PROFILE",
    "UMRS_RAG_GRAPH_LANE",
    "UMRS_RAG_GRAPH_LANE_MEASURED",
    "UMRS_RAG_MERGE",
    "UMRS_RAG_RERANK_DEFAULT",
    "UMRS_RAG_FINAL_K_MAX",
    "UMRS_RAG_POOL",
    "UMRS_RAG_PER_ARTIFACT",
    "UMRS_RAG_ANSWER_MAX_TOKENS",
    "UMRS_RAG_QUERY_FACADE",
    "R4_RAG_PROFILE",
    "R4_RAG_EXPAND",
    "R4_RAG_ROUTER_AUTO",
    "R4_MODEL_SCALE",
)


def _chunk(index: int, ranks: dict[str, int]) -> Candidate:
    """One chunk per artifact; every cosine sits within the dense cut's delta."""
    cosine = round(TOP_COSINE - 0.001 * (index - 1), 6)
    return Candidate(
        node_id=f"node-chunk-g-art-{index:02d}-0",
        artifact_id=f"art-{index:02d}",
        cosine=cosine,
        lane_scores={"dense": cosine},
        lane_ranks=dict(ranks),
    )


def _no_lexical(name: str) -> Any:
    def lane(_pool: Any, _query: str, _spec: Any, *, limit: int) -> LaneResult:
        return LaneResult(name=name)

    return lane


def _encode(texts: Any) -> tuple[str, list[list[float]], str]:
    return SPACE, [[1.0, 0.0] for _ in texts], ""


def _whole_project(_request: Any, _db: Any, spec: Any, _graphs: Any, _exclude: Any) -> Any:
    return spec


class _Reply:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "Swelling slows sorption [1]."}}]}


class _Site:
    """The UMRS site: its registry chat model (extraction and answers) and the
    fakes of what touches Postgres or a model, recording what each was asked."""

    def __init__(self) -> None:
        self.model = SMALL
        self.sigma = SUFFICIENT_SIGMA
        self.specs: list[Any] = []
        self.dense_limits: list[int] = []
        self.merged: list[int] = []
        self.hops: list[set[str]] = []
        self.chats: list[dict[str, Any]] = []

    def dense_lane(self, _pool: Any, vectors: Any, _model: str, spec: Any, *, limit: int) -> Any:
        self.specs.append(spec)
        self.dense_limits.append(limit)
        lanes = [f"dense#{v}" for v in range(len(vectors))] if len(vectors) > 1 else ["dense"]
        found = [_chunk(i, dict.fromkeys(lanes, i)) for i in range(1, min(limit, 40) + 1)]
        return LaneResult("dense", found, info={"in_scope": 1842, "space": SPACE})

    @staticmethod
    def hydrate(_pool: Any, candidates: Any, _fetch: Any, **_kwargs: Any) -> None:
        for candidate in candidates:
            candidate.text = f"Passage {candidate.node_id}: values under controlled conditions."
            candidate.text_source, candidate.section = "search_text", "Results"
            candidate.source_name = f"{candidate.artifact_id}.pdf"

    def null(self, *_args: Any, **_kwargs: Any) -> calibrate.NullStats:
        return calibrate.NullStats(mu=NULL_MU, sigma=self.sigma, n=30, model=SPACE)

    def merge(self, _pool: Any, kept: Any, **_kwargs: Any) -> Any:
        self.merged.append(len(kept))
        return kept

    def graph_run(self, _pool: Any, _spec: Any, _vector: Any, _model: str, kept: set[str]) -> Any:
        """The lane's statements: two linked entities in three documents, one
        of them (art-01) already kept. Its pure route and bridge rules run."""
        self.hops.append(set(kept))
        entities = [
            graph_lane.LinkedEntity(("nife", "ldh"), "NiFe-LDH", 1.0, 2, {"art-90"}),
            graph_lane.LinkedEntity(("oer",), "OER", 1.0, 1, {"art-91", "art-01"}),
        ]
        documents, why_not = graph_lane.route(entities, kept)
        rows = [(f"node-chunk-g-{a}-0", {"artifact_id": a}, 0.2) for a in documents]
        found = graph_lane.bridges(rows, entities)
        named = {"art-90": "NiFe-LDH", "art-91": "OER"}
        report = [{"source_id": c.node_id, "bridge_entity": named[c.artifact_id]} for c in found]
        info = {"fired": bool(documents), "bridges": report}
        return LaneResult("graph", found, reason=why_not, info=info)

    def chat(self, _url: str, **kwargs: Any) -> _Reply:
        self.chats.append(kwargs["json"])
        return _Reply()

    def endpoint(self, _project: str) -> tuple[str, str]:
        return "http://llm.site", self.model


def _scope(_request: Any, target: str | None = None, **_kwargs: Any) -> tuple[str, str]:
    return "u1", target or PROJECT


@pytest.fixture
def site(monkeypatch, tmp_path) -> _Site:
    fake = _Site()
    for knob in SITE_KNOBS:
        monkeypatch.delenv(knob, raising=False)
    monkeypatch.setenv("R4_RAG_ROUTER", "on")
    # R4's model-scale probes (C22) never leave the process: the isolated
    # config has no source and no override, and every probe is a 404.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    model_scale.clear_scale_memo()
    offline = httpx.MockTransport(lambda _request: httpx.Response(404, json={}))
    monkeypatch.setattr(model_scale, "_TRANSPORT", offline)
    stages = {
        (dense, "dense_lane"): fake.dense_lane,
        (lexical, "lexical_lane"): _no_lexical("lex"),
        (lexical, "ident_lane"): _no_lexical("ident"),
        (hydrate_stage, "hydrate"): fake.hydrate,
        (calibrate, "null_for"): fake.null,
        (expand, "merge_neighbours"): fake.merge,
        (graph_lane, "_run"): fake.graph_run,
        (rag_retrieve, "resolve_graphs"): _whole_project,
        (rag_retrieve, "hidden_graphs"): lambda *_args: (),
        (rag_retrieve, "batch_encoder"): lambda: _encode,
        (rag_retrieve, "site_reranker"): lambda: None,
        (rag_spine, "_artifact_fetcher"): lambda: None,
        (knowledge_adapters, "_extract_chat_endpoint"): fake.endpoint,
        (rag_api, "_require_rag_scope"): _scope,
        (httpx, "post"): fake.chat,
    }
    for (module, name), stage in stages.items():
        monkeypatch.setattr(module, name, stage)
    return fake


class _Gateway:
    """The gateway's ``/gw/rag/* -> /umrs/rag/*`` rewrite, as R4's client sees it."""

    def __init__(self, app: TestClient) -> None:
        self.app = app
        self.calls: list[tuple[str, str, Any]] = []

    def __enter__(self) -> _Gateway:
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    @staticmethod
    def _path(url: str) -> str:
        return urlsplit(url).path.replace("/gw/rag/", "/umrs/rag/", 1)

    def get(self, url: str, params: Any = None, **_kwargs: Any) -> Any:
        self.calls.append(("GET", self._path(url), params))
        return self.app.get(self._path(url), params=params)

    def post(self, url: str, json: Any = None, params: Any = None, **_kwargs: Any) -> Any:
        self.calls.append(("POST", self._path(url), json))
        return self.app.post(self._path(url), json=json, params=params)


@pytest.fixture
def gateway(site, monkeypatch) -> _Gateway:
    app = FastAPI()
    app.include_router(rag_api.router)
    app.dependency_overrides[rag_api.get_registry] = lambda: None
    app.state.umrs_db_pool = object()
    gw = _Gateway(TestClient(app))
    monkeypatch.setattr(HTTPCore, "_client", lambda _self, _timeout=None: gw)
    return gw


@pytest.fixture
def client(gateway) -> Client:
    return Client(config=Config(gateway_url=GATEWAY, api_key="k", project_id=PROJECT))


class _Agent:
    """The session agent: its model, its per-call guard and its own route."""

    def __init__(self, model: str, reply: str) -> None:
        self.model, self.reply = model, reply
        self._call_type = "turn"
        self.guarded: list[str] = []
        self.asked: list[Any] = []

    def _effective_model(self) -> str:
        return self.model

    def _pre_llm_call(self, call_type: str) -> None:
        self.guarded.append(call_type)

    def _chat(self, messages: Any, _tools: Any, **_kwargs: Any) -> str:
        self.asked.append(messages)
        return self.reply


def _session(client: Client, model: str, *, window: int, reply: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        client=client,
        agent=_Agent(model, reply),
        context=SimpleNamespace(max_context_tokens=window),
        messages=[],
        metadata={},
        _current_turn_id="t1",
    )


def _sizes(spec: Any) -> tuple[Any, ...]:
    """What a profile sizes on the spec, and who it was sized for."""
    return (
        spec.k_max,
        spec.pool,
        spec.per_artifact,
        spec.merge,
        spec.partial_k,
        spec.profile,
        spec.reader_model,
        spec.graph_lane,
    )


def _wire(gateway: _Gateway, index: int = 0) -> dict[str, Any]:
    return dict(gateway.calls[index][2] or {})


# ── the router: small and large readers ──────────────────────────────────────

#: The large profile's sizes (ADR-093 D-14; the medium sizes since MD-14,
#: 2026-10-13): the router asks for the pack's passages, the server cuts at
#: k_max, a partial verdict keeps partial_k.
LARGE = RETRIEVAL_PROFILES["large"]


def test_a_small_reader_retrieves_exactly_as_before_d14(site, client, gateway):
    session = _session(client, SMALL, window=STANDALONE_WINDOW)
    decision = route_turn_decision(session, QUESTION)
    wire = _wire(gateway)
    assert (wire["q"], wire["profile"], wire["reader_model"]) == ([QUESTION], "small", SMALL)
    # The pre-D-14 request: the router's 3 passages, the site's pool, merge
    # and per-artifact defaults, 2 when partial; no graph lane on a 3B site.
    assert _sizes(site.specs[0]) == (3, 50, 2, "auto", 2, "small", SMALL, "off")
    assert (site.dense_limits, site.merged, site.hops) == ([50], [], [])
    assert (decision.action, decision.status, len(decision.sids)) == ("pack", "sufficient", 3)
    assert decision.event_data()["profile"] == "small"
    assert session.agent.asked == []  # no expansion call for a small reader


@pytest.mark.parametrize("model", RIKYU)
def test_a_rikyu_reader_reads_the_large_profile_end_to_end(site, client, gateway, model):
    reply = '["' + '", "'.join(VARIANTS) + '"]'
    session = _session(client, model, window=RIKYU_WINDOW, reply=reply)
    decision = route_turn_decision(session, QUESTION)
    wire = _wire(gateway)
    assert (wire["q"], wire["profile"], wire["reader_model"]) == (
        [QUESTION, *VARIANTS],
        "large",
        model,
    )
    spec = site.specs[0]
    assert spec.queries == (QUESTION, *VARIANTS)  # fused server-side
    assert _sizes(spec) == (
        LARGE.pack_passages,
        LARGE.pool,
        2,
        "on",
        LARGE.partial_k,
        "large",
        model,
        "off",
    )
    assert (site.dense_limits, site.merged) == ([LARGE.pool], [LARGE.pack_passages])
    assert (decision.action, len(decision.sids), decision.variants) == (
        "pack",
        LARGE.pack_passages,
        3,
    )
    assert (session.agent.guarded, decision.event_data()["profile"]) == (["turn"], "large")


@pytest.mark.parametrize(
    ("model", "window", "shown"),
    [(SMALL, STANDALONE_WINDOW, 2), ("kimi-k3", RIKYU_WINDOW, LARGE.partial_k)],
)
def test_a_partial_verdict_shows_the_readers_partial_cap(site, client, model, window, shown):
    site.sigma = PARTIAL_SIGMA
    decision = route_turn_decision(_session(client, model, window=window), QUESTION)
    assert (decision.action, decision.status, len(decision.sids)) == ("pack", "partial", shown)
    assert 'match="partial"' in decision.message


@pytest.mark.parametrize(
    ("reader", "name", "partial"),
    [
        (SMALL, "small", 2),
        ("glm-5.3", "large", LARGE.partial_k),
        ("kimi-k3@rikyu", "large", LARGE.partial_k),
        (None, "small", 2),
    ],
)
def test_the_server_sizes_and_reports_the_profile_the_reader_names(
    site, client, reader, name, partial
):
    site.sigma = PARTIAL_SIGMA
    result = client.rag.retrieve(QUESTION, reader_model=reader)  # profile auto
    assert result.lanes["profile"] == {"status": "ok", "name": name, "reader_model": reader or ""}
    assert (result.sufficiency.status, len(result.passages)) == ("partial", partial)
    assert site.specs[0].partial_k == partial


# ── graph bridges on a large-extraction site ─────────────────────────────────


@pytest.mark.parametrize(
    ("sigma", "status", "cut"),
    [
        (SUFFICIENT_SIGMA, "sufficient", LARGE.pack_passages),
        (PARTIAL_SIGMA, "partial", LARGE.partial_k),
    ],
)
def test_graph_bridges_reach_a_large_reader_after_a_full_cut(
    site, client, monkeypatch, sigma, status, cut
):
    # The lane passed its multi-hop gate (D-14; review of ADR-093: auto stays
    # off until the maintainer sets UMRS_RAG_GRAPH_LANE_MEASURED).
    monkeypatch.setenv("UMRS_RAG_GRAPH_LANE_MEASURED", "1")
    site.model, site.sigma = "kimi-k3", sigma  # rikyu: extraction by kimi-k3
    decision = route_turn_decision(_session(client, "kimi-k3", window=RIKYU_WINDOW), QUESTION)
    assert (site.specs[0].graph_lane, len(site.hops)) == ("on", 1)
    assert (decision.status, len(decision.sids)) == (status, cut + 2)
    assert "art-90.pdf" in decision.message and "art-91.pdf" in decision.message


def test_each_bridge_comes_after_the_cut_and_names_its_entity(site, client, monkeypatch):
    monkeypatch.setenv("UMRS_RAG_GRAPH_LANE_MEASURED", "1")
    site.model = "glm-5.3"
    result = client.rag.retrieve(QUESTION, profile="large", reader_model="glm-5.3")
    bridges = [p for p in result.passages if is_graph_bridge(p)]
    assert [p.artifact_id for p in bridges] == ["art-90", "art-91"]
    assert list(result.passages[-2:]) == bridges  # appended, never displacing
    assert len(result.passages) == LARGE.k_max + 2  # the full large cut, plus two
    entities = [b["bridge_entity"] for b in result.lanes["graph"]["bridges"]]
    assert entities == ["NiFe-LDH", "OER"]


def test_an_unmeasured_lane_stays_off_at_a_large_extraction_site(site, client):
    """D-14: no default flips without a multi-hop probe; until the maintainer
    sets UMRS_RAG_GRAPH_LANE_MEASURED, auto is off even under kimi-k3."""
    site.model = "kimi-k3"
    result = client.rag.retrieve(QUESTION, profile="large", reader_model="kimi-k3")
    assert (site.specs[0].graph_lane, site.hops) == ("off", [])
    assert "UMRS_RAG_GRAPH_LANE_MEASURED unset" in result.lanes["graph"]["reason"]


def test_a_large_reader_on_a_3b_extraction_site_gets_no_graph_lane(site, client):
    result = client.rag.retrieve(QUESTION, profile="large", reader_model="kimi-k3")
    assert (site.specs[0].graph_lane, site.hops) == ("off", [])
    assert result.lanes["graph"]["status"] == "off"
    assert "llama3.2:3b" in result.lanes["graph"]["reason"]


# ── /rag/query: sized by the registry's answer model ────────────────────────


@pytest.mark.parametrize(
    ("model", "name", "k_max", "limit"),
    # the large cut is the profile's k_max (10 until ADR-093 MD-14; the
    # medium 6 since, 2026-10-13); its output limit stays unset
    [(SMALL, "small", 4, 400), ("glm-5.3", "large", LARGE.k_max, None)],
)
def test_rag_query_sizes_by_the_registry_answer_model(site, client, model, name, k_max, limit):
    site.model = model
    response = client.rag.query(QUESTION)
    spec, body = site.specs[0], site.chats[0]
    assert (spec.k_max, spec.partial_k > 0, spec.profile, spec.reader_model) == (
        k_max,
        True,
        name,
        model,
    )
    assert (body["model"], body.get("max_tokens"), body["temperature"]) == (model, limit, 0.0)
    assert ("max_tokens" in body) is (limit is not None)  # never "max_tokens": null
    assert response.answer == "Swelling slows sorption [1]."


# ── search_docs: the agent's own follow-up ───────────────────────────────────


def test_search_docs_sizes_the_cut_for_the_session_model_per_call(site, client, gateway):
    reading = {"model": "kimi-k3@rikyu"}
    tools = {
        t["name"]: t["execute"] for t in _client_tools(client, reader=lambda: reading["model"])
    }
    tools["search_docs"](QUESTION)
    reading["model"] = SMALL  # a /model switch
    tools["search_docs"](QUESTION)
    large, small = site.specs
    assert _sizes(large)[:2] + _sizes(large)[5:7] == (
        LARGE.k_max,
        LARGE.pool,
        "large",
        "kimi-k3@rikyu",
    )
    assert _sizes(small)[:2] + _sizes(small)[5:7] == (4, 50, "small", SMALL)
    assert "k_max" not in _wire(gateway, 0)  # the profile decides, not R4


def test_search_docs_without_a_session_model_sends_what_it_sent_before(site, client, gateway):
    tools = {t["name"]: t["execute"] for t in _client_tools(client)}
    tools["search_docs"](QUESTION)
    wire = _wire(gateway)
    assert "profile" not in wire and "reader_model" not in wire
    assert _sizes(site.specs[0]) == (4, 50, 2, "auto", 2, "small", "", "off")


def test_a_reader_name_past_the_routes_bound_is_clamped_not_refused(site, client):
    assert r4_rag.READER_MODEL_MAX_CHARS == rag_retrieve.READER_MODEL_CHARS
    name = "kimi-k3@" + "r" * 300
    result = client.rag.retrieve(QUESTION, reader_model=name)
    assert site.specs[0].reader_model == name[: rag_retrieve.READER_MODEL_CHARS]
    assert result.lanes["profile"]["name"] == "large"


# ── the eval harness: retrieve:large ─────────────────────────────────────────


def _stack(client: Client) -> Any:
    stack = object.__new__(backends.Stack)
    stack.client, stack.project, stack.graph = client, PROJECT, "scifact-eval"
    stack.corpus, stack.art_to_doc = None, {}
    return stack


def test_the_harness_large_preset_is_served_as_the_large_profile(site, client):
    knobs = arms.PRESETS["large"]
    got = _stack(client).retrieve(QUESTION, **knobs)
    spec = site.specs[0]
    # the large profile's sizes, served whatever they are (since ADR-093
    # MD-14, 2026-10-13, the medium ones: the preset reads the same table)
    assert _sizes(spec) == (
        LARGE.k_max,
        LARGE.pool,
        2,
        "on",
        LARGE.partial_k,
        "large",
        "kimi-k3",
        "off",
    )
    assert (spec.lexical, spec.rerank, spec.cutoff) == ("on", "off", "adaptive")
    assert arms.unmet_stages(knobs, got.meta["lanes"]) == []  # the row is a measurement
    assert got.meta["lanes"]["profile"]["name"] == "large"


def test_the_harness_merge_preset_stays_the_sites_small_profile(site, client):
    knobs = arms.PRESETS["merge"]
    got = _stack(client).retrieve(QUESTION, **knobs)
    assert _sizes(site.specs[0]) == (4, 50, 2, "on", 2, "small", "", "off")
    assert arms.unmet_stages(knobs, got.meta["lanes"]) == []


# ── wave D: one size rule in both repositories (2026-10-10) ─────────────────

MEDIUM_READERS = ("qwen3-30b-a3b", "gpt-oss-20b", "house-model")


@pytest.mark.parametrize("model", MEDIUM_READERS)
def test_a_moe_or_unknown_reader_is_medium_in_r4_and_in_umrs(site, client, gateway, model):
    decision = route_turn_decision(_session(client, model, window=RIKYU_WINDOW), QUESTION)
    wire = _wire(gateway)
    assert (wire["profile"], wire["reader_model"]) == ("medium", model)
    assert _sizes(site.specs[0]) == (4, 50, 2, "on", 3, "medium", model, "off")
    assert (decision.action, decision.event_data()["profile"]) == ("pack", "medium")
    # UMRS alone (profile auto) sizes the same reader the same way.
    result = client.rag.retrieve(QUESTION, reader_model=model)
    assert result.lanes["profile"]["name"] == "medium"
    assert _sizes(site.specs[1])[1:6] == (50, 2, "on", 3, "medium")


def test_an_unknown_reader_gets_one_notice_per_session(site, client):
    session = _session(client, "house-model", window=RIKYU_WINDOW)
    notices: list[tuple[Any, ...]] = []
    session.agent._emit = lambda *event: notices.append(event)
    route_turn_decision(session, QUESTION)
    route_turn_decision(session, QUESTION)
    assert len(notices) == 1
    _kind, message, data = notices[0]
    assert "r4 model scale set house-model" in message
    assert (data["kind"], data["profile"]) == ("model_scale", "medium")


@pytest.mark.parametrize(("model", "k_max", "limit"), [("qwen3-30b-a3b", 6, 600)])
def test_rag_query_reads_a_moe_answer_model_as_medium(site, client, model, k_max, limit):
    site.model = model  # the registry's answer (and extraction) model
    client.rag.query(QUESTION)
    spec, body = site.specs[0], site.chats[0]
    assert (spec.k_max, spec.profile, spec.graph_lane) == (k_max, "medium", "off")
    assert body["max_tokens"] == limit
