"""Mode-aware ops and hpc routes (cleanup C-2(v)/(vi), 2026-10-19).

Hard-coded ``/umrs/*`` paths 404 behind the gateway (the edge's only
``/umrs/`` location is ``/umrs/zmq/``). The ops reads and the TUI's hpc
durable helpers ride ``_api_prefix`` now — ``/gw`` behind the edge,
``/umrs`` in direct mode — and an unreachable durable submit is an honest
"nothing was submitted" error, never a fake local-sbatch fallback.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


_REFUSAL_DETAIL = (
    "Site cluster-a runs user work under one login, a personal account of the "
    "operator; only the listed projects may use it until the operator consents, "
    "and project proj-a is not listed. (hpcaccess.project_not_allowed)"
)


def _refused_post(*_args, **_kwargs):
    """The real r4 mapping of UMRS's typed 403 (ADR-112 hpcaccess.*)."""
    from r4._http import raise_for_status

    response = SimpleNamespace(
        status_code=403,
        text=json.dumps({"detail": _REFUSAL_DETAIL}),
        headers={"x-platform-error-code": "hpcaccess.project_not_allowed"},
    )
    raise_for_status(response)


@pytest.mark.parametrize("armed", [False, True])
def test_a_site_refusal_reaches_the_agent_whole_with_its_code(monkeypatch, armed):
    """Plan §3 A.3.2 (review 2026-11-08): ``hpc_submit`` passes the typed
    ``hpcaccess.*`` code through. Before, the 403 was cut to 120 characters
    (the code, at the end, was lost), dressed as "gateway unreachable" with
    retry advice, and an armed session dropped it entirely."""
    from r4.companion import tools as tools_mod

    client = SimpleNamespace(_api_prefix="/gw", post=_refused_post)
    monkeypatch.setattr("r4.client.Client", lambda: client)
    monkeypatch.setattr(tools_mod, "_WORKFLOW_ARMED", armed)

    out = tools_mod._hpc_submit_via_umrs("/tmp/job.sh")

    assert out is not None
    assert "hpcaccess.project_not_allowed" in out
    assert "proj-a is not listed" in out
    assert "gateway is reachable" not in out and "legacy sbatch" not in out
