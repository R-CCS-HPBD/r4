"""Local persistent store for ``r4 schedule``.

UMRS does not yet expose a scheduler REST surface, so schedules are persisted
locally in ``~/.r4/schedules.json``. The store keeps full schedule metadata
(ID, cron, params, run counters, next-run computation) so the CLI can offer
real add/list/pause/resume/remove semantics instead of stub prints.

Migration path: when UMRS lands /umrs/schedules routes, swap
:class:`ScheduleStore` internals for HTTP calls; the CLI surface must not
change.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from r4_contracts.scheduler import ScheduleSpec, ScheduleStatus, cron_matches

logger = logging.getLogger(__name__)

_STORE_ENV = "R4_SCHEDULE_STORE"
_DEFAULT_PATH = Path(os.path.expanduser("~/.r4/schedules.json"))


def _next_run(cron_expr: str, now: datetime, horizon_days: int = 366) -> Optional[datetime]:
    """Compute the next datetime matching ``cron_expr`` after ``now``.

    Minute-resolution scan; cheap enough for interactive CLI usage
    (worst case 366 * 24 * 60 ≈ 527k evaluations of pure-stdlib matching).
    """
    probe = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = now + timedelta(days=horizon_days)
    while probe <= limit:
        if cron_matches(cron_expr, probe):
            return probe
        probe += timedelta(minutes=1)
    return None


@dataclass
class ScheduleRecord:
    """One persisted schedule row."""

    schedule_id: str
    name: str
    cron: str
    job_type: str
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # active | paused
    created_at: str = ""
    next_run: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "name": self.name,
            "cron": self.cron,
            "job_type": self.job_type,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "next_run": self.next_run,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleRecord":
        return cls(
            schedule_id=data["schedule_id"],
            name=data["name"],
            cron=data["cron"],
            job_type=data["job_type"],
            params=data.get("params", {}),
            status=data.get("status", "active"),
            created_at=data.get("created_at", ""),
            next_run=data.get("next_run"),
        )


class ScheduleStore:
    """JSON-file backed schedule store with atomic writes."""

    def __init__(self, path: Optional[Path] = None) -> None:
        env_override = os.environ.get(_STORE_ENV)
        self._path = Path(env_override) if env_override else (path or _DEFAULT_PATH)

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> list[ScheduleRecord]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"schedule store unreadable, starting empty: {exc}")
            return []
        records_raw = raw.get("schedules", []) if isinstance(raw, dict) else []
        return [ScheduleRecord.from_dict(r) for r in records_raw]

    def _save(self, records: list[ScheduleRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "schedules": [r.to_dict() for r in records]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        tmp.replace(self._path)

    def add(
        self,
        name: str,
        cron: str,
        job_type: str,
        params: Dict[str, Any],
        *,
        now: Optional[datetime] = None,
    ) -> ScheduleRecord:
        now = now or datetime.now()
        records = self._load()
        record = ScheduleRecord(
            schedule_id=uuid.uuid4().hex[:8],
            name=name,
            cron=cron,
            job_type=job_type,
            params=params,
            status="active",
            created_at=now.isoformat(timespec="seconds"),
            next_run=(nxt := _next_run(cron, now)) and nxt.isoformat(timespec="seconds"),
        )
        records.append(record)
        self._save(records)
        return record

    def list(self) -> list[ScheduleRecord]:
        return self._load()

    def get(self, schedule_id: str) -> Optional[ScheduleRecord]:
        for record in self._load():
            if record.schedule_id == schedule_id or record.name == schedule_id:
                return record
        return None

    def _mutate(self, schedule_id: str, status: str) -> Optional[ScheduleRecord]:
        records = self._load()
        for record in records:
            if record.schedule_id == schedule_id or record.name == schedule_id:
                record.status = status
                self._save(records)
                return record
        return None

    def pause(self, schedule_id: str) -> Optional[ScheduleRecord]:
        return self._mutate(schedule_id, "paused")

    def resume(self, schedule_id: str) -> Optional[ScheduleRecord]:
        return self._mutate(schedule_id, "active")

    def remove(self, schedule_id: str) -> Optional[ScheduleRecord]:
        records = self._load()
        kept = []
        removed: Optional[ScheduleRecord] = None
        for record in records:
            if record.schedule_id == schedule_id or record.name == schedule_id:
                removed = record
            else:
                kept.append(record)
        if removed is not None:
            self._save(kept)
        return removed


__all__ = [
    "ScheduleRecord",
    "ScheduleStatus",
    "ScheduleSpec",
    "ScheduleStore",
]
