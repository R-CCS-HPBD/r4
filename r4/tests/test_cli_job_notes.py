"""`r4 job describe` shows a knowledge job's extraction notes (2026-11-14).

A thin extraction (7 entities from a 26-page paper) gave no reason anywhere: the
understand job keeps its notes in the attempt's ``metrics["result"]``, which the
CLI never printed.
"""

from __future__ import annotations

import json

from r4._cli import job as job_cli
from r4.jobs import JobAttempt


def _attempt(result) -> JobAttempt:
    metrics = {} if result is None else {"result": result}
    return JobAttempt.from_dict(
        {"attempt_id": "att-1234567890", "attempt_no": 1, "status": "succeeded", "metrics": metrics}
    )


NOTES = {
    "degraded": True,
    "degraded_reason": "semantic extraction unavailable: replied without a JSON graph",
    "extract_notes": ["extract_window_unparsed:10"],
}


def test_the_notes_come_from_the_attempt_result():
    notes = job_cli._attempt_notes(_attempt(json.dumps(NOTES)))
    assert notes == [
        "degraded: semantic extraction unavailable: replied without a JSON graph",
        "extract_window_unparsed:10",
    ]


def test_a_result_without_notes_or_a_bad_one_gives_none():
    assert job_cli._attempt_notes(_attempt(None)) == []
    assert job_cli._attempt_notes(_attempt("not json")) == []
    assert job_cli._attempt_notes(_attempt(json.dumps(["a"]))) == []
    assert job_cli._attempt_notes(_attempt(json.dumps({"extract_notes": ["x"]}))) == ["x"]


def test_describe_prints_and_json_carries_the_notes(capsys):
    attempt = _attempt(json.dumps(NOTES))
    job_cli._print_attempt(attempt, job_cli._attempt_status_icons())
    out = capsys.readouterr().out
    assert "Note: extract_window_unparsed:10" in out
    assert "Note: degraded: semantic extraction unavailable" in out
    assert job_cli._job_attempt_item(attempt)["notes"][1] == "extract_window_unparsed:10"
