"""``r4 hpc usage``: the usage ledger as the person and the project see it.

Plan §7 (wave D) and the C/D.0 wire contract, item D4. Read only: r4 shows
what UMRS recorded (``GET /umrs/sites/me/hpc/usage`` per site, or
``GET /umrs/projects/{id}/hpc-usage`` with the project's lifetime GPU-hour
budget). r4 never asks a scheduler (CONSTITUTION §1.3). Every value shown
came from a server, so it is cut and stripped of control characters.
"""

from __future__ import annotations

import math
from typing import Any

from .hpc_intents import _grant_settlements, _log_entries, shown

TOTAL_KEYS = ("jobs", "gpu_hours", "core_hours", "reserved_gpu_hours", "reserved_core_hours")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float:
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    number = float(value) if numeric else 0.0
    return number if math.isfinite(number) else 0.0


def _hours(value: Any) -> str:
    """Hours rounded up to centihours: small nonzero usage never shows zero."""
    return f"{math.ceil(_number(value) * 100) / 100:.2f}"


def _row_unit(row: dict[str, Any]) -> str:
    """The run and step for a staged step, else the UMRS job id."""
    if row.get("run_id"):
        return f"{shown(row.get('run_id'), 40)}/{shown(row.get('step'), 40)}"
    return shown(row.get("job_id") or row.get("attempt_id"), 60)


def _authority(row: dict[str, Any]) -> str:
    if row.get("grant_id"):
        return f"grant {shown(row['grant_id'], 32)}"
    if row.get("intent_id"):
        return f"intent {shown(row['intent_id'], 64)[:16]}"
    return shown(row.get("access_mode"), 20)


def _logged_jobs(entries: list[dict[str, Any]]) -> set[str]:
    jobs = set()
    for entry in entries:
        if entry.get("verb") == "submit" and entry.get("result") == "ok":
            detail = dict(_fields(entry.get("detail", "")))
            job = detail.get("job", "")
            if job.isdigit():
                jobs.add(job)
    return jobs


def _fields(detail: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in str(detail).split():
        key, sep, value = token.partition("=")
        if sep:
            fields.setdefault(key, value)
    return fields


def _ledger_seconds(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0
    return round(value * 3600)


def _row_jobs(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row["scheduler_job_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("scheduler_job_id")
    }


def _charge_problems(job: str, row: dict[str, Any], gpu: int, core: int) -> list[str]:
    problems = []
    actual = _ledger_seconds(row, "gpu_hours")
    if actual < gpu:
        problems.append(
            f"job {job}: UMRS ledger reports {actual} GPU-s, below gate-log charge {gpu} GPU-s"
        )
    actual_core = _ledger_seconds(row, "core_hours")
    if actual_core < core:
        problems.append(
            f"job {job}: UMRS ledger reports {actual_core} core-s, "
            f"below gate-log charge {core} core-s"
        )
    return problems


def usage_evidence_problems(data: bytes, rows: list[dict[str, Any]]) -> list[str]:
    """Compare hash-chained gate submissions/charges with the UMRS ledger."""
    entries = _log_entries(data)
    if entries is None:
        return ["the gate log's hash chain is broken"]
    receipts = _grant_settlements(entries)
    charges = {
        receipt.job: (receipt.gpu_s, receipt.core_s)
        for receipt in receipts
        if receipt.job.isdigit()
    }
    jobs = sorted(_logged_jobs(entries) | set(charges), key=int)
    ledger = _row_jobs(rows)
    problems = [
        "settle record with no numeric job id: gate charge cannot be joined to the UMRS ledger"
        for receipt in receipts
        if not receipt.job.isdigit()
    ]
    for job in jobs:
        row = ledger.get(job)
        if row is None:
            problems.append(f"job {job}: logged gated submit is missing from the UMRS ledger")
        elif job in charges:
            problems.extend(_charge_problems(job, row, *charges[job]))
    return problems


def row_line(row: dict[str, Any]) -> str:
    estimate = "  (estimate)" if row.get("source") == "estimate" else ""
    job = row.get("scheduler_job_id") or "-"
    return (
        f"  {shown(row.get('venue'), 32):<10} {shown(row.get('project_id'), 40):<16} "
        f"{_row_unit(row):<36} job {shown(job, 24):<10} {shown(row.get('state'), 16):<13} "
        f"GPU-h {_hours(row.get('gpu_hours'))}  core-h {_hours(row.get('core_hours'))}  "
        f"{_authority(row)}{estimate}"
    )


def totals_line(totals: dict[str, Any]) -> str:
    return (
        f"total: {int(_number(totals.get('jobs')))} jobs, "
        f"GPU-h {_hours(totals.get('gpu_hours'))} used + "
        f"{_hours(totals.get('reserved_gpu_hours'))} reserved, "
        f"core-h {_hours(totals.get('core_hours'))} used + "
        f"{_hours(totals.get('reserved_core_hours'))} reserved"
    )


def merge_totals(answers: dict[str, Any]) -> dict[str, float]:
    """The sum of each site's ``totals``."""
    merged = dict.fromkeys(TOTAL_KEYS, 0.0)
    for answer in answers.values():
        totals = answer.get("totals") if isinstance(answer, dict) else None
        for key in TOTAL_KEYS:
            merged[key] += _number(totals.get(key)) if isinstance(totals, dict) else 0.0
    return merged


def _rows(answer: Any) -> list[dict[str, Any]]:
    rows = answer.get("rows") if isinstance(answer, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def sites_text(answers: dict[str, Any]) -> str:
    """My rows at every site, then the merged totals."""
    lines = [row_line(row) for answer in answers.values() for row in _rows(answer)]
    if not lines:
        lines = ["No HPC usage recorded."]
    return "\n".join(lines + [totals_line(merge_totals(answers))])


def budget_line(budget: Any) -> str:
    """The project's lifetime GPU-hour budget, summed over every site."""
    if not isinstance(budget, dict) or budget.get("remaining_gpu_hours") is None:
        return "budget: no GPU-hour budget for this project (uncapped)"
    return (
        f"budget: {_hours(budget.get('max_gpu_hours'))} GPU-hours over the project's "
        f"lifetime, all sites; used {_hours(budget.get('used_gpu_hours'))}, reserved "
        f"{_hours(budget.get('reserved_gpu_hours'))}, remaining "
        f"{_hours(budget.get('remaining_gpu_hours'))}"
    )


def _member_line(member: dict[str, Any]) -> str:
    return (
        f"  {shown(member.get('person'), 64):<24} {int(_number(member.get('jobs')))} jobs, "
        f"GPU-h {_hours(member.get('gpu_hours'))} + {_hours(member.get('reserved_gpu_hours'))} "
        f"reserved, core-h {_hours(member.get('core_hours'))}"
    )


def project_text(answer: dict[str, Any]) -> str:
    """One project: the budget line, the totals, members (owners), rows."""
    lines = [
        f"project {shown(answer.get('project_id'), 64)}",
        budget_line(answer.get("budget")),
        totals_line(_as_dict(answer.get("totals"))),
    ]
    members = answer.get("members")
    if isinstance(members, list) and members:
        lines += ["members:"] + [_member_line(m) for m in members if isinstance(m, dict)]
    rows = _rows(answer)
    return "\n".join(lines + (["rows:"] + [row_line(r) for r in rows] if rows else []))
