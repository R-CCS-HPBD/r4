"""``r4 hpc jobs|cancel|retry`` across sites (multi-HPC plan S3, 2026-10-24).

The four umrs-fronts share one ``jobs`` table, so every gateway lists every
job. The listing keeps each job once (by ``job_id``), labels it from the
row's ``venue`` (the server's site name, mapped to the user's source name),
and cancel/retry go through the gateway of the site that runs the job.
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4 import hpc_jobs, sites
from r4.config import Config
from r4.errors import APIError, R4Error
from r4.jobs import Job

URLS = {
    "rccs": "https://rccs.example",
    "bdrt": "https://bdrt.example",
    "ai4s": "https://ai4s.example",
    "octopus": "https://octopus.example",
}
SITE_NAMES = {
    "rccs": "rccs-front",
    "bdrt": "bdrt-front",
    "ai4s": "ai4s",
    "octopus": "octopus-front",
}


def _job(job_id: str, venue: Any) -> Job:
    data = {"job_id": job_id, "job_type": "workflow_run", "status": "running"}
    if venue is not None:
        data["venue"] = venue
    return Job.from_dict(data)


SHARED_ROWS = [
    _job("1", "bdrt-front"),
    _job("2", "rccs-front"),
    _job("3", ""),
    _job("4", None),
    _job("5", "elsewhere-front"),
]
_BY_ID = {job.job_id: job for job in SHARED_ROWS}


def _get(job_id: str) -> Job:
    """The shared table: every gateway answers for every job."""
    if job_id not in _BY_ID:
        raise APIError(f"Job not found: {job_id}", status_code=404)
    return _BY_ID[job_id]


@pytest.fixture
def four_sites(monkeypatch):
    from r4.source import Source, get_source_store

    store = get_source_store()
    clients: dict[str, MagicMock] = {}
    for name, url in URLS.items():
        store.add(Source(name=name, kind="platform", url=url))
        client = MagicMock(name=name)
        client.readiness.return_value = {"ready": True}
        client.site_me.return_value = {"site": SITE_NAMES[name], "access": {"mode": "undeclared"}}
        client.jobs.list.return_value = list(SHARED_ROWS)
        client.jobs.get.side_effect = _get
        client.jobs.cancel.side_effect = lambda job_id, n=name: _job(job_id, n)
        client.jobs.retry.side_effect = lambda job_id, n=name: _job(job_id, n)
        clients[name] = client
    monkeypatch.setattr(sites, "_client_for", lambda source, base: clients[source.name])
    return SimpleNamespace(clients=clients, base=Config(gateway_url=URLS["rccs"], api_key="k"))


def test_four_sources_same_rows_each_job_once_labelled_by_venue(four_sites):
    jobs, errors = hpc_jobs.list_across_sites(four_sites.base)
    assert errors == {}
    assert [j.job.job_id for j in jobs] == ["1", "2", "3", "4", "5"]
    labels = {j.job.job_id: j.site for j in jobs}
    assert labels == {"1": "bdrt", "2": "rccs", "3": "shared", "4": "?", "5": "elsewhere-front"}
    assert all(len(j.seen_via) == 4 for j in jobs)


def test_rows_whose_site_cannot_be_named_print_the_bare_id(four_sites):
    """Review m1: ``3@shared``/``4@?`` were printed and then refused by
    cancel/retry; the bare id is the form they take back."""
    jobs, _errors = hpc_jobs.list_across_sites(four_sites.base)
    handles = [j.to_dict()["handle"] for j in jobs]
    assert handles == ["1@bdrt", "2@rccs", "3", "4", "5"]


def test_an_unreachable_site_is_reported_not_fatal(four_sites):
    four_sites.clients["octopus"].jobs.list.side_effect = RuntimeError("connection refused")
    jobs, errors = hpc_jobs.list_across_sites(four_sites.base)
    assert errors == {"octopus": "connection refused"}
    assert len(jobs) == 5


def test_cancel_goes_to_the_gateway_of_the_rows_venue(four_sites):
    site, job = hpc_jobs.act_on_job("cancel", "1", four_sites.base)
    assert site == "bdrt"
    four_sites.clients["bdrt"].jobs.cancel.assert_called_once_with("1")
    four_sites.clients["rccs"].jobs.cancel.assert_not_called()


def test_retry_with_an_explicit_site(four_sites):
    site, _job_row = hpc_jobs.act_on_job("retry", "2@ai4s", four_sites.base)
    assert site == "ai4s"
    four_sites.clients["ai4s"].jobs.retry.assert_called_once_with("2")


def test_server_site_name_in_a_handle_resolves(four_sites):
    site, _row = hpc_jobs.act_on_job("cancel", "2@octopus-front", four_sites.base)
    assert site == "octopus"


def test_shared_row_goes_home(four_sites):
    site, _row = hpc_jobs.act_on_job("cancel", "3", four_sites.base)
    assert site == "rccs"


@pytest.mark.parametrize("job_id", ["4", "5"])
def test_untellable_site_is_refused_with_the_handle_form(four_sites, job_id):
    with pytest.raises(hpc_jobs.HpcSiteError, match=f"name it as {job_id}@<site>"):
        hpc_jobs.act_on_job("cancel", job_id, four_sites.base)
    for client in four_sites.clients.values():
        client.jobs.cancel.assert_not_called()


def test_unknown_site_fails_closed(four_sites):
    with pytest.raises(hpc_jobs.HpcSiteError, match="unknown_site"):
        hpc_jobs.act_on_job("cancel", "1@bdtr", four_sites.base)


def test_missing_job_is_an_error(four_sites):
    with pytest.raises(R4Error, match="not found"):
        hpc_jobs.act_on_job("cancel", "999", four_sites.base)


def test_a_bare_id_is_looked_up_once_at_home_not_by_listing(four_sites):
    """Review P8: the lookup listed 500 jobs per site and missed older ones;
    the shared table answers ``jobs.get`` at home for any job."""
    site, _row = hpc_jobs.act_on_job("cancel", "1", four_sites.base)
    assert site == "bdrt"
    four_sites.clients["rccs"].jobs.get.assert_called_once_with("1")
    for client in four_sites.clients.values():
        client.jobs.list.assert_not_called()


@pytest.mark.parametrize(("handle", "site"), [("3@shared", "rccs")])
def test_a_printed_shared_handle_is_taken_back(four_sites, handle, site):
    assert hpc_jobs.act_on_job("cancel", handle, four_sites.base)[0] == site


def test_a_question_mark_site_asks_for_the_site_by_name(four_sites):
    with pytest.raises(hpc_jobs.HpcSiteError, match="name it as"):
        hpc_jobs.act_on_job("cancel", "4@?", four_sites.base)


def test_two_sources_claiming_one_site_name_map_to_neither(four_sites):
    """Review S3: a registered gateway reporting another's site name took its
    work (last row won the venue map)."""
    four_sites.clients["ai4s"].site_me.return_value = {"site": "bdrt-front"}
    with pytest.raises(hpc_jobs.HpcSiteError, match="cannot tell"):
        hpc_jobs.act_on_job("cancel", "1", four_sites.base)
    for client in four_sites.clients.values():
        client.jobs.cancel.assert_not_called()


def test_cli_jobs_exits_one_when_every_site_failed(four_sites, monkeypatch, capsys):
    from r4._cli.hpc import HpcCommand

    monkeypatch.setattr(sites, "_base_config", lambda base=None: four_sites.base)
    for client in four_sites.clients.values():
        client.jobs.list.side_effect = RuntimeError("Authentication failed")
    args = Namespace(hpc_command="jobs", as_json=False, status=None, limit=50)
    assert HpcCommand().handle(args) == 1
    err = capsys.readouterr().err
    assert "rccs: error: Authentication failed" in err and "unreachable" not in err


def test_job_parses_the_venue():
    assert Job.from_dict({"job_id": "1", "venue": "bdrt-front"}).venue == "bdrt-front"
    assert Job.from_dict({"job_id": "1"}).venue is None


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_jobs_json(four_sites, monkeypatch, capsys):
    from r4._cli.hpc import HpcCommand

    monkeypatch.setattr(sites, "_base_config", lambda base=None: four_sites.base)
    args = Namespace(hpc_command="jobs", as_json=True, status=None, limit=50)
    assert HpcCommand().handle(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert [j["handle"] for j in out["jobs"]][:2] == ["1@bdrt", "2@rccs"]


def test_cli_cancel_refusal_exits_one(four_sites, monkeypatch, capsys):
    from r4._cli.hpc import HpcCommand

    monkeypatch.setattr(sites, "_base_config", lambda base=None: four_sites.base)
    assert HpcCommand().handle(Namespace(hpc_command="cancel", job="4")) == 1
    assert "name it as 4@<site>" in capsys.readouterr().err


def test_cli_sites_text(four_sites, monkeypatch, capsys):
    from r4._cli.hpc import HpcCommand

    monkeypatch.setattr(sites, "_base_config", lambda base=None: four_sites.base)
    args = Namespace(hpc_command="sites", as_json=False, refresh=False)
    assert HpcCommand().handle(args) == 0
    out = capsys.readouterr().out
    assert "- rccs (home): healthy, server=rccs-front, mode=undeclared" in out
    assert "- bdrt: healthy" in out and "NOT SELECTABLE (access mode undeclared)" in out


def test_cli_is_registered():
    from r4 import cli

    assert "hpc" in cli._PRE_REGISTER_COMMANDS and "hpc" in cli._DISPATCH_MODULES
