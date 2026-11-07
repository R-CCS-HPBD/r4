"""Mode-aware ops and hpc routes (cleanup C-2(v)/(vi), 2026-10-19).

Hard-coded ``/umrs/*`` paths 404 behind the gateway (the edge's only
``/umrs/`` location is ``/umrs/zmq/``). The ops reads and the TUI's hpc
durable helpers ride ``_api_prefix`` now — ``/gw`` behind the edge,
``/umrs`` in direct mode — and an unreachable durable submit is an honest
"nothing was submitted" error, never a fake local-sbatch fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.config import Config


def _client(direct: bool):
    from r4.client import Client

    client = Client(
        config=Config(
            gateway_url="http://gw:8080",
            api_key="gateway-key",
            project_id="proj",
            umrs_username="alice",
        )
    )
    client._is_umrs_direct = direct  # skip the probe
    client.http.get = MagicMock(return_value={})
    return client


def test_governance_reads_are_mode_aware():
    for direct, prefix in ((False, "/gw"), (True, "/umrs")):
        client = _client(direct)
        client.governance_ownership()
        client.governance_quotas()
        client.audit_trace(limit=5)
        urls = [c.args[0] for c in client.http.get.call_args_list]
        assert urls == [
            f"http://gw:8080{prefix}/ops/governance/ownership",
            f"http://gw:8080{prefix}/ops/governance/quotas",
            f"http://gw:8080{prefix}/ops/audit/trace",
        ]


def test_hpc_submit_posts_jobs_on_the_mode_prefix(monkeypatch):
    from r4.companion import tools as tools_mod

    client = SimpleNamespace(
        _api_prefix="/gw",
        post=MagicMock(return_value={"job_id": "42"}),
    )
    monkeypatch.setattr("r4.client.Client", lambda: client)

    out = tools_mod._hpc_submit_via_umrs("/tmp/job.sh")

    client.post.assert_called_once()
    args, kwargs = client.post.call_args
    assert args[0] == "/gw/jobs"
    assert "Submitted via UMRS durable job 42" in out


def test_hpc_durable_status_gets_jobs_on_the_mode_prefix(monkeypatch):
    from r4.companion import tools as tools_mod

    client = SimpleNamespace(
        _api_prefix="/gw",
        get=MagicMock(return_value={"status": "RUNNING", "spec": {"script_path": "/tmp/job.sh"}}),
    )
    monkeypatch.setattr("r4.client.Client", lambda: client)

    out = tools_mod._hpc_durable_status("42")

    assert client.get.call_args.args[0] == "/gw/jobs/42"
    assert "RUNNING" in out


def test_durable_submit_failure_is_honest_and_submits_nothing(monkeypatch):
    """C-2(vi): the old note claimed 'falling back to local sbatch' while
    the caller returned it as-is — nothing ran anywhere."""
    from r4.companion import tools as tools_mod

    def boom():
        raise RuntimeError("gateway 404")

    monkeypatch.setattr("r4.client.Client", boom)
    monkeypatch.setattr(tools_mod, "_WORKFLOW_ARMED", False)
    local = MagicMock()
    monkeypatch.setattr(tools_mod.subprocess, "run", local)

    out = tools_mod._hpc_submit_via_umrs("/tmp/job.sh")

    assert out.startswith("Error: durable UMRS submit failed")
    assert "nothing was submitted" in out
    local.assert_not_called()
