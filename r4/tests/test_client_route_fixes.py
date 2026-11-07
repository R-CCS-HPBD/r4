"""Client route fixes for the liveness offenders (cleanup C-2(v), plan H-10.2).

2026-10-18. Each test records the exact request path a client builds, in both
modes (``_api_prefix`` ``/gw`` behind the edge, ``/umrs`` direct), and checks
the direct path against the hermetic UMRS route inventory
(``tools/route_inventory.base.json``):

* ``ModelClient.provision_endpoint`` / ``get_provision_status`` called
  ``/models/{name}/provision`` (no route in either mode);
* ``A2LClient`` hard-coded ``/gw/a2l`` (404 direct; UMRS mounts
  ``/umrs/a2l/ledger``);
* ``KnowledgeClient`` K2 ingest calls 404'd direct (UMRS mounts ``/umrs/k2``,
  only the edge maps ``/gw/knowledge/ingest/*`` there), and the export
  fallback read ``{api}/catalog`` and ``{api}/knowledge/artifacts``, which
  nothing serves (the K2 lane lists artifacts).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from r4.a2l_client import A2LClient
from r4.config import Config
from r4.knowledge import KnowledgeClient
from r4.models import ModelClient, PolicyDeniedError, ProvisionStatus

GATEWAY = "http://edge.test"
MODES = ("/gw", "/umrs")
ROUTE_INVENTORY = Path(__file__).resolve().parents[2] / "tools" / "route_inventory.base.json"


def _inventory() -> list[dict[str, Any]]:
    if not ROUTE_INVENTORY.is_file():
        pytest.skip("route inventory not present (r4 outside the platform repo)")
    return json.loads(ROUTE_INVENTORY.read_text())["routes"]


def _route_regex(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$")


def assert_umrs_route(method: str, path: str) -> None:
    """The direct-mode path is a mounted UMRS route for ``method``."""
    for route in _inventory():
        if method in route["methods"] and _route_regex(route["path"]).match(path):
            return
    raise AssertionError(f"no mounted UMRS route for {method} {path}")


# ---------------------------------------------------------------------------
# ModelClient: provision through the endpoints API
# ---------------------------------------------------------------------------


class _Recorder:
    """httpx MockTransport handler that records requests and answers by path."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, Any]]):
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        params = dict(request.url.params)
        self.calls.append((request.method, request.url.path, params, body))
        status, payload = self.answers.get((request.method, request.url.path), (404, {}))
        return httpx.Response(status, json=payload)


def _model_client(monkeypatch, mode: str, answers) -> tuple[ModelClient, _Recorder]:
    recorder = _Recorder(answers)
    real_client = httpx.Client

    def _client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(recorder)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("r4.models.httpx.Client", _client)
    config = Config(gateway_url=GATEWAY, api_key="test-key", project_id="p1")
    client = ModelClient(config=config)
    client._is_umrs_direct = mode == "/umrs"
    return client, recorder


@pytest.mark.parametrize("mode", MODES)
def test_download_status_reads_jobs_in_the_mode_prefix(monkeypatch, mode):
    # It hard-coded /gw/jobs/{id}, which 404s in direct mode (2026-10-18).
    path = f"{mode}/jobs/job-1"
    answers = {("GET", path): (200, {"job_id": "job-1", "status": "running"})}
    client, recorder = _model_client(monkeypatch, mode, answers)

    client.get_download_status("job-1")

    assert [(m, p) for m, p, _q, _b in recorder.calls] == [("GET", path)]
    if mode == "/umrs":
        assert_umrs_route("GET", path)


READY_ENDPOINT = {
    "model": "qwen",
    "status": "ready",
    "litellm_url": "http://node1:8000/v1",
    "tier": "project",
}


@pytest.mark.parametrize("mode", MODES)
def test_provision_endpoint_posts_endpoints_provision(monkeypatch, mode):
    path = f"{mode}/endpoints/provision"
    answers = {("POST", path): (200, {"status": "ready", "endpoint": READY_ENDPOINT})}
    client, recorder = _model_client(monkeypatch, mode, answers)

    result = client.provision_endpoint("qwen")

    assert recorder.calls == [("POST", path, {}, {"model": "qwen", "prefer_warm": True})]
    assert result.status is ProvisionStatus.READY
    assert result.endpoint_url == "http://node1:8000/v1"
    assert result.endpoint_type is not None and result.endpoint_type.value == "project"
    if mode == "/umrs":
        assert_umrs_route("POST", path)


def test_provision_endpoint_maps_in_progress_reply(monkeypatch):
    answers = {
        ("POST", "/gw/endpoints/provision"): (
            202,
            {"status": "provisioning", "job_id": "j1", "retry_after": 30},
        )
    }
    client, _ = _model_client(monkeypatch, "/gw", answers)

    result = client.provision_endpoint("qwen")

    assert result.status is ProvisionStatus.PROVISIONING
    assert result.estimated_wait == 30
    assert result.endpoint_url is None


def test_provision_endpoint_403_is_policy_denial(monkeypatch):
    answers = {("POST", "/gw/endpoints/provision"): (403, {"detail": "model ACL"})}
    client, _ = _model_client(monkeypatch, "/gw", answers)

    with pytest.raises(PolicyDeniedError, match="model ACL"):
        client.provision_endpoint("qwen")


@pytest.mark.parametrize("mode", MODES)
def test_provision_status_reads_active_endpoints(monkeypatch, mode):
    active = f"{mode}/endpoints/active"
    answers = {("GET", active): (200, {"endpoints": [READY_ENDPOINT], "total": 1})}
    client, recorder = _model_client(monkeypatch, mode, answers)

    result = client.get_provision_status("qwen")

    assert recorder.calls == [("GET", active, {"model": "qwen"}, None)]
    assert result.status is ProvisionStatus.READY
    assert result.endpoint_url == "http://node1:8000/v1"
    if mode == "/umrs":
        assert_umrs_route("GET", active)


@pytest.mark.parametrize("mode", MODES)
def test_provision_status_falls_back_to_project_list(monkeypatch, mode):
    listing = {
        "endpoints": [
            {"model": "other", "status": "ready"},
            {"model": "qwen", "status": "failed", "error_message": "OOM"},
            {"model": "qwen", "status": "starting", "detail": "loading model"},
        ]
    }
    answers = {
        ("GET", f"{mode}/endpoints/active"): (200, {"endpoints": [], "total": 0}),
        ("GET", f"{mode}/endpoints"): (200, listing),
    }
    client, recorder = _model_client(monkeypatch, mode, answers)

    result = client.get_provision_status("qwen")

    assert [(m, p, q) for m, p, q, _ in recorder.calls] == [
        ("GET", f"{mode}/endpoints/active", {"model": "qwen"}),
        ("GET", f"{mode}/endpoints", {"status_filter": "pending,provisioning,starting,failed"}),
    ]
    assert result.status is ProvisionStatus.PROVISIONING
    assert result.message == "loading model"
    if mode == "/umrs":
        assert_umrs_route("GET", f"{mode}/endpoints")


def test_provision_status_list_refusal_is_not_a_policy_denial(monkeypatch):
    """No project header and not admin: UMRS refuses the cross-project list."""
    answers = {
        ("GET", "/gw/endpoints/active"): (200, {"endpoints": []}),
        ("GET", "/gw/endpoints"): (403, {"detail": "requires admin role"}),
    }
    client, _ = _model_client(monkeypatch, "/gw", answers)

    result = client.get_provision_status("qwen")

    assert result.status is ProvisionStatus.PENDING


def test_provision_status_without_endpoint_is_pending(monkeypatch):
    answers = {
        ("GET", "/gw/endpoints/active"): (200, {"endpoints": []}),
        ("GET", "/gw/endpoints"): (200, {"endpoints": []}),
    }
    client, _ = _model_client(monkeypatch, "/gw", answers)

    result = client.get_provision_status("qwen")

    assert result.status is ProvisionStatus.PENDING
    assert "r4 endpoint up --model qwen" in (result.message or "")


# ---------------------------------------------------------------------------
# A2LClient: mode-aware ledger base
# ---------------------------------------------------------------------------

A2L_BASES = {"/gw": "/gw/a2l", "/umrs": "/umrs/a2l/ledger"}


def _a2l(mode: str, reply: dict[str, Any]) -> tuple[A2LClient, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def _request(method: str, url: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append((method, url))
        return reply

    http = SimpleNamespace(request=_request)
    parent = SimpleNamespace(
        platform_http=http,
        config=SimpleNamespace(gateway_url=GATEWAY + "/"),
        _api_prefix=mode,
    )
    return A2LClient(parent), calls  # type: ignore[arg-type]


def _drive_a2l(a2l: A2LClient) -> None:
    a2l.derive(["p"], "op", {}, "t", "s3://b/k", "alice", "sha256:x")
    a2l.list_artifacts()
    a2l.get_artifact("art1")
    a2l.create_execution(agent_name="agent", goal="g", user_id="alice")
    a2l.get_execution("ex1")
    a2l.update_execution("ex1", status="completed")


@pytest.mark.parametrize("mode", MODES)
def test_a2l_client_uses_mode_ledger_base(mode):
    reply = {"id": "a", "execution_id": "ex1", "agent_name": "agent", "items": []}
    a2l, calls = _a2l(mode, reply)

    _drive_a2l(a2l)

    base = GATEWAY + A2L_BASES[mode]
    assert calls == [
        ("POST", f"{base}/artifacts/derive"),
        ("GET", f"{base}/artifacts"),
        ("GET", f"{base}/artifacts/art1"),
        ("POST", f"{base}/agent-executions"),
        ("GET", f"{base}/agent-executions/ex1"),
        ("PATCH", f"{base}/agent-executions/ex1"),
    ]
    if mode == "/umrs":
        for method, url in calls:
            assert_umrs_route(method, urlsplit(url).path)


# ---------------------------------------------------------------------------
# KnowledgeClient: K2 ingest lane and the catalog read
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, mode: str):
        self._api_prefix = mode
        self.calls: list[tuple[str, str, Any]] = []

    def post(self, url: str, json: Any = None, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(("POST", url, json))
        return {"status": "queued"}

    def get(self, url: str, params: Any = None, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(("GET", url, params))
        if url.endswith("/catalog/search"):
            return {"results": [{"s3_key": "p1/a.csv"}]}
        if url.endswith("/artifacts"):
            return {"artifacts": [{"id": "a1"}], "total": 1, "page": 1, "page_size": 100}
        return {}


K2_LANES = {"/gw": "/gw/knowledge/ingest", "/umrs": "/umrs/k2"}


@pytest.mark.parametrize("mode", MODES)
def test_k2_ingest_calls_use_mode_lane(mode):
    fake = _FakeClient(mode)
    knowledge = KnowledgeClient(fake)  # type: ignore[arg-type]

    knowledge.ingest_sources("p1", [{"kind": "url", "url": "https://x.test"}])
    knowledge.submit_verify("p1")
    knowledge.emit_workflow({"objectives": []}, project_id="p1")

    lane = K2_LANES[mode]
    assert [(m, u) for m, u, _ in fake.calls] == [
        ("POST", f"{lane}/ingest"),
        ("POST", f"{lane}/verify"),
        ("POST", f"{lane}/emit-workflow"),
    ]
    if mode == "/umrs":
        for method, url, _ in fake.calls:
            assert_umrs_route(method, url)


@pytest.mark.parametrize("mode", MODES)
def test_export_fallback_reads_catalog_search(mode):
    fake = _FakeClient(mode)
    knowledge = KnowledgeClient(fake)  # type: ignore[arg-type]

    result = knowledge._export_fallback("p1", RuntimeError("404"))

    catalog = [c for c in fake.calls if "/catalog" in c[1]]
    assert catalog == [("GET", f"{mode}/catalog/search", {"prefix": "p1/", "limit": 100})]
    assert result["fallback_data"]["catalog"] == [{"s3_key": "p1/a.csv"}]
    if mode == "/umrs":
        assert_umrs_route("GET", f"{mode}/catalog/search")


@pytest.mark.parametrize("mode", MODES)
def test_export_fallback_lists_artifacts_on_k2_lane(mode):
    fake = _FakeClient(mode)
    knowledge = KnowledgeClient(fake)  # type: ignore[arg-type]

    result = knowledge._export_fallback("p1", RuntimeError("404"))

    listing = f"{K2_LANES[mode]}/artifacts"
    assert ("GET", listing, {"page_size": 100}) in fake.calls
    assert result["fallback_data"]["artifacts"] == [{"id": "a1"}]
    if mode == "/umrs":
        assert_umrs_route("GET", listing)
