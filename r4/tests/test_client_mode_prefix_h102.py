"""Client route liveness clean-sweep fixtures (plan H-10.2, 2026-10-20).

The 2026-10-19 audit sweep listed 126 client paths that reached no route
(DIRECT_GW_LITERAL / DIRECT_NO_ROUTE / EDGE_* classes). The wave that closed
them did three things, and each has its pin here (every test fails on the
pre-wave code):

* hard-coded ``/gw/…`` literals now ride the shared mode probe
  (``r4._http.probe_api_prefix``) — direct-UMRS deployments get ``/umrs/…``;
* modes that diverge mid-path (A2L ``/a2l`` → ``/a2l/ledger``, plan-records
  route) follow the probe too;
* client surfaces whose routes never existed server-side (KnowledgeClient
  Phases 4–13, workflow server lint, the mcp-use streamable endpoint) fail
  loudly with the live lane named, instead of a 404 trail.

Checked paths stay honest against the hermetic UMRS route inventory where a
direct call is asserted (same contract as test_client_route_fixes.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import r4._http as r4_http
from r4.errors import R4Error

GATEWAY = "http://edge.test"
ROUTE_INVENTORY = Path(__file__).resolve().parents[2] / "tools" / "route_inventory.base.json"


def _route_regex(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$")


def assert_umrs_route(method: str, path: str) -> None:
    """The direct-mode path is a mounted UMRS route for ``method``."""
    if not ROUTE_INVENTORY.is_file():
        pytest.skip("route inventory not present (r4 outside the platform repo)")
    routes = json.loads(ROUTE_INVENTORY.read_text())["routes"]
    for route in routes:
        if method in route["methods"] and _route_regex(route["path"]).match(path):
            return
    raise AssertionError(f"no mounted UMRS route for {method} {path}")


@pytest.fixture(autouse=True)
def _clear_prefix_cache():
    r4_http._API_PREFIX_CACHE.clear()
    yield
    r4_http._API_PREFIX_CACHE.clear()


def _stub_probe(prefix: str) -> None:
    """Pin the probe's answer by seeding the memo (no network leg runs)."""
    r4_http._API_PREFIX_CACHE[GATEWAY] = prefix


# ---------------------------------------------------------------------------
# probe_api_prefix
# ---------------------------------------------------------------------------


def test_probe_defaults_edge_when_unreachable(monkeypatch):
    monkeypatch.setattr(r4_http, "_probe_api_prefix_uncached", lambda *_a: "/gw")
    assert r4_http.probe_api_prefix("http://gone.test") == "/gw"


def test_probe_detects_direct_umrs(monkeypatch):
    monkeypatch.setattr(r4_http, "_probe_api_prefix_uncached", lambda *_a: "/umrs")
    assert r4_http.probe_api_prefix("http://umrs.test:8000") == "/umrs"


def test_probe_reads_config_gateway_url_and_memoizes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        r4_http,
        "_probe_api_prefix_uncached",
        lambda gateway, verify: calls.append(gateway) or "/umrs",
    )
    config = SimpleNamespace(gateway_url="http://umrs.test:8000/", verify_ssl=False)
    assert r4_http.probe_api_prefix(config) == "/umrs"
    assert r4_http.probe_api_prefix(config) == "/umrs"
    assert calls == ["http://umrs.test:8000"]  # one probe per base URL


# ---------------------------------------------------------------------------
# FilesClient / VisionClient — /gw literals map in direct mode
# ---------------------------------------------------------------------------


def _config() -> Any:
    from r4.config import Config

    return Config(gateway_url=GATEWAY, api_key="test-key", project_id="p1")


@pytest.mark.parametrize("prefix,expected", [("/gw", "/gw"), ("/umrs", "/umrs")])
def test_files_url_follows_the_mode(prefix, expected):
    from r4.files import FilesClient

    c = FilesClient(config=_config())
    c._prefix = prefix  # pin the probe
    assert c._url("/gw/files/upload") == f"{GATEWAY}{expected}/files/upload"
    assert c._url("/gw/buckets") == f"{GATEWAY}{expected}/buckets"


def test_files_json_buckets_direct_route():
    from r4.files import FilesClient

    c = FilesClient(config=_config())
    c._prefix = "/umrs"
    assert c._url("/gw/buckets").endswith("/umrs/buckets")
    assert_umrs_route("POST", "/umrs/buckets")
    assert_umrs_route("GET", "/umrs/files/list")


@pytest.mark.parametrize("prefix,expected", [("/gw", "/gw"), ("/umrs", "/umrs")])
def test_vision_url_follows_the_mode(prefix, expected):
    from r4.vision import VisionClient

    c = VisionClient(config=_config())
    c._prefix = prefix
    assert c._url("/gw/vision/infer/submit") == f"{GATEWAY}{expected}/vision/infer/submit"


def test_vision_direct_route_exists():
    from r4.vision import VisionClient

    c = VisionClient(config=_config())
    c._prefix = "/umrs"
    path = c._url("/gw/vision/infer/submit").split(GATEWAY, 1)[1]
    assert_umrs_route("POST", path)


# ---------------------------------------------------------------------------
# a2l_base_url — the mid-path ledger split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix,expected",
    [("/gw", f"{GATEWAY}/gw/a2l"), ("/umrs", f"{GATEWAY}/umrs/a2l/ledger")],
)
def test_a2l_base_url_split(prefix, expected):
    from r4.a2l_client import a2l_base_url

    client = SimpleNamespace(_api_prefix=prefix, config=SimpleNamespace(gateway_url=GATEWAY + "/"))
    assert a2l_base_url(client) == expected


def test_a2l_direct_health_route_exists():
    assert_umrs_route("GET", "/umrs/a2l/ledger/health")


# ---------------------------------------------------------------------------
# Client project / storage rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/gw", "/umrs"])
def test_project_row_paths_follow_the_prefix(prefix):
    from unittest.mock import MagicMock, patch

    from r4.client import Client

    seen: dict[str, Any] = {}
    mock_core = MagicMock()
    mock_core.put = lambda url, **kw: seen.setdefault("put", url) or {}
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
        client = Client(config=_config())
        client._is_umrs_direct = prefix == "/umrs"
        client.get = lambda path, **kw: seen.setdefault("get", path) or {}  # type: ignore[assignment]
        client.patch = lambda path, json=None: seen.setdefault("patch", path) or {}  # type: ignore[assignment]
        client.post = lambda path, json=None, **kw: seen.setdefault("post", path) or {}  # type: ignore[assignment]
        client.delete = lambda path, **kw: seen.setdefault("delete", path) or {}  # type: ignore[assignment]

        client.get_platform_project("p1")
        client.set_project_data_class("p1", "non_sensitive")
        client.set_project_storage("p1", s3_url="s3://x", check=False)
        client.check_project_storage("p1")
        client.clear_project_storage("p1")

    assert seen["get"] == f"{prefix}/projects/p1"
    assert seen["patch"] == f"{prefix}/projects/p1"
    assert seen["put"].endswith(f"{prefix}/projects/p1/storage")
    for key in ("post", "delete"):
        assert seen[key].startswith(f"{prefix}/projects/p1/storage")
    if prefix == "/umrs":
        assert_umrs_route("GET", seen["get"])
        assert_umrs_route("PATCH", seen["patch"])


# ---------------------------------------------------------------------------
# UmrsPlanRecords — default route follows the client's mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/gw", "/umrs"])
def test_plan_records_default_route_follows_mode(prefix):
    from r4.teams.durable_records import UmrsPlanRecords

    client = SimpleNamespace(
        _api_prefix=prefix, config=SimpleNamespace(gateway_url=GATEWAY), http=None
    )
    api = UmrsPlanRecords(client)
    assert api._route == f"{prefix}/traces/plan-runs"


def test_plan_records_venue_route_untouched():
    from r4.teams.durable_records import UmrsPlanRecords

    client = SimpleNamespace(config=SimpleNamespace(gateway_url=GATEWAY), http=None)
    api = UmrsPlanRecords(client, route="/traces/plan-runs")
    assert api._route == "/traces/plan-runs"


# ---------------------------------------------------------------------------
# LLM base endpoints (config + model routing)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/gw", "/umrs"])
def test_effective_endpoint_follows_the_mode(prefix):
    from r4.config import Config

    _stub_probe(prefix)
    config = Config(gateway_url=GATEWAY, api_key="k", project_id="p1")
    assert config.effective_endpoint == f"{GATEWAY}{prefix}/llm"


@pytest.mark.parametrize("prefix", ["/gw", "/umrs"])
def test_gateway_llm_endpoint_follows_the_mode(prefix):
    from r4.model_routing import gateway_llm_endpoint

    _stub_probe(prefix)
    config = SimpleNamespace(gateway_url=GATEWAY, verify_ssl=True)
    assert gateway_llm_endpoint(config) == f"{GATEWAY}{prefix}/llm"


# ---------------------------------------------------------------------------
# Retired surfaces fail loudly (never reach HTTP)
# ---------------------------------------------------------------------------


def _knowledge() -> Any:
    from r4.knowledge import KnowledgeClient

    client = SimpleNamespace(_api_prefix="/gw")
    return KnowledgeClient(client)


def test_knowledge_extract_retired_names_the_lane():
    k = _knowledge()
    with pytest.raises(R4Error, match="never mounted.*understand"):
        k.extract("p1", "src-1")


def test_knowledge_link_retired_names_proposals():
    k = _knowledge()
    with pytest.raises(R4Error, match="graph_propose"):
        k.link("p1", "a", "b", "cites")


def test_knowledge_verify_status_retired_names_the_job_lane():
    k = _knowledge()
    with pytest.raises(R4Error, match="submit_verify"):
        k.verify_status("p1")


def test_knowledge_coverage_pair_retired():
    k = _knowledge()
    with pytest.raises(R4Error, match="readiness"):
        k.coverage("p1")
    with pytest.raises(R4Error, match="readiness"):
        k.coverage_gaps("p1")


def test_knowledge_memory_pair_retired():
    k = _knowledge()
    with pytest.raises(R4Error, match="never mounted"):
        k.memory("p1")
    with pytest.raises(R4Error, match="ingest_paths"):
        k.memory_add("p1", "finding", "text")


def test_knowledge_plan_and_list_plans_retired():
    k = _knowledge()
    with pytest.raises(R4Error, match="start_loop"):
        k.plan("p1", "goal")
    with pytest.raises(R4Error, match="list_loops"):
        k.list_plans("p1")


def test_knowledge_get_and_approve_plan_retired():
    k = _knowledge()
    with pytest.raises(R4Error, match="loop"):
        k.get_plan("p1", "plan-1")
    with pytest.raises(R4Error, match="loop"):
        k.approve_plan("p1", "plan-1")


def test_knowledge_correct_retired_names_proposals():
    k = _knowledge()
    with pytest.raises(R4Error, match="graph_propose"):
        k.correct("p1", "node-1", "fix")


def test_workflow_server_lint_retired_in_both_modes(tmp_path):
    from r4.workflows import WorkflowClient

    draft = tmp_path / "workflow.json"
    draft.write_text("{}")
    for prefix in ("/gw", "/umrs"):
        parent = SimpleNamespace(_api_prefix=prefix, config=_config())
        with pytest.raises(R4Error, match="server-side workflow lint is not available"):
            WorkflowClient(parent).lint_file(str(draft))


def test_create_gateway_mcp_client_retired(monkeypatch):
    from r4.integrations import mcp_use

    monkeypatch.setattr(mcp_use, "_check_mcp_use_available", lambda: True)
    with pytest.raises(R4Error, match="mcp/call"):
        mcp_use.create_gateway_mcp_client("http://edge.test", "p1")


# ---------------------------------------------------------------------------
# Retired lanes degrade inside the explorer read model
# ---------------------------------------------------------------------------


def test_explorer_project_data_survives_retired_lanes():
    from r4.knowledge_explorer.data import fetch_project_data

    client = SimpleNamespace(_api_prefix="/gw")
    from r4.knowledge import KnowledgeClient

    knowledge = KnowledgeClient(client)
    knowledge.status = lambda pid: {"artifacts": []}  # type: ignore[assignment]
    knowledge.findings = lambda pid: []  # type: ignore[assignment]
    knowledge.readiness = lambda pid: {"readiness_level": "partial"}  # type: ignore[assignment]

    data = fetch_project_data(knowledge, "p1")
    assert data["memory"] == []  # retired lane degrades, never aborts
    assert data["plans"] == []
    assert data["readiness_level"] == "partial"
