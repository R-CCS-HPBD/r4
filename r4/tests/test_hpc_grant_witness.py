"""The gate-log witness for grants and forks (plan §6, C/D.0 item D5; K17, K20).

``r4 hpc status <site> --witness`` reads the log over the person's own SSH
(here: bytes handed in), lists ``submit ok grant=…`` and ``grant-cancel``
entries, flags a grant this r4 did not sign until ``--ack``, counts each
grant's submits over the whole log against the ``max_jobs`` r4 signed,
compares the server's view of the head (a split view is a fork), and
records a fork loudly: r4 then signs no intent, grant or rotate for the site
until ``--clear-fork``. ``--live`` detects a rollback without the log.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from r4_contracts import hpc_gate

from r4 import hpc_grants as hg
from r4 import hpc_intents as hi
from r4 import hpc_remote
from r4 import hpc_signing as hs
from r4._cli import hpc as hpc_cli
from r4.knowledge import COMPANION_SHELL_ENV

SITE = "bdrt"
MINE = "0123456789abcdef0123456789abcdef"
THEIRS = "fedcba9876543210fedcba9876543210"
SPEC = "e" * 64


def _submit(grant: str, step: str, job: int, gpu: int = 3600, core: int = 7200) -> tuple:
    detail = (
        f"grant={grant} run=wf-0123456789ab spec={SPEC} step={step} wd=w job={job} "
        f"gpu_s={gpu} core_s={core} signer=SHA256:x"
    )
    return ("submit", "ok", detail)


def _settle(
    grant: str, run: str, step: str, job: int, gpu: int, core: int, bound: bool = False
) -> tuple:
    detail = (
        f"grant={grant} run={run} step={step} job={job} gpu_s={gpu} core_s={core} "
        f"bound={int(bound)}"
    )
    return ("settle", "ok", detail)


def _log(path: Path, entries: list[tuple], start: int = 0) -> bytes:
    log = hpc_gate.GateLog(str(path))
    log.open()
    try:
        for n, (verb, result, detail) in enumerate(entries):
            log.append(verb, result, detail, 1_700_000_000 + start + n)
    finally:
        log.close()
    return path.read_bytes()


def _signed(grant: str, max_jobs: int = 10) -> None:
    """A local record as ``r4 hpc grant`` writes it."""
    body = {
        "grant_id": grant,
        "site": SITE,
        "expires": 1_900_000_000,
        "caps": {
            "max_jobs": max_jobs,
            "max_gpu_hours": 1,
            "max_core_hours": 0,
            "max_concurrent": 1,
        },
    }
    hg.record_grant(body, b"payload", SITE)


def _head(data: bytes, n: int) -> dict[str, Any]:
    lines = [line for line in data.splitlines() if line]
    entry = hpc_gate.parse_strict(lines[n - 1])
    return {"n": n, "h": entry["h"]}


def _has_cancel_problem(report: Any, text: str) -> bool:
    return any(text in problem for problem in report.cancel_problems)


# ── grant entries ────────────────────────────────────────────────────────────


def test_grant_submits_and_cancels_are_listed_with_their_reservation(tmp_path):
    _signed(MINE)
    data = _log(
        tmp_path / "gate.jsonl",
        [
            _submit(MINE, "prep", 1),
            _submit(MINE, "fit", 2, gpu=60, core=120),
            ("grant-cancel", "ok", f"grant={MINE} signer=SHA256:x"),
        ],
    )
    report = hi.witness_site(SITE, data)
    assert [(s.n, s.grant_id, s.step) for s in report.grant_submits] == [
        (1, MINE, "prep"),
        (2, MINE, "fit"),
    ]
    assert report.grant_cancels == [(3, MINE)]
    assert report.grant_totals[MINE] == hi.GrantTotal(jobs=2, gpu_s=3660, core_s=7320)
    assert report.unknown_grants == [] and report.over_cap == []


def test_a_grant_not_signed_here_is_flagged_until_acknowledged(tmp_path):
    _signed(MINE)
    data = _log(tmp_path / "gate.jsonl", [_submit(MINE, "a", 1), _submit(THEIRS, "a", 2)])
    assert hi.witness_site(SITE, data).unknown_grants == [THEIRS]
    assert hi.witness_site(SITE, data).unknown_grants == [THEIRS]  # kept on every witness
    assert hi.acknowledge_unknown(SITE, THEIRS) is True
    assert hi.acknowledge_unknown(SITE, THEIRS) is False
    assert hi.witness_site(SITE, data).unknown_grants == []


def test_a_grant_over_its_max_jobs_is_flagged_over_the_whole_log(tmp_path):
    """K17: the count is over the whole log, not only since the last head."""
    _signed(MINE, max_jobs=2)
    path = tmp_path / "gate.jsonl"
    first = _log(path, [_submit(MINE, "a", 1), _submit(MINE, "b", 2)])
    assert hi.witness_site(SITE, first).over_cap == []
    more = _log(path, [_submit(MINE, "c", 3)], start=10)
    report = hi.witness_site(SITE, more)
    assert [s.step for s in report.grant_submits] == ["c"]
    assert report.over_cap == [MINE] and report.grant_totals[MINE].jobs == 3


def test_reserved_seconds_are_shown_not_compared_with_hour_caps(tmp_path):
    """K17: settlement may charge less than the reservation."""
    _signed(MINE, max_jobs=5)
    data = _log(tmp_path / "gate.jsonl", [_submit(MINE, "a", 1, gpu=10**7)])
    assert hi.witness_site(SITE, data).over_cap == []


def test_cancel_witness_flags_missing_receipt_and_submits_after_the_request(tmp_path):
    _signed(MINE)
    path = hg.grants_dir() / f"{MINE}.json"
    record = hg.read_grant_record(MINE)
    record["cancel_requested_at"] = 1_700_000_050
    path.write_text(json.dumps(record) + "\n")
    log_path = tmp_path / "gate.jsonl"
    data = _log(
        log_path,
        [
            ("hello", "ok", "before"),
            _submit(MINE, "at-request", 1),
            _submit(MINE, "after-request", 2),
        ],
        start=49,
    )
    report = hi.witness_site(SITE, data)
    assert _has_cancel_problem(report, "no grant-cancel")
    assert _has_cancel_problem(report, "after-request")
    assert not _has_cancel_problem(report, "at-request")

    data = _log(log_path, [("grant-cancel", "ok", f"grant={MINE} signer=x")], start=52)
    report = hi.witness_site(SITE, data)
    assert not _has_cancel_problem(report, "no grant-cancel")
    assert _has_cancel_problem(report, "after-request")


def test_charged_seconds_over_the_signed_hour_cap_are_flagged(tmp_path):
    _signed(MINE, max_jobs=5)
    data = _log(
        tmp_path / "gate.jsonl",
        [_submit(MINE, "a", 1), _settle(MINE, "wf-0123456789ab", "a", 1, 3601, 0)],
    )
    report = hi.witness_site(SITE, data)
    assert report.over_cap == []  # jobs and reservation remain within their earlier checks
    assert report.over_hour_cap == [MINE]
    assert report.grant_totals[MINE].charged_gpu_s == 3601


# ── the CLI ─────────────────────────────────────────────────────────────────


@pytest.fixture
def cli(monkeypatch):
    """``status --witness`` with the log and the server's head handed in."""
    state: dict[str, Any] = {"log": b"", "head": None}
    monkeypatch.setattr(hpc_remote, "read_gate_log", lambda _host: state["log"])
    monkeypatch.setattr(hpc_cli, "_host", lambda _args: "login.example")
    gate = SimpleNamespace(hpc_gate=lambda: {"log_head": state["head"]})
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: gate)
    return state


def run(argv: list[str]) -> int:
    from r4.cli import _build_parser

    return hpc_cli.HpcCommand().handle(_build_parser().parse_args(["hpc", *argv]))


def test_witness_cli_lists_grants_and_exits_1_until_ack(cli, tmp_path, capsys):
    _signed(MINE)
    cli["log"] = _log(tmp_path / "gate.jsonl", [_submit(MINE, "a", 1), _submit(THEIRS, "b", 2)])
    assert run(["status", SITE, "--witness"]) == 1
    captured = capsys.readouterr()
    assert f"submit #1: grant {MINE} run wf-0123456789ab step a" in captured.out
    assert f"grant {MINE}: 1 submits in the log, reserved 3600 GPU-s" in captured.out
    assert f"✗ grant {THEIRS} was not signed by this r4" in captured.err
    assert run(["status", SITE, "--witness", "--ack", THEIRS]) == 0
    assert f"acknowledged: grant {THEIRS}" in capsys.readouterr().out


def test_witness_cli_says_over_cap_on_stderr(cli, tmp_path, capsys):
    _signed(MINE, max_jobs=1)
    cli["log"] = _log(tmp_path / "gate.jsonl", [_submit(MINE, "a", 1), _submit(MINE, "b", 2)])
    assert run(["status", SITE, "--witness"]) == 1
    assert f"✗ OVER CAP: grant {MINE} has 2 submits" in capsys.readouterr().err


def test_witness_cli_names_a_charged_hour_cap_breach(cli, tmp_path, capsys):
    _signed(MINE, max_jobs=5)
    cli["log"] = _log(
        tmp_path / "gate.jsonl",
        [_submit(MINE, "a", 1), _settle(MINE, "wf-0123456789ab", "a", 1, 3601, 0)],
    )
    assert run(["status", SITE, "--witness"]) == 1
    assert f"✗ OVER CHARGED CAP: grant {MINE}" in capsys.readouterr().err


# ── forks: split view, a rewritten log, --live, the signing refusal ─────────


def test_a_split_view_is_a_fork_and_is_recorded(cli, tmp_path, capsys):
    path = tmp_path / "gate.jsonl"
    cli["log"] = _log(path, [("hello", "ok", ""), ("hello", "ok", "")])
    (tmp_path / "other").mkdir()
    other = _log(tmp_path / "other" / "gate.jsonl", [("hello", "ok", "x")])
    cli["head"] = _head(other, 1)  # the server was shown another log
    assert run(["status", SITE, "--witness"]) == 1
    err = capsys.readouterr().err
    assert "✗ FORK at bdrt: split view" in err and "--clear-fork" in err
    record = hs.read_site_record(SITE)
    assert record["witness_fork"]["got"] == cli["head"] and "witness" not in record


def test_a_server_head_on_the_log_is_no_fork(cli, tmp_path, capsys):
    cli["log"] = _log(tmp_path / "gate.jsonl", [("hello", "ok", ""), ("probe", "refused", "")])
    cli["head"] = _head(cli["log"], 1)  # an earlier point of the same log
    assert run(["status", SITE, "--witness"]) == 0
    assert hs.recorded_fork(SITE) is None


def test_a_server_head_beyond_the_log_is_a_split_view(tmp_path):
    data = _log(tmp_path / "gate.jsonl", [("hello", "ok", "")])
    entries = [hpc_gate.parse_strict(line) for line in data.splitlines()]
    assert hi.split_view(entries, {"n": 2, "h": "0" * 64}).startswith("split view")
    assert hi.split_view(entries, {"n": 0, "h": hpc_gate.GENESIS}) == ""


def _rewrite(cli: dict[str, Any], tmp_path: Path) -> None:
    """Witness a two-entry log, then serve another (a rewritten) log."""
    cli["log"] = _log(tmp_path / "gate.jsonl", [("hello", "ok", ""), ("hello", "ok", "")])
    assert run(["status", SITE, "--witness"]) == 0
    (tmp_path / "other").mkdir()
    cli["log"] = _log(tmp_path / "other" / "gate.jsonl", [("hello", "ok", "rewritten")])


def test_a_rewritten_log_records_the_fork_loudly(cli, tmp_path, capsys):
    _rewrite(cli, tmp_path)
    assert run(["status", SITE, "--witness"]) == 1
    assert "✗ FORK at bdrt: the log does not extend" in capsys.readouterr().err
    fork = hs.recorded_fork(SITE) or {}
    assert (fork.get("stored", {}).get("n"), hs.read_site_record(SITE)["witness"]["n"]) == (2, 2)
    assert fork.get("got") == _head(cli["log"], 1)


def test_a_rewritten_log_records_the_locally_read_head_not_the_server_head(cli, tmp_path, capsys):
    _rewrite(cli, tmp_path)
    cli["head"] = {"n": 9, "h": "f" * 64}
    assert run(["status", SITE, "--witness"]) == 1
    fork = hs.recorded_fork(SITE) or {}
    assert fork.get("got") == _head(cli["log"], 1)


def test_a_recorded_fork_refuses_signing_an_intent(cli, tmp_path, monkeypatch):
    """X7: r4 signs no intent for a forked site (grants: test_hpc_grants)."""
    _rewrite(cli, tmp_path)
    run(["status", SITE, "--witness"])
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    request = hi.TriggerRequest("wf", None, {}, [], "p", site=SITE)
    with pytest.raises(hs.HpcSigningError, match="FORK"):
        hi.run_gated_trigger(SimpleNamespace(), request, confirm=lambda _p: True)


def test_the_fork_stands_until_clear_fork(cli, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    _rewrite(cli, tmp_path)
    run(["status", SITE, "--witness"])
    capsys.readouterr()
    # the original log again, extended: no new fork, but the recorded one stands
    cli["log"] = _log(tmp_path / "gate.jsonl", [("hello", "ok", "")], start=5)
    assert run(["status", SITE, "--witness"]) == 1
    assert "✗ FORK at bdrt: recorded" in capsys.readouterr().err
    assert run(["status", SITE, "--witness", "--clear-fork"]) == 0
    assert (hs.recorded_fork(SITE), hs.fork_refusal(SITE, "grant")) == (None, None)
    assert hs.read_site_record(SITE)["witness"]["n"] == 3


def test_clear_fork_keeps_the_fork_while_the_split_view_stands(cli, tmp_path, monkeypatch):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: True)
    cli["log"] = _log(tmp_path / "gate.jsonl", [("hello", "ok", "")])
    cli["head"] = {"n": 1, "h": "f" * 64}
    assert run(["status", SITE, "--witness"]) == 1
    assert run(["status", SITE, "--witness", "--clear-fork"]) == 1
    assert hs.recorded_fork(SITE) is not None


def test_a_broken_chain_records_one_fork_warning_and_preserves_the_old_head(cli, tmp_path, capsys):
    data = _log(tmp_path / "gate.jsonl", [("hello", "ok", ""), ("probe", "refused", "")])
    old_head = hi.witness_site(SITE, data).head
    cli["log"] = data.replace(b'"probe"', b'"hello"')
    assert run(["status", SITE, "--witness"]) == 1
    assert hs.read_site_record(SITE)["witness"] == old_head
    fork = hs.recorded_fork(SITE) or {}
    assert fork.get("stored") == old_head and "hash chain is broken" in fork.get("reason", "")
    assert capsys.readouterr().err.count("hash chain is broken") == 1


@pytest.mark.parametrize(("companion", "tty"), [(True, True), (False, False)])
def test_clear_fork_requires_a_person_at_a_terminal(cli, monkeypatch, companion, tty):
    fork = {"seen_at": "t", "reason": "r"}
    hs.update_site_record(SITE, witness_fork=fork)
    if companion:
        monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    else:
        monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)
    monkeypatch.setattr(hs, "_stdin_is_tty", lambda: tty)
    assert run(["status", SITE, "--witness", "--clear-fork"]) == 2
    assert hs.recorded_fork(SITE) == fork


@pytest.mark.parametrize("flag", ["--clear-fork", "--cl"])
def test_clear_fork_is_never_auto_in_the_companion_shell(flag):
    from r4.companion.permissions import _shell_command_fence

    command = f"r4 hpc status {SITE} --witness {flag}"
    assert _shell_command_fence("shell_exec", {"command": command}) == "never_auto"


def test_read_only_witness_is_not_fenced():
    from r4.companion.permissions import _shell_command_fence

    command = f"r4 hpc status {SITE} --witness"
    assert _shell_command_fence("shell_exec", {"command": command}) is None


def test_clear_fork_is_rejected_without_witness(monkeypatch, capsys):
    client = SimpleNamespace(hpc_account=lambda: {"account": None, "links": []})
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _site: client)
    assert run(["status", SITE, "--clear-fork"]) == 2
    assert "--witness" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("live", "forked"),
    [
        ({"n": 1, "h": "a" * 64}, True),  # rolled back
        ({"n": 2, "h": "b" * 64}, True),  # the same length, another head
        ({"n": 3, "h": "c" * 64}, False),  # longer: cannot tell without the log
        (None, False),
    ],
)
def test_live_detects_a_rollback_without_the_log(monkeypatch, capsys, live, forked):
    hs.update_site_record(SITE, witness={"n": 2, "h": "d" * 64})
    client = SimpleNamespace(
        hpc_account=lambda: {"account": None, "links": []},
        hpc_gate=lambda: {"gate_version": "3", "log_head": live, "current": {}},
    )
    monkeypatch.setattr(hpc_cli, "_site_client", lambda _n: client)
    assert run(["status", SITE, "--live"]) == (1 if forked else 0)
    assert (hs.recorded_fork(SITE) is not None) is forked
    assert ("✗ FORK" in capsys.readouterr().err) is forked


@pytest.mark.parametrize(
    ("action", "verb"),
    [
        ("grant-cancel", "allowed"),
        ("revoke", "allowed"),
        ("grant", "refused"),
        ("rotate", "refused"),
    ],
)
def test_the_fork_text_names_what_still_goes_through(action, verb):
    """K20: the controls that take authority away are never blocked."""
    hs.update_site_record(SITE, witness_fork={"seen_at": "t", "reason": "r"})
    text = hs.fork_refusal(SITE, action) or ""
    assert f"{action} is {verb}" in text and f"r4 hpc status {SITE} --witness --clear-fork" in text
