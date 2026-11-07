"""`r4 artifact` / `r4 agent-exec` read the A2L ledger's responses.

Since ADR-034's cutover `/gw/a2l/*` reaches UMRS's a2l-ledger router, which
answers ``{"artifacts": [...]}`` / ``{"executions": [...]}``; the printers
read ``items`` and printed "No artifacts found." for a non-empty list. The
`agent-exec ledger` lane called `/umrs/a2l/ledger/jobs` through the gateway,
a path nginx does not serve (2026-10-09).
"""

from __future__ import annotations

from r4._cli.agent_exec import _print_agent_exec_list
from r4._cli.artifact import _print_artifact_list

_ART = {"id": "a" * 64, "type": "report", "status": "completed", "uri": "s3://b/r.md"}


def test_artifact_list_reads_the_ledger_shape(capsys):
    _print_artifact_list({"artifacts": [_ART], "limit": 50, "offset": 0})
    out = capsys.readouterr().out
    assert "s3://b/r.md" in out and "1/1 artifact(s)" in out


def test_artifact_list_still_reads_items(capsys):
    _print_artifact_list({"items": [_ART], "total": 3})
    assert "1/3 artifact(s)" in capsys.readouterr().out


def test_agent_exec_list_reads_the_ledger_shape(capsys):
    _print_agent_exec_list(
        {"executions": [{"id": "e1", "agent_name": "summariser", "status": "running"}]}
    )
    assert "summariser" in capsys.readouterr().out


def test_agent_exec_ledger_goes_through_the_gateway_route():
    import inspect

    from r4._cli import agent_exec

    source = inspect.getsource(agent_exec)
    # H-10.2: the ledger path rides the mode-aware a2l helper (edge
    # /gw/a2l/jobs, direct /umrs/a2l/ledger/jobs) — never hard-coded direct.
    assert '_a2l_url(client, "jobs")' in source and "/umrs/a2l/ledger/jobs" not in source
