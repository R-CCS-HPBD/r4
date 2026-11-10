"""``r4 hpc usage`` and ``r4 hpc grants``: read-only reports (plan §6 C5, §7 D4).

The person's own usage rows merged over every site, a project's usage with
its lifetime GPU-hour budget, the grants list with live usage, stale
reasons and a pending gate cancel, and ``--json``. Both commands are read
only: they run in the assistant's shell and are not fenced. r4 asks UMRS
only; it never runs a scheduler command.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4 import hpc_usage
from r4._cli import hpc as hpc_cli
from r4.errors import APIError
from r4.knowledge import COMPANION_SHELL_ENV

GRANT = "0123456789abcdef0123456789abcdef"


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "venue": "bdrt",
        "project_id": "proj-1",
        "run_id": "wf-0123456789ab",
        "step": "prep",
        "job_id": "",
        "attempt_id": "wf-0123456789ab/prep/1",
        "scheduler_job_id": "4242",
        "state": "COMPLETED",
        "gpu_hours": 1.5,
        "core_hours": 12.25,
        "grant_id": GRANT,
        "intent_id": "",
        "access_mode": "user_account",
        "source": "gate_usage",
    }
    row.update(over)
    return row


def _totals(jobs: int, gpu: float, core: float, rgpu: float = 0.0) -> dict[str, Any]:
    return {
        "jobs": jobs,
        "gpu_hours": gpu,
        "core_hours": core,
        "reserved_gpu_hours": rgpu,
        "reserved_core_hours": 0.0,
    }


class Sites:
    """``_site_client(name)`` for two sites with fixed answers."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.usage = {
            "bdrt": {"venue": "bdrt", "rows": [_row()], "totals": _totals(1, 1.5, 12.25)},
            "rccs": {
                "venue": "rccs",
                "rows": [_row(venue="rccs", run_id="", job_id="job-9", source="estimate")],
                "totals": _totals(1, 0.0, 0.0, rgpu=2.0),
            },
        }
        self.project = {
            "project_id": "proj-1",
            "budget": {
                "max_gpu_hours": 100.0,
                "used_gpu_hours": 40.0,
                "reserved_gpu_hours": 2.0,
                "remaining_gpu_hours": 58.0,
            },
            "totals": _totals(2, 40.0, 100.0, rgpu=2.0),
            "members": [{"person": "bob", "jobs": 1, "gpu_hours": 39.0, "core_hours": 90.0}],
            "rows": [_row()],
        }
        self.grants: dict[str, Any] = {"bdrt": {"grants": []}, "rccs": {"grants": []}}
        self.fail: dict[str, Exception] = {}

    def client(self, name: str) -> Any:
        def call(kind: str, answer: Any) -> Any:
            def fn(*args: Any, **kwargs: Any) -> Any:
                self.calls.append((name, kind, args, kwargs))
                if name in self.fail:
                    raise self.fail[name]
                return answer

            return fn

        return SimpleNamespace(
            hpc_usage=call("usage", self.usage.get(name)),
            project_hpc_usage=call("project", self.project),
            hpc_grants=call("grants", self.grants.get(name)),
        )


@pytest.fixture
def sites(monkeypatch):
    fake = Sites()
    monkeypatch.setattr(hpc_cli, "_site_client", fake.client)
    monkeypatch.setattr(hpc_cli, "_all_sites", lambda: ["bdrt", "rccs"])
    return fake


def run(argv: list[str]) -> int:
    from r4.cli import _build_parser

    return hpc_cli.HpcCommand().handle(_build_parser().parse_args(["hpc", *argv]))


# ── r4 hpc usage ─────────────────────────────────────────────────────────────


def test_usage_merges_my_rows_over_every_site(sites, capsys):
    assert run(["usage", "--since", "2026-11-01"]) == 0
    out = capsys.readouterr().out
    needed = [
        "wf-0123456789ab/prep",
        "job 4242",
        f"grant {GRANT}",
        "job-9",
        "(estimate)",
        "total: 2 jobs, GPU-h 1.50 used + 2.00 reserved, core-h 12.25 used",
    ]
    assert [item for item in needed if item not in out] == []
    assert [(c[0], c[1], c[3]) for c in sites.calls] == [
        ("bdrt", "usage", {"since": "2026-11-01"}),
        ("rccs", "usage", {"since": "2026-11-01"}),
    ]


def test_usage_for_one_site_asks_only_that_site(sites, capsys):
    assert run(["usage", "--site", "rccs"]) == 0
    assert [c[0] for c in sites.calls] == ["rccs"]
    assert "wf-0123456789ab/prep" not in capsys.readouterr().out


def test_usage_json_carries_every_site_and_the_errors(sites, capsys):
    sites.fail["rccs"] = APIError("down", status_code=503)
    assert run(["usage", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sites"]["bdrt"]["rows"][0]["scheduler_job_id"] == "4242"
    assert "rccs" in data["errors"] and "rccs" not in data["sites"]


def test_usage_exits_1_when_every_site_failed(sites, capsys):
    sites.fail.update(bdrt=APIError("down"), rccs=APIError("down"))
    assert run(["usage"]) == 1
    err = capsys.readouterr().err
    assert "bdrt: error" in err and "rccs: error" in err


@pytest.mark.parametrize(
    ("hours", "shown"),
    [(0.0, "0.00"), (0.004, "0.01"), (0.01, "0.01"), (0.010001, "0.02"), (1.5, "1.50")],
)
def test_usage_hours_round_up_to_centihours(hours, shown):
    assert hpc_usage._hours(hours) == shown


def test_project_usage_shows_the_lifetime_budget_line(sites, capsys):
    assert run(["usage", "--project", "proj-1", "--site", "bdrt"]) == 0
    out = capsys.readouterr().out
    assert (
        "budget: 100.00 GPU-hours over the project's lifetime, all sites; used 40.00, "
        "reserved 2.00, remaining 58.00"
    ) in out
    assert "bob" in out and "rows:" in out
    assert sites.calls == [("bdrt", "project", ("proj-1",), {"since": None})]


def test_an_uncapped_project_says_so():
    assert "uncapped" in hpc_usage.budget_line({"max_gpu_hours": 0, "remaining_gpu_hours": None})


def test_project_usage_json_is_the_server_answer(sites, capsys):
    assert run(["usage", "--project", "proj-1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == sites.project


def test_server_text_is_stripped_of_control_characters():
    line = hpc_usage.row_line(_row(state="DONE\x1b[2J", step="a\nb"))
    assert "\x1b" not in line and "\n" not in line


# ── r4 hpc grants ────────────────────────────────────────────────────────────


def _view(**over: Any) -> dict[str, Any]:
    view = {
        "venue": "bdrt",
        "grant_id": GRANT,
        "status": "active",
        "ended_reason": "",
        "gate_cancel": "",
        "workflows": [{"name": "wf", "version": "1.0"}],
        "inputs_mode": "chained",
        "caps": {"max_jobs": 10, "max_gpu_hours": 5, "max_core_hours": 0, "max_concurrent": 2},
        "expires_at": "2026-12-01T00:00:00Z",
        "usage": {
            "jobs": 3,
            "open": 1,
            "gpu_seconds": 3600,
            "core_seconds": 7200,
            "reserved_gpu_seconds": 1800,
            "reserved_core_seconds": 0,
        },
        "covers_now": True,
        "stale": [],
    }
    view.update(over)
    return view


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(0, "0.00"), (1, "0.01"), (36, "0.01"), (37, "0.02"), (3600, "1.00"), (3601, "1.01")],
)
def test_grant_usage_rounds_integer_seconds_up_to_centihours(seconds, shown):
    from r4 import hpc_grants

    assert hpc_grants._hours(seconds) == shown


def test_grants_shows_caps_against_usage(sites, capsys):
    sites.grants["bdrt"] = {"grants": [_view()]}
    assert run(["grants"]) == 0
    out = capsys.readouterr().out
    needed = [
        f"{GRANT}  active  site bdrt",
        "workflows wf@1.0   data chained",
        "jobs 3/10   GPU-h 1.00 used + 0.50 reserved / 5   core-h 2.00 / no cap",
    ]
    assert [item for item in needed if item not in out] == []
    assert [c[3] for c in sites.calls] == [{"refresh": True}, {"refresh": True}]


def test_grants_flags_stale_grants_and_a_pending_gate_cancel(sites, capsys):
    stale = ["image_unknown: bbbbbbbbbbbb", "workflow_mismatch: wf@1.0"]
    sites.grants["bdrt"] = {
        "grants": [
            _view(covers_now=False, stale=stale),
            _view(status="cancelled", ended_reason="cancelled", gate_cancel="pending"),
        ]
    }
    assert run(["grants", "--site", "bdrt"]) == 0
    out = capsys.readouterr().out
    assert "covers nothing now (image_unknown: bbbbbbbbbbbb; workflow_mismatch: wf@1.0)" in out
    assert "sign a new grant with `r4 hpc grant`" in out
    assert "your gate has not recorded the cancel" in out
    assert f"r4 hpc grant-cancel {GRANT}" in out and "cancelled (cancelled)" in out


def test_grants_json(sites, capsys):
    sites.grants["bdrt"] = {"grants": [_view()]}
    assert run(["grants", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sites"]["bdrt"]["grants"][0]["grant_id"] == GRANT and data["errors"] == {}


def test_grants_skips_a_site_without_personal_accounts_quietly(sites, capsys):
    sites.fail["rccs"] = APIError(
        "undeclared", status_code=409, error_code="hpcaccess.mode_undeclared"
    )
    assert run(["grants"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and "No grants." in captured.out


def test_grants_names_a_site_that_failed(sites, capsys):
    sites.fail["rccs"] = APIError("boom", status_code=500)
    assert run(["grants"]) == 0
    assert "rccs: error: boom" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["grants"], ["usage"]])
def test_read_only_reports_run_in_the_assistants_shell(sites, monkeypatch, argv):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    assert run(argv) == 0


@pytest.mark.parametrize(
    "command", ["r4 hpc grants", "r4 hpc grants --json", "r4 hpc usage --project p"]
)
def test_read_only_reports_are_not_fenced(command):
    from r4.companion.permissions import _shell_command_fence

    assert _shell_command_fence("shell_exec", {"command": command}) is None


def _evidence_log(tmp_path, entries):
    from r4_contracts import hpc_gate

    path = tmp_path / "gate.jsonl"
    log = hpc_gate.GateLog(str(path))
    log.open()
    try:
        for index, (verb, detail) in enumerate(entries):
            log.append(verb, "ok", detail, 1_700_000_000 + index)
    finally:
        log.close()
    return path.read_bytes()


def _grant_submit(job):
    return (
        "submit",
        f"grant={GRANT} run=wf-0123456789ab spec={'e' * 64} step=a wd=w job={job} "
        "gpu_s=100 core_s=200 signer=SHA256:x",
    )


def _grant_settle(job, gpu, core):
    detail = f"grant={GRANT} run=wf-0123456789ab step=a job={job} gpu_s={gpu} core_s={core} bound=0"
    return ("settle", detail)


def test_usage_evidence_flags_ledger_below_the_logged_charge(tmp_path):
    data = _evidence_log(tmp_path, [_grant_submit(42), _grant_settle(42, 100, 200)])
    rows = [{"scheduler_job_id": "42", "gpu_hours": 50 / 3600, "core_hours": 100 / 3600}]
    problems = hpc_usage.usage_evidence_problems(data, rows)
    assert len(problems) == 2
    assert all("below gate-log charge" in problem for problem in problems)


def test_usage_evidence_flags_a_logged_gated_job_missing_from_the_ledger(tmp_path):
    data = _evidence_log(tmp_path, [_grant_submit(42)])
    problems = hpc_usage.usage_evidence_problems(data, [])
    assert problems == ["job 42: logged gated submit is missing from the UMRS ledger"]


def test_usage_witness_reads_the_gate_log_and_fails_closed(sites, tmp_path, monkeypatch, capsys):
    from r4 import hpc_remote

    data = _evidence_log(tmp_path, [_grant_submit(4242), _grant_settle(4242, 5401, 44101)])
    monkeypatch.setattr(hpc_remote, "read_gate_log", lambda host: data)
    assert run(["usage", "--site", "bdrt", "--witness"]) == 1
    assert "below gate-log charge" in capsys.readouterr().err


@pytest.mark.parametrize(("direct", "prefix"), [(False, "/gw"), (True, "/umrs")])
def test_the_usage_routes_match_the_contract(direct, prefix):
    from r4.client import Client
    from r4.config import Config

    client = Client(config=Config(gateway_url="http://gw:8080", api_key="k", project_id="p"))
    client._is_umrs_direct = direct
    client.http.get = MagicMock(return_value={})
    client.hpc_usage(project="p 1", since="2026-11-01")
    client.project_hpc_usage("p 1", since="2026-11-01")
    client.hpc_grants()
    base = f"http://gw:8080{prefix}"
    calls = [(c.args[0], c.kwargs.get("params")) for c in client.http.get.call_args_list]
    assert calls == [
        (f"{base}/sites/me/hpc/usage", {"project": "p 1", "since": "2026-11-01"}),
        (f"{base}/projects/p%201/hpc-usage", {"since": "2026-11-01"}),
        (f"{base}/sites/me/hpc/grants", {"refresh": "1"}),
    ]
