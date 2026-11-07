"""WP-11 client+CLI surface for attempt credentials (round 2)."""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _client(reply: dict | None = None):
    from r4.client import Client

    mock_core = MagicMock()
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
        client = Client()
        client._is_umrs_direct = False
        mock_core.request = MagicMock(return_value=reply or {})
        yield client, mock_core


class TestAttemptCredentialsClient:
    def test_mint_route(self):
        with _client() as (client, mock_core):
            client.jobs.attempt_credentials_mint("job-9", "attempt-1")
        args, _ = mock_core.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/gw/jobs/job-9/attempts/attempt-1/credentials")

    def test_sts_route(self):
        with _client() as (client, mock_core):
            client.jobs.attempt_credentials_sts("job-9", "attempt-1")
        args, _ = mock_core.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/gw/jobs/job-9/attempts/attempt-1/credentials/sts")

    def test_presign_route_sends_body(self):
        with _client() as (client, mock_core):
            client.jobs.attempt_presign(
                "job-9",
                "attempt-1",
                "jobs/job-9/attempt_1/out.zip",
                method="put",
                ttl_sec=120,
            )
        args, kwargs = mock_core.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/gw/jobs/job-9/attempts/attempt-1/presign")
        assert kwargs["json_body"]["key"] == "jobs/job-9/attempt_1/out.zip"
        assert kwargs["json_body"]["method"] == "put"
        assert kwargs["json_body"]["ttl_sec"] == 120


class _StubJobs:
    def __init__(self, result: dict):
        self._result = result

    def attempt_credentials_mint(self, job_id, attempt_id):
        self.last = ("mint", job_id, attempt_id)
        return self._result

    def attempt_credentials_sts(self, job_id, attempt_id):
        self.last = ("sts", job_id, attempt_id)
        return self._result

    def attempt_presign(self, job_id, attempt_id, key, method="get", ttl_sec=None):
        self.last = ("presign", job_id, attempt_id, key, method, ttl_sec)
        return self._result


def _run_cli(stub_jobs: _StubJobs, argv):
    from r4._cli import job as job_cli

    handler = job_cli._job_command_handlers()[argv.job_command]
    stub = MagicMock()
    stub.jobs = stub_jobs
    return handler(argv, stub)


class TestAttemptCredentialsCLI:
    def _mint_namespace(self, sts: bool = False):
        return Namespace(
            job_command="attempt-credentials",
            job_id="job-9",
            attempt_id="attempt-1",
            sts=sts,
            as_json=False,
        )

    def test_mint_prints_scope_and_no_token(self, capsys):
        result = {
            "status": "minted",
            "credentials": {
                "job_id": "job-9",
                "attempt_id": "attempt-1",
                "scope_prefixes": ["jobs/job-9/attempt_1/"],
                "ttl_sec": 900,
                "expires_at": "2026-09-11T01:00:00+00:00",
            },
        }
        stub = _StubJobs(result)
        _run_cli(stub, self._mint_namespace())
        assert stub.last[0] == "mint"
        out = capsys.readouterr().out
        assert "jobs/job-9/attempt_1/" in out
        assert "session_token" not in out

    def test_sts_prints_token_once_and_warns(self, capsys):
        result = {
            "status": "federated",
            "scope_prefixes": ["jobs/job-9/attempt_1/"],
            "credentials": {
                "access_key": "AK",
                "secret_key": "SK",
                "session_token": "T",
                "expiration": "2026-09-11T02:00:00+00:00",
            },
        }
        stub = _StubJobs(result)
        _run_cli(stub, self._mint_namespace(sts=True))
        assert stub.last[0] == "sts"
        out = capsys.readouterr().out
        assert "session_token" in out
        assert "never stored" in out.lower() or "note it down" in out.lower()

    def test_presign_passes_method_and_ttl(self, capsys):
        result = {"url": "https://signed", "method": "put", "expires_in_sec": 120}
        stub = _StubJobs(result)
        argv = Namespace(
            job_command="attempt-presign",
            job_id="job-9",
            attempt_id="attempt-1",
            key="jobs/job-9/attempt_1/out.zip",
            method="put",
            ttl_sec=120,
            as_json=False,
        )
        _run_cli(stub, argv)
        key = "jobs/job-9/attempt_1/out.zip"
        assert stub.last == ("presign", "job-9", "attempt-1", key, "put", 120)
        out = capsys.readouterr().out
        assert "https://signed" in out
