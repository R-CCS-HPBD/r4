"""TUI audit 2026-10-17, client lane: r4 client calls that missed their UMRS route.

#72/#96: ``check_server_health`` POSTed ``/gw/mcp/servers/{id}/health``, which
no UMRS router serves (404 on every bridge/ACP/CLI health check); every MCP
URL also hard-coded ``/gw`` so direct-UMRS mode 404'd.
The registry row says ``healthy`` from registration on and nothing updates
it, so health is a client-side reachability probe of the recorded URL.
#93: ``ModelClient.list_models`` sent ``type`` where the route reads
``model_type``, and the grant-aware listing drops scope/type/status filters,
so the ``/endpoint up`` picker offered embedding and staging models.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@contextmanager
def _client(direct: bool):
    """Client with mocked http core and a fixed direct/gateway prefix."""
    from r4.client import Client

    core = MagicMock()
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: core)):
        client = Client()
        client._is_umrs_direct = direct
        yield client, core


_SERVER = {
    "server_id": "mcp-abc",
    "name": "demo",
    "url": "http://demo.local",
    "status": "healthy",
    "updated_at": "2026-10-17T00:00:00",
}


@contextmanager
def _probe(outcome):
    """Patch the reachability probe's urlopen: ``outcome`` is raised or returned."""

    def fake_urlopen(req, timeout):
        fake_urlopen.requests.append((req, timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return MagicMock()

    fake_urlopen.requests = []
    with patch("r4.mcp.urlopen", fake_urlopen):
        yield fake_urlopen.requests


class TestMCPServerHealth:
    def test_health_reads_the_served_server_route(self):
        with _client(direct=False) as (client, core), _probe(None):
            core.get.return_value = dict(_SERVER)
            result = client.mcp.check_server_health("mcp-abc")
        core.post.assert_not_called()
        assert core.get.call_args.args[0].endswith("/gw/mcp/servers/mcp-abc")
        assert result["checked_at"]
        expected = {
            "healthy": True,
            "status": "healthy",
            "registry_status": "healthy",
            "source": "probe",
            "server": "demo",
            "server_id": "mcp-abc",
            "url": "http://demo.local",
        }
        assert {key: result[key] for key in expected} == expected

    def test_health_probe_is_a_bare_head_to_the_server_url(self):
        with _client(direct=False) as (client, core), _probe(None) as requests:
            core.get.return_value = dict(_SERVER)
            client.mcp.check_server_health("mcp-abc")
        req, timeout = requests[0]
        assert (req.full_url, req.get_method(), timeout) == ("http://demo.local", "HEAD", 5)
        # The gateway credential never goes to the registered server's URL.
        assert not req.has_header("Authorization")

    def test_unreachable_server_with_healthy_registry_row_is_not_healthy(self):
        with _client(direct=False) as (client, core), _probe(URLError("refused")):
            core.get.return_value = dict(_SERVER)
            result = client.mcp.check_server_health("demo")
        assert result["healthy"] is False
        assert result["registry_status"] == "healthy"
        assert result["status"] == "unreachable (probe)"

    def test_server_error_answer_is_not_healthy(self):
        error = HTTPError("http://demo.local", 503, "Service Unavailable", {}, None)
        with _client(direct=False) as (client, core), _probe(error):
            core.get.return_value = dict(_SERVER)
            result = client.mcp.check_server_health("demo")
        assert result["healthy"] is False

    def test_client_error_answer_means_the_server_is_up(self):
        # MCP servers commonly answer HEAD with 401/405; the server is serving.
        error = HTTPError("http://demo.local", 405, "Method Not Allowed", {}, None)
        with _client(direct=False) as (client, core), _probe(error):
            core.get.return_value = dict(_SERVER)
            result = client.mcp.check_server_health("demo")
        assert result["healthy"] is True

    def test_row_without_url_is_not_healthy_and_not_probed(self):
        with _client(direct=False) as (client, core), _probe(None) as requests:
            core.get.return_value = {**_SERVER, "url": ""}
            result = client.mcp.check_server_health("demo")
        assert requests == []
        assert result["healthy"] is False

    def test_direct_mode_uses_umrs_prefix(self):
        with _client(direct=True) as (client, core):
            core.get.return_value = []
            client.mcp.list_servers()
            client.mcp.list_tools(refresh=True)
            core.get.return_value = dict(_SERVER)
            with _probe(None):
                client.mcp.check_server_health("mcp-abc")
        urls = [c.args[0] for c in core.get.call_args_list]
        assert urls and all("/umrs/mcp/" in u for u in urls), urls
        assert not any("/gw/" in u for u in urls), urls


def _model_row(name: str, model_type: str, status: str, scope: str = "system") -> dict:
    return {
        "model_id": name,
        "name": name,
        "scope": scope,
        "model_type": model_type,
        "status": status,
        "storage_path": f"s3://models/{name}",
    }


def _model_client(rows: list[dict]):
    from r4.config import Config
    from r4.models import ModelClient

    client = ModelClient(config=Config(gateway_url="http://gw.test"))
    client._is_umrs_direct = False
    client._http_core = MagicMock()
    client._http_core.get.return_value = {"models": rows}
    client._transport_router = SimpleNamespace(execute=lambda _op, http_fn, zmq_fn: http_fn())
    return client


class TestRegistryModelFilters:
    def test_type_filter_uses_the_route_query_name(self):
        from r4.models import ModelStatus, ModelType

        client = _model_client([])
        client.list_models(status=ModelStatus.AVAILABLE, model_type=ModelType.LLM)
        params = client._http_core.get.call_args.kwargs["params"]
        assert params["model_type"] == "llm"
        assert "type" not in params
        assert params["status"] == "available"

    def test_rows_outside_the_requested_type_and_status_are_dropped(self):
        from r4.models import ModelStatus, ModelType

        client = _model_client(
            [
                _model_row("chat", "llm", "available"),
                _model_row("embed", "embedding", "available"),
                _model_row("pending", "llm", "staging"),
            ]
        )
        models = client.list_models(status=ModelStatus.AVAILABLE, model_type=ModelType.LLM)
        assert [m.name for m in models] == ["chat"]

    def test_unfiltered_listing_keeps_every_row(self):
        client = _model_client(
            [_model_row("chat", "llm", "available"), _model_row("embed", "embedding", "staging")]
        )
        assert [m.name for m in client.list_models()] == ["chat", "embed"]

    def test_rows_outside_the_requested_scope_are_dropped(self):
        from r4.models import ModelScope

        client = _model_client(
            [
                _model_row("shared", "llm", "available"),
                _model_row("mine", "llm", "available", scope="user"),
                _model_row("team", "llm", "available", scope="project"),
            ]
        )
        models = client.list_models(scope=ModelScope.SYSTEM)
        assert client._http_core.get.call_args.kwargs["params"]["scope"] == "system"
        assert [m.name for m in models] == ["shared"]
