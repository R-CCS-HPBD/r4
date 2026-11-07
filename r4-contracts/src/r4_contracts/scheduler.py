"""Cron expression validation and matching.

Pure-stdlib utilities used by clients (r4 CLI) to validate cron strings
locally before submitting a schedule to UMRS. UMRS remains the owner of
the durable schedule store and execution.

Supports the standard 5-field cron format:
    minute hour day_of_month month day_of_week
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

__all__ = [
    "ScheduleStatus",
    "ScheduleSpec",
    "validate_cron",
    "cron_matches",
]


class ScheduleStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ScheduleSpec:
    """A scheduled job specification (client-visible DTO)."""

    schedule_id: str = ""
    name: str = ""
    cron_expr: str = ""  # 5-field cron: minute hour dom month dow
    job_type: str = ""  # e.g. "connector_sync", "recipe_run", "maintenance"
    job_config: dict[str, Any] = field(default_factory=dict)
    owner_id: str = ""
    project_id: str | None = None
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    created_at: datetime | None = None
    last_run: datetime | None = None
    next_run: datetime | None = None
    run_count: int = 0
    max_runs: int | None = None
    timezone_name: str = "UTC"


# ── Cron parser (minimal, 5-field) ──────────────────────────────────────────

_CRON_FIELD_RANGES = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),  # day of week (0=Sun in classic cron; we accept Python Mon=0 too)
]

_FIELD_NAMES = ["minute", "hour", "day_of_month", "month", "day_of_week"]


def validate_cron(expr: str) -> list[str]:
    """Validate a 5-field cron expression. Returns list of errors (empty=valid)."""
    errors: list[str] = []
    parts = expr.strip().split()
    if len(parts) != 5:
        errors.append(f"Expected 5 fields, got {len(parts)}")
        return errors

    for i, (part, (lo, hi)) in enumerate(zip(parts, _CRON_FIELD_RANGES, strict=True)):
        if part == "*":
            continue
        if part.startswith("*/"):
            try:
                step = int(part[2:])
                if step < 1:
                    errors.append(f"{_FIELD_NAMES[i]}: step must be >= 1")
            except ValueError:
                errors.append(f"{_FIELD_NAMES[i]}: invalid step '{part}'")
            continue
        for token in part.split(","):
            if "-" in token:
                bounds = token.split("-")
                if len(bounds) != 2:
                    errors.append(f"{_FIELD_NAMES[i]}: invalid range '{token}'")
                    continue
                try:
                    a, b = int(bounds[0]), int(bounds[1])
                    if a < lo or b > hi or a > b:
                        errors.append(f"{_FIELD_NAMES[i]}: range {a}-{b} out of bounds ({lo}-{hi})")
                except ValueError:
                    errors.append(f"{_FIELD_NAMES[i]}: non-numeric range '{token}'")
            else:
                try:
                    v = int(token)
                    if v < lo or v > hi:
                        errors.append(f"{_FIELD_NAMES[i]}: value {v} out of bounds ({lo}-{hi})")
                except ValueError:
                    errors.append(f"{_FIELD_NAMES[i]}: non-numeric value '{token}'")
    return errors


def _cron_field_matches(field_expr: str, value: int) -> bool:
    """Check if a value matches a single cron field expression."""
    if field_expr == "*":
        return True
    if field_expr.startswith("*/"):
        step = int(field_expr[2:])
        return value % step == 0
    for token in field_expr.split(","):
        if "-" in token:
            a, b = token.split("-")
            if int(a) <= value <= int(b):
                return True
        elif int(token) == value:
            return True
    return False


def cron_matches(expr: str, dt: datetime) -> bool:
    """Check if a datetime matches a cron expression."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    return (
        _cron_field_matches(minute, dt.minute)
        and _cron_field_matches(hour, dt.hour)
        and _cron_field_matches(dom, dt.day)
        and _cron_field_matches(month, dt.month)
        and _cron_field_matches(dow, dt.weekday())
    )
