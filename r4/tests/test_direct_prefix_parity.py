"""Direct-UMRS prefix parity for workflow/job clients (WP-08, WP-07 residual).

Finding: WorkflowClient/JobClient hardcoded ``/gw/…`` so every verb 404'd
against the sidecar in direct mode. The client's ``_api_prefix`` probe now
drives both families.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _client(direct: bool):
    """Client with mocked http; calls run inside the patch scope."""
    from r4.client import Client

    mock_core = MagicMock()
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
        client = Client()
        client._is_umrs_direct = direct
        mock_core.request = MagicMock(return_value={})
        mock_core.get = MagicMock(return_value={})
        mock_core.delete = MagicMock(return_value={})
        mock_core.post = MagicMock(return_value={})
        yield client, mock_core


class TestWorkflowClientPrefix:
    def test_direct_mode_uses_umrs_prefix(self):
        with _client(direct=True) as (client, mock_core):
            client.workflows.describe("demo")
        args, _ = mock_core.get.call_args
        assert "/umrs/workflows/demo" in args[0]

    def test_gateway_mode_uses_gw_prefix(self):
        with _client(direct=False) as (client, mock_core):
            client.workflows.describe("demo")
        args, _ = mock_core.get.call_args
        assert "/gw/workflows/demo" in args[0]

    def test_execution_url_direct(self):
        with _client(direct=True) as (client, _):
            url = client.workflows._execution_url("exec-1")
        assert url.endswith("/umrs/workflows/executions/exec-1")

    def test_list_workflows_direct(self):
        with _client(direct=True) as (client, mock_core):
            client.workflows._list_workflows_via_http(None)
        args, _ = mock_core.get.call_args
        assert args[0].endswith("/umrs/workflows")

    def test_list_all_users_rides_the_http_params(self):
        with _client(direct=True) as (client, mock_core):
            client.workflows._list_workflows_via_http(None, all_users=True)
            _, kwargs = mock_core.get.call_args
            assert kwargs["params"]["all_users"] == "true"
            client.workflows._list_workflows_via_http(None)
            _, kwargs = mock_core.get.call_args
            assert "all_users" not in kwargs["params"]

    def test_list_all_users_rides_the_zmq_filters(self):
        with _client(direct=True) as (client, _):
            seen = {}
            client.workflows._list_workflows_via_zmq = lambda filters: (
                seen.setdefault("f", filters) or []
            )
            router = MagicMock()
            router.execute.side_effect = lambda _op, http_fn, zmq_fn: zmq_fn()
            with patch.object(type(client.workflows), "_router", router):
                client.workflows.list_workflows(all_users=True)
                assert seen["f"] == {"all_users": True}
                seen.clear()
                client.workflows.list_workflows()
                assert seen["f"] == {}


class TestJobClientPrefix:
    def test_direct_mode_job_list(self):
        with _client(direct=True) as (client, mock_core):
            client.jobs.list(project_id="p")
        args, _ = mock_core.request.call_args
        assert "/umrs/jobs" in args[1]

    def test_gateway_mode_job_list(self):
        with _client(direct=False) as (client, mock_core):
            client.jobs.list(project_id="p")
        args, _ = mock_core.request.call_args
        assert "/gw/jobs" in args[1]

    def test_route_translation_leaves_other_paths(self):
        with _client(direct=True) as (client, _):
            assert client.jobs._route("/other/x") == "/other/x"
            assert client.jobs._route("/gw/jobs") == "/umrs/jobs"
