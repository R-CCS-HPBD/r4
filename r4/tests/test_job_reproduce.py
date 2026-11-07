"""r4 executor-backed replay surface (G-P7-2): client + CLI drift print."""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _client(direct: bool = False, reply: dict | None = None):
    from r4.client import Client

    mock_core = MagicMock()
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
        client = Client()
        client._is_umrs_direct = direct
        mock_core.request = MagicMock(return_value=reply or {})
        yield client, mock_core


class TestJobReproduceClient:
    def test_posts_reproduce_route(self):
        with _client() as (client, mock_core):
            client.jobs.reproduce("job-9")
        args, _ = mock_core.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/gw/jobs/job-9/reproduce")

    def test_direct_mode_route(self):
        with _client(direct=True) as (client, mock_core):
            client.jobs.reproduce("job-9")
        args, _ = mock_core.request.call_args
        assert "/umrs/jobs/job-9/reproduce" in args[1]


class _StubClient:
    def __init__(self, result: dict):
        self.jobs = Namespace(reproduce=lambda job_id: result)


def _run_cli(result: dict) -> str:
    from r4._cli.job import _cmd_job_reproduce

    printed: list[str] = []
    with patch("builtins.print", lambda *a, **_k: printed.append(" ".join(str(x) for x in a))):
        _cmd_job_reproduce(Namespace(job_id="job-1", as_json=False), _StubClient(result))
    return "\n".join(printed)


class TestJobReproduceCLI:
    def test_drift_undeclared_printed(self):
        out = _run_cli(
            {
                "job_id": "job-1",
                "success": True,
                "drift": {"pinned": False, "note": "undeclared"},
            }
        )
        assert "no recorded code version" in out

    def test_drift_none_printed(self):
        out = _run_cli(
            {
                "job_id": "job-1",
                "success": True,
                "drift": {
                    "pinned": True,
                    "same_code": True,
                    "current_code_sha": "abcdef0123456789",
                },
            }
        )
        assert "drift: none" in out

    def test_drift_changed_printed(self):
        out = _run_cli(
            {
                "job_id": "job-1",
                "success": True,
                "drift": {
                    "pinned": True,
                    "same_code": False,
                    "recorded_code_sha": "aaaa00000000",
                    "current_code_sha": "bbbb11111111",
                },
            }
        )
        assert "code changed aaaa00000000 → bbbb11111111" in out
