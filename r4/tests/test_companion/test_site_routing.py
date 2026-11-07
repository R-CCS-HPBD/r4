"""Site-addressed agent tools (multi-HPC plan S3, 2026-10-24).

One session drives several HPC sites: ``site=`` (or an ``id@site`` handle)
sends a tool call through that site's own gateway client. Routing fails
closed: an unknown site is an error naming the valid sites, never a silent
run at home (X-a); a non-home site that has not declared its HPC access mode
is refused (``site_access_undeclared``). Every result names its site.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from r4_contracts.workflows import WorkflowResult, WorkflowStatus

from r4 import sites
from r4.companion import site_routing
from r4.config import Config

RCCS_URL = "https://rccs.example"
BDRT_URL = "https://bdrt.example"
OCT_URL = "https://octopus.example"

_ME = {
    "bdrt": {
        "site": "bdrt-front",
        "access": {"mode": "user_account", "declared": True},
        "account": {"status": "present"},
    },
    "octopus": {
        "site": "octopus-front",
        "access": {"mode": "undeclared", "declared": False},
        "account": {"status": "present"},
    },
    "rccs": {
        "site": "rccs-front",
        "access": {"mode": "undeclared", "declared": False},
        "account": {"status": "present"},
    },
}


def _site_client(name: str) -> MagicMock:
    client = MagicMock(name=f"client-{name}")
    client.config = Config(gateway_url={"rccs": RCCS_URL, "bdrt": BDRT_URL}.get(name, OCT_URL))
    client.readiness.return_value = {"ready": True}
    client.site_me.return_value = _ME[name]
    job = MagicMock(job_id="77", job_type="workflow_run", status="running", venue=f"{name}-front")
    job.attempts = []
    client.jobs.get.return_value = job
    client.jobs.cancel.return_value = job
    client.jobs.list.return_value = [job]
    client.get_endpoint_status.return_value = {"instance_id": "ep_3c", "status": "ready"}
    client.stop_endpoint.return_value = {"stopped": True}
    client.health.return_value = True
    client.workflows.status.return_value = WorkflowStatus(
        execution_id="exec_7f", workflow_name="example-workflow", status="running", progress=0.5
    )
    client.workflows.get_execution_detail.return_value = {}
    client.workflows.trigger.return_value = WorkflowResult(
        execution_id="exec_9a", workflow_name="example-workflow", status="submitted"
    )
    client.provision_endpoint.return_value = {"instance_id": "ep_1"}
    return client


@pytest.fixture
def world(monkeypatch):
    """Home = rccs; bdrt declared; octopus undeclared; 'openai' public."""
    from r4.source import Source, get_source_store

    store = get_source_store()
    store.add(Source(name="rccs", kind="platform", url=RCCS_URL))
    store.add(Source(name="bdrt", kind="platform", url=BDRT_URL))
    store.add(Source(name="octopus", kind="platform", url=OCT_URL))
    store.add(Source(name="openai", kind="public", url="https://api.example/v1"))
    clients = {name: _site_client(name) for name in ("rccs", "bdrt", "octopus")}
    monkeypatch.setattr(sites, "_client_for", lambda source, base: clients[source.name])
    monkeypatch.setattr(site_routing, "_routed_client", lambda source, client: clients[source.name])
    return clients


def _tools(client: Any) -> dict[str, Any]:
    from r4.companion.tools import _client_tools

    return {t["name"]: t["execute"] for t in _client_tools(client)}


def _untouched(client: MagicMock) -> None:
    for call in client.method_calls:
        assert call[0] in ("readiness", "site_me", "site_capabilities"), call


# ── fail closed (X-a) ─────────────────────────────────────────────────────────


def test_typo_source_is_an_error_naming_valid_sites_not_a_home_run(world):
    home = world["rccs"]
    out = _tools(home)["endpoint_provision"](model="m", source="bdtr")
    assert out.startswith("Error provisioning endpoint: unknown_site")
    assert "rccs" in out and "bdrt" in out and "octopus" in out
    home.provision_endpoint.assert_not_called()


def test_resolve_source_client_fails_closed_on_public_and_unknown(world):
    resolve = site_routing.resolve_source_client
    with pytest.raises(site_routing.SiteRefused, match="unknown_site"):
        resolve(world["rccs"], "ghost")
    with pytest.raises(site_routing.SiteRefused, match="public model source"):
        resolve(world["rccs"], "openai")


def test_typo_site_on_job_status_errors(world):
    out = _tools(world["rccs"])["job_status"](job_id="77", site="bdrtt")
    assert "unknown_site" in out
    world["rccs"].jobs.get.assert_not_called()


# ── the undeclared-mode guard ────────────────────────────────────────────────


def test_undeclared_non_home_site_is_refused(world):
    out = _tools(world["rccs"])["job_cancel"](job_id="77", site="octopus")
    assert "site_access_undeclared" in out and "octopus" in out
    world["octopus"].jobs.cancel.assert_not_called()
    world["rccs"].jobs.cancel.assert_not_called()


def test_undeclared_home_site_is_not_refused(world):
    out = _tools(world["rccs"])["job_cancel"](job_id="77")
    assert out == "Job 77@rccs cancelled (status=running)"


def test_undeclared_site_refused_for_workflow_run(world):
    from r4.companion.workflow_tools import build_workflow_tools

    run = {t["name"]: t["execute"] for t in build_workflow_tools(world["rccs"])}["workflow_run"]
    body = json.loads(run(workflow_name="example-workflow", site="octopus"))
    assert body["code"] == "site_access_undeclared"
    world["octopus"].workflows.trigger.assert_not_called()


def test_absent_account_is_refused(world):
    world["bdrt"].site_me.return_value = {
        "access": {"mode": "user_account"},
        "account": {"status": "absent"},
    }
    out = _tools(world["rccs"])["job_cancel"](job_id="77", site="bdrt")
    assert "no_site_account" in out
    world["bdrt"].jobs.cancel.assert_not_called()


# ── routing reaches the named site, results are tagged ───────────────────────


def test_workflow_status_handle_reaches_the_other_site(world):
    from r4.companion.workflow_tools import build_workflow_tools

    tools = {t["name"]: t["execute"] for t in build_workflow_tools(world["rccs"])}
    body = json.loads(tools["workflow_status"](execution_id="exec_7f@bdrt"))
    world["bdrt"].workflows.status.assert_called_once_with("exec_7f")
    world["rccs"].workflows.status.assert_not_called()
    assert body["site"] == "bdrt" and body["handle"] == "exec_7f@bdrt"


def test_workflow_run_on_a_named_site_returns_a_handle(world):
    from r4.companion.workflow_tools import build_workflow_tools

    tools = {t["name"]: t["execute"] for t in build_workflow_tools(world["rccs"])}
    body = json.loads(tools["workflow_run"](workflow_name="example-workflow", site="bdrt"))
    assert body["handle"] == "exec_9a@bdrt"
    _untouched(world["rccs"])


def test_server_site_name_resolves_to_its_source(world):
    out = json.loads(_tools(world["rccs"])["job_status"](job_id="77", site="bdrt-front"))
    assert out["site"] == "bdrt" and out["handle"] == "77@bdrt"
    world["bdrt"].jobs.get.assert_called_once_with("77")


def test_site_and_handle_must_agree(world):
    out = _tools(world["rccs"])["job_status"](job_id="77@bdrt", site="octopus")
    assert "site_conflict" in out


def test_endpoint_status_and_stop_take_a_site(world):
    tools = _tools(world["rccs"])
    status = json.loads(tools["endpoint_status"](instance_id="ep_3c@bdrt"))
    assert status["site"] == "bdrt" and status["handle"] == "ep_3c@bdrt"
    stopped = json.loads(tools["endpoint_stop"](instance_id="ep_3c", source="bdrt"))
    assert stopped["site"] == "bdrt"
    world["bdrt"].stop_endpoint.assert_called_once_with("ep_3c", force=False)
    world["rccs"].stop_endpoint.assert_not_called()


def test_home_results_are_labelled_with_the_home_site(world):
    tools = _tools(world["rccs"])
    assert tools["job_list"]().startswith("- 77@rccs  type=workflow_run")
    assert json.loads(tools["platform_health"]())["site"] == "rccs"


def test_hpc_sites_tool_lists_every_site(world):
    from r4.companion.tools import _hpc_sites_json

    rows = json.loads(_hpc_sites_json(world["rccs"]))["sites"]
    by = {r["source"]: r for r in rows}
    assert by["rccs"]["home"] is True
    assert by["bdrt"]["selectable"] is True
    assert by["octopus"]["selectable"] is False


# ── 2026-10-24 review fixes ──────────────────────────────────────────────────


def _home_world(monkeypatch, *, register_bdrt: bool, bound: str | None = None) -> Any:
    """Home = the profile's gateway with NO registered source for it (plain
    ``r4 login``, env-only configs, hatch); optionally bdrt registered."""
    from r4.source import Source, get_source_store

    clients = {"home": _site_client("rccs"), "bdrt": _site_client("bdrt")}
    clients["home"].config = Config(gateway_url=RCCS_URL, bound_source=bound)
    if register_bdrt:
        get_source_store().add(Source(name="bdrt", kind="platform", url=BDRT_URL))
    monkeypatch.setattr(sites, "_client_for", lambda source, base: clients[source.name])
    monkeypatch.setattr(site_routing, "_routed_client", lambda source, client: clients[source.name])
    return clients


@pytest.mark.parametrize("register_bdrt", [False, True], ids=["one-site", "two-sites"])
@pytest.mark.parametrize("bound", [None, RCCS_URL], ids=["unbound", "bound-url"])
def test_printed_home_handles_resolve_home(monkeypatch, register_bdrt, bound):
    """M1: with no registered source for the home gateway every result was
    labelled ``@home`` and then refused (``unknown_site: no HPC site named
    'home'``), and so was the server's own site name."""
    clients = _home_world(monkeypatch, register_bdrt=register_bdrt, bound=bound)
    tools = _tools(clients["home"])
    line = tools["job_list"]()
    assert line.startswith("- 77@home  type=workflow_run"), line
    for args in ({"job_id": "77@home"}, {"job_id": "77", "site": "rccs-front"}):
        body = json.loads(tools["job_status"](**args))
        assert body["handle"] == "77@home", (args, body)
    assert tools["job_cancel"](job_id="77@home") == "Job 77@home cancelled (status=running)"
    clients["bdrt"].jobs.cancel.assert_not_called()


def test_an_at_sign_inside_a_file_key_is_not_a_site(world):
    """M2: ``photo@2x.png`` was split into a site ``2x.png`` and refused."""
    world["rccs"].data.download.return_value = MagicMock(
        filename="photo@2x.png", local_path="/tmp/x", size_bytes=1, status="ok"
    )
    tools = _tools(world["rccs"])
    for key in ("data/photo@2x.png", "users/alice@riken.jp/x.csv"):
        out = tools["file_download_platform"](file_id=key, local_path="/tmp/x")
        assert out.startswith("Downloaded from rccs"), out
        world["rccs"].data.download.assert_called_with(key, "/tmp/x", project_id=None)
    # site= given: the whole text is the key, at that site.
    world["bdrt"].data.download.return_value = world["rccs"].data.download.return_value
    out = tools["file_download_platform"](file_id="a@bdrt", local_path="/tmp/x", site="bdrt")
    assert out.startswith("Downloaded from bdrt")
    world["bdrt"].data.download.assert_called_with("a@bdrt", "/tmp/x", project_id=None)
    # A suffix that names a site still routes.
    tools["file_download_platform"](file_id="k1@bdrt", local_path="/tmp/x")
    world["bdrt"].data.download.assert_called_with("k1", "/tmp/x", project_id=None)


def test_job_list_labels_each_job_with_the_site_that_runs_it(world):
    """M3/P4: the shared jobs table lists bdrt's job at rccs too; it was
    labelled ``77@rccs`` and its cancel went to rccs."""
    bdrt_job = MagicMock(job_id="88", job_type="workflow_run", status="running", venue="bdrt-front")
    stray = MagicMock(job_id="99", job_type="cleanup", status="queued", venue="elsewhere")
    world["rccs"].jobs.list.return_value = [bdrt_job, stray]
    lines = _tools(world["rccs"])["job_list"]().splitlines()
    assert lines[0].startswith("- 88@bdrt  type=workflow_run")
    assert lines[1].startswith("- 99  type=cleanup") and "not one of your sites" in lines[1]


def test_bare_job_cancel_goes_to_the_gateway_of_the_jobs_venue(world):
    bdrt_job = MagicMock(job_id="88", job_type="workflow_run", status="running", venue="bdrt-front")
    world["rccs"].jobs.get.return_value = bdrt_job
    world["bdrt"].jobs.cancel.return_value = bdrt_job
    world["bdrt"].jobs.retry.return_value = bdrt_job
    tools = _tools(world["rccs"])
    assert tools["job_cancel"](job_id="88") == "Job 88@bdrt cancelled (status=running)"
    assert tools["job_retry"](job_id="88") == "Job 88@bdrt retried (status=running)"
    world["bdrt"].jobs.cancel.assert_called_once_with("88")
    world["rccs"].jobs.cancel.assert_not_called()
    world["rccs"].jobs.retry.assert_not_called()


def test_job_cancel_at_the_wrong_site_or_no_site_of_yours_is_refused(world):
    bdrt_job = MagicMock(job_id="88", job_type="workflow_run", status="running", venue="bdrt-front")
    world["rccs"].jobs.get.return_value = bdrt_job
    tools = _tools(world["rccs"])
    out = tools["job_cancel"](job_id="88@rccs")
    assert "site_conflict" in out and "88@bdrt" in out
    world["rccs"].jobs.get.return_value = MagicMock(job_id="99", venue="elsewhere")
    assert "unknown_site" in tools["job_cancel"](job_id="99")
    for client in world.values():
        client.jobs.cancel.assert_not_called()


def test_reads_reach_an_undeclared_site_but_work_does_not_start_there(world):
    """P3: the guard blocked reads too, so ``endpoint_list(source=...)``,
    which worked before the wave, failed for everyone."""
    world["octopus"].list_endpoints.return_value = [{"instance_id": "ep1", "model": "m"}]
    tools = _tools(world["rccs"])
    assert tools["endpoint_list"](source="octopus").startswith("- ep1@octopus  model=m")
    assert json.loads(tools["job_status"](job_id="77@octopus"))["handle"] == "77@octopus"
    assert json.loads(tools["platform_health"](site="octopus"))["site"] == "octopus"
    assert "site_access_undeclared" in tools["endpoint_provision"](model="m", site="octopus")
    assert "site_access_undeclared" in tools["endpoint_stop"](instance_id="ep1@octopus")
    assert "site_access_undeclared" in tools["file_upload_platform"](
        file_path="/tmp/f", site="octopus"
    )
    world["octopus"].provision_endpoint.assert_not_called()
    world["octopus"].stop_endpoint.assert_not_called()


def test_workflow_reads_reach_an_undeclared_site(world):
    from r4.companion.workflow_tools import build_workflow_tools

    tools = {t["name"]: t["execute"] for t in build_workflow_tools(world["rccs"])}
    assert json.loads(tools["workflow_status"](execution_id="exec_7f@octopus"))["site"] == "octopus"
    refused = json.loads(tools["workflow_cancel"](execution_id="exec_7f@octopus"))
    assert refused["code"] == "site_access_undeclared"
    world["octopus"].workflows.cancel.assert_not_called()


def test_an_unknown_account_is_not_eligible(world):
    """m3: ``unknown`` (the site could not read its user store) passed."""
    world["bdrt"].site_me.return_value = {
        "access": {"mode": "user_account"},
        "account": {"status": "unknown"},
    }
    assert "no_site_account" in _tools(world["rccs"])["job_cancel"](job_id="77", site="bdrt")
    world["bdrt"].jobs.cancel.assert_not_called()


def test_two_sources_reporting_one_site_name_is_a_conflict(world):
    """S3: the first source reporting ``bdrt-front`` won, so a registered
    gateway could take another's work by claiming its name."""
    world["octopus"].site_me.return_value = dict(_ME["bdrt"])
    out = _tools(world["rccs"])["job_status"](job_id="77", site="bdrt-front")
    assert "site_conflict" in out and "bdrt" in out and "octopus" in out
    world["octopus"].jobs.get.assert_not_called()
    # Source names still resolve (the ambiguous venue labels no site).
    body = json.loads(_tools(world["rccs"])["job_status"](job_id="77@bdrt"))
    world["bdrt"].jobs.get.assert_called_once_with("77")
    assert body["site"] is None and "handle" not in body


def test_single_site_sessions_get_no_site_params(monkeypatch):
    """P5: every user paid for the multi-site schemas (~900 tokens a turn)."""
    from r4.companion.tools import get_builtin_tools

    clients = _home_world(monkeypatch, register_bdrt=False)
    one = {t["name"]: t for t in get_builtin_tools(clients["home"])}
    assert "hpc_sites" in one  # the HPC skill names it; it shows mode and account
    for name in ("job_status", "workflow_run", "platform_health", "endpoint_list"):
        assert "site" not in one[name]["parameters"]["properties"], name
    assert "source" in one["endpoint_list"]["parameters"]["properties"]  # kept (pre-wave)


def test_multi_site_sessions_keep_the_site_params(world):
    from r4.companion.tools import get_builtin_tools

    tools = {t["name"]: t for t in get_builtin_tools(world["rccs"])}
    assert "hpc_sites" in tools
    assert "site" in tools["job_status"]["parameters"]["properties"]
    assert "site" in tools["workflow_run"]["parameters"]["properties"]


def test_hpc_sites_is_a_safe_tool():
    """P7: it asked for approval on every call in auto-safe mode."""
    from r4.companion.permissions import SAFE_TOOLS

    assert "hpc_sites" in SAFE_TOOLS
