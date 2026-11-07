"""ADR-093 wave A: R4's side of the retrieve verb against the REAL UMRS route.

The R4 client (``RAGClient.retrieve`` / ``fetch_passage``) and the companion's
pre-retrieval router talk to UMRS's own ``GET /umrs/rag/retrieve`` and
``/umrs/rag/passages/{node_id}`` routes through the gateway's
``/gw/rag/* -> /umrs/rag/*`` rewrite, in process. The retrieval core behind
the route is a recorder here (``umrs/tests/test_retrieval_end_to_end.py``
drives the real core over a SciFact-shaped corpus); what this file pins is the
seam: every knob the client sends is one the route accepts and reaches the
core's spec, the route's JSON parses back into the very ``RetrieveResult``
the core returned, and the router's request is one the route serves
(integrator, 2026-10-10).
"""

from __future__ import annotations

import dataclasses
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from umrs.api import rag as rag_api
from umrs.api import rag_retrieve, rag_spine
from umrs.knowledge.retrieval import pipeline

from r4._http import HTTPCore
from r4.client import Client
from r4.companion.context_budget import RetrievalProfile
from r4.companion.evidence_ledger import EvidenceLedger
from r4.companion.retrieval_router import RetrievalRouter
from r4.config import Config
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

GATEWAY = "http://gw.test"
QUESTION = "What slows moisture sorption in hydrogels?"


def _passage(rank: int, artifact: str) -> RetrievedPassage:
    return RetrievedPassage(
        rank=rank,
        source_id=f"node-chunk-{artifact}-0",
        artifact_id=artifact,
        source_name=f"{artifact}.pdf",
        text=f"Swelling of the {artifact} hydrogel slows moisture sorption.",
        section="Results",
        chunk_count=2,
        span=(0, 58),
        text_source="search_text",
        scores={"dense": 0.93 - rank / 100, "fused": 0.03, "lexical": 6.1},
        lanes=("dense", "lex"),
    )


def _result(status: str = "sufficient", n: int = 3) -> RetrieveResult:
    return RetrieveResult(
        query=QUESTION,
        passages=tuple(_passage(rank, f"art-{rank}") for rank in range(1, n + 1)),
        sufficiency=RetrievalSufficiency(status=status, basis="z_null", value=4.2, threshold=3.0),
        searched=1842,
        space="e5-base-v2",
        lanes={"dense": {"status": "ok", "n": 50, "in_scope": 1842}, "lex": {"status": "ok"}},
        timing_ms={"encode": 12.0, "dense": 40.5, "total": 80.25},
        trace={"mode": "fused"},
    )


class _Core:
    """Stands in for ``pipeline.retrieve`` and ``resolve_graphs``; records the
    spec the route built and the raw graph selection it received."""

    def __init__(self) -> None:
        self.result = _result()
        self.specs: list[Any] = []
        self.graphs: list[tuple[str | None, str | None]] = []

    def retrieve(self, _pool: Any, spec: Any, **_kwargs: Any) -> RetrieveResult:
        self.specs.append(spec)
        return self.result

    def resolve(self, _request: Any, _db: Any, spec: Any, graphs: Any, exclude: Any) -> Any:
        self.graphs.append((graphs, exclude))
        split = tuple(g for g in (graphs or "").split(",") if g) or None
        excluded = tuple(g for g in (exclude or "").split(",") if g)
        return dataclasses.replace(spec, graph_ids=split, exclude_graph_ids=excluded)


class _Gateway:
    """The gateway rewrite in front of the UMRS app, as R4's httpx client sees it."""

    def __init__(self, app: TestClient) -> None:
        self.app = app
        self.paths: list[str] = []

    def __enter__(self) -> _Gateway:
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def get(self, url: str, params: Any = None, **_kwargs: Any) -> Any:
        path = urlsplit(url).path.replace("/gw/rag/", "/umrs/rag/", 1)
        self.paths.append(path)
        return self.app.get(path, params=params)


@pytest.fixture
def core(monkeypatch) -> _Core:
    recorder = _Core()
    monkeypatch.setattr(pipeline, "retrieve", recorder.retrieve)
    monkeypatch.setattr(rag_retrieve, "resolve_graphs", recorder.resolve)
    monkeypatch.setattr(rag_retrieve, "batch_encoder", lambda: "ENCODER")
    monkeypatch.setattr(rag_retrieve, "site_reranker", lambda: None)
    monkeypatch.setattr(rag_spine, "_artifact_fetcher", lambda: "FETCH")
    monkeypatch.setattr(
        rag_api,
        "_require_rag_scope",
        lambda _request, target=None, **_kw: ("u1", target or "p-verified"),
    )
    return recorder


@pytest.fixture
def gateway(core, monkeypatch) -> _Gateway:
    app = FastAPI()
    app.include_router(rag_api.router)
    app.state.umrs_db_pool = object()
    gw = _Gateway(TestClient(app))
    monkeypatch.setattr(HTTPCore, "_client", lambda _self, _timeout=None: gw)
    return gw


@pytest.fixture
def client(gateway) -> Client:
    return Client(config=Config(gateway_url=GATEWAY, api_key="k", project_id="p-verified"))


def test_the_routes_json_parses_back_into_the_cores_result(client, core, gateway):
    result = client.rag.retrieve(QUESTION)
    assert result == core.result  # to_dict on the server, from_dict in R4: lossless
    assert gateway.paths == ["/umrs/rag/retrieve"]
    spec = core.specs[0]
    assert (spec.queries, spec.project_id, spec.k_max, spec.threshold) == (
        (QUESTION,),
        "p-verified",
        4,  # the site default: the client sent no k_max
        None,
    )


def test_every_knob_the_client_sends_reaches_the_cores_spec(client, core):
    client.rag.retrieve(
        [QUESTION, "hydrogel swelling moisture"],
        project_id="p2",
        graphs=["g-a", "g-b"],
        exclude_graphs=["g-c"],
        k_max=6,
        pool=80,
        per_artifact=3,
        lexical="on",
        rerank="off",
        merge="on",
        cutoff="fixed",
        trace=True,
    )
    spec = core.specs[0]
    assert spec.queries == (QUESTION, "hydrogel swelling moisture")
    assert (spec.project_id, spec.k_max, spec.pool, spec.per_artifact) == ("p2", 6, 80, 3)
    assert (spec.lexical, spec.rerank, spec.merge, spec.cutoff, spec.trace) == (
        "on",
        "off",
        "on",
        "fixed",
        True,
    )
    assert core.graphs == [("g-a,g-b", "g-c")]  # csv, as the route resolves it
    assert spec.graph_ids == ("g-a", "g-b") and spec.exclude_graph_ids == ("g-c",)


def test_legacy_mode_and_the_k_max_clamp_are_requests_the_route_accepts(client, core):
    client.rag.retrieve(QUESTION, threshold=0.7, k_max=40)  # the route takes 1-12
    spec = core.specs[0]
    assert (spec.threshold, spec.k_max) == (0.7, 12)


def test_the_fetch_handle_reaches_the_route(client, monkeypatch, gateway):
    seen: list[tuple] = []

    def fetch(_request, _db, project_id, node_id, window, max_chars):
        seen.append((project_id, node_id, window, max_chars))
        return {"source_id": node_id, "text": "full chunk", "window": window}

    monkeypatch.setattr(rag_retrieve, "fetch_passage", fetch)
    body = client.rag.fetch_passage("node-chunk-g-1a2b-art-7-3", window=2, max_chars=900)
    assert body["text"] == "full chunk"
    assert seen == [("p-verified", "node-chunk-g-1a2b-art-7-3", 2, 900)]
    assert gateway.paths == ["/umrs/rag/passages/node-chunk-g-1a2b-art-7-3"]


def _route(client: Client, message: str) -> Any:
    router = RetrievalRouter(client.rag.retrieve, EvidenceLedger(), scanner=_PassAll())
    profile = RetrievalProfile(size_class="small", k_max=3, max_tokens=1200)
    return router.route(message, mode="on", project_id="p-verified", profile=profile)


class _PassAll:
    def scan(self, *_args: Any, **_kwargs: Any) -> Any:
        return type("Scan", (), {"risk_level": "low", "is_blocked": False})()


def test_the_router_retrieves_through_the_route_and_packs_its_passages(client, core):
    decision = _route(client, QUESTION)
    spec = core.specs[0]
    assert (spec.queries, spec.k_max, spec.project_id) == ((QUESTION,), 3, "p-verified")
    assert decision.action == "pack" and decision.status == "sufficient"
    assert decision.sids == ("S1", "S2", "S3")
    assert "[S1] art-1.pdf › Results" in decision.message
    assert decision.message.rstrip().endswith("cite passages as [S1].")
    assert decision.message.index("</passages>") < decision.message.index(f"Question: {QUESTION}")


def test_the_router_follows_the_routes_insufficient_verdict(client, core):
    core.result = _result(status="insufficient", n=1)
    decision = _route(client, QUESTION)
    assert decision.action == "note" and decision.sids == ()
    assert "art-1" not in decision.message  # no passage reaches the model
