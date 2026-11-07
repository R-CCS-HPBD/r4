"""Worker-side results adapter — SAME general plane for all video profiles.

Runs INSIDE the platform worker (aggregate step) or as an offline backfill
(Postgres reachable). No client-side usage — Phase 2l forbids passing DB or
presigned credential capabilities to external callers.

Writes into:
  video_run       — run catalogue (provenance, metrics status)
  video_keypoint  — DLC/SuperAnimal per-frame pose rows
  video_event     — motion / wheel / tag events
  video_embedding — pgvector rows (V-JEPA2, sequence embeddings)
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

DEFAULT_EPS = 64  # rows per flush across sequences
SCHEMA_VERSION = "0001"


def _col_fps(row: dict[str, Any], name: str, default: float = 0.0) -> float:
    v = row.get(name)
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


class ResultsAdapter:
    """Use any psycopg2-ish connection (duck typing: conn.cursor())."""

    def __init__(self, conn: Any, profile: str, rows_per_flush: int = 4096):
        self.conn = conn
        self.profile = profile
        self.rows_per_flush = rows_per_flush
        self._pending_keypoints: list[tuple] = []
        self._pending_events: list[tuple] = []

    # ------------------------------------------------------------------
    # run catalogue
    # ------------------------------------------------------------------
    def start_run(
        self,
        run_id: str,
        workflow: str,
        *,
        video_id: str,
        camera: str | None = None,
        site: str | None = None,
        day: str | None = None,
        params: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO video_run (run_id, profile, workflow, video_id, camera, site, day, params, inputs, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'running')
            ON CONFLICT (run_id) DO NOTHING
            """,
            (
                run_id,
                self.profile,
                workflow,
                video_id,
                camera,
                site,
                day,
                json.dumps(params or {}),
                json.dumps(inputs or {}),
            ),
        )
        self.conn.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        metrics: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.flush()
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE video_run
               SET status=%s, metrics=%s, outputs=%s, error=%s, updated_at=now()
             WHERE run_id=%s
            """,
            (
                status,
                json.dumps(metrics or {}),
                json.dumps(outputs or {}),
                error,
                run_id,
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # keypoints (DLC pose / SuperAnimal)
    # ------------------------------------------------------------------
    def add_keypoint_rows(self, run_id: str, video_id: str, rows: Iterable[dict[str, Any]]) -> None:
        for r in rows:
            self._pending_keypoints.append(
                (
                    run_id,
                    video_id,
                    int(r["frame"]),
                    float(r.get("time_sec", 0.0)),
                    str(r["bodypart"]),
                    float(r.get("x", 0.0)),
                    float(r.get("y", 0.0)),
                    float(r.get("likelihood", 0.0)),
                )
            )
            if len(self._pending_keypoints) >= self.rows_per_flush:
                self.flush()
        self.flush()

    def add_pose_csv(self, run_id: str, video_id: str, csv_path: str | Path) -> int:
        with Path(csv_path).open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.add_keypoint_rows(run_id, video_id, rows)
        return len(rows)

    # ------------------------------------------------------------------
    # events (motion tracking, generic bouts)
    # ------------------------------------------------------------------
    def add_event_row(self, run_id: str, video_id: str, row: dict[str, Any]) -> None:
        self._pending_events.append(
            (
                run_id,
                video_id,
                str(row["event_id"]),
                str(row.get("kind", "motion_bout")),
                float(row["t_start"]),
                float(row["t_end"]),
                json.dumps(row.get("payload", {})),
            )
        )
        if len(self._pending_events) >= self.rows_per_flush:
            self.flush()

    def add_motion_tsv(self, run_id: str, video_id: str, tsv_path: str | Path) -> int:
        rows = 0
        with Path(tsv_path).open(newline="", encoding="utf-8") as f:
            rdr = csv.DictReader(f, delimiter="\t")
            for i, r in enumerate(rdr):
                self.add_event_row(
                    run_id,
                    video_id,
                    {
                        "event_id": str(i),
                        "kind": "motion_bout",
                        "t_start": float(r.get("start", r.get("t_start", 0.0))),
                        "t_end": float(r.get("end", r.get("t_end", 0.0))),
                        "payload": dict(r),
                    },
                )
                rows += 1
        self.flush()
        return rows

    # ------------------------------------------------------------------
    # embeddings (pgvector)
    # ------------------------------------------------------------------
    def add_embedding(
        self,
        run_id: str,
        video_id: str,
        kind: str,
        vec: Sequence[float],
        *,
        t_start: float = 0.0,
        t_end: float = 0.0,
        meta: dict[str, Any] | None = None,
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO video_embedding (run_id, video_id, kind, t_start, t_end, embedding, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, kind, t_start) DO NOTHING
            """,
            (
                run_id,
                video_id,
                kind,
                t_start,
                t_end,
                "[" + ",".join(f"{x:.7g}" for x in vec) + "]",
                json.dumps(meta or {}),
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    def flush(self) -> None:
        if not self._pending_keypoints and not self._pending_events:
            return
        cur = self.conn.cursor()
        if self._pending_keypoints:
            cur.executemany(
                """
                INSERT INTO video_keypoint
                    (run_id, video_id, frame, t_rel, bodypart, x, y, likelihood)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, bodypart, frame) DO NOTHING
                """,
                self._pending_keypoints,
            )
            self._pending_keypoints.clear()
        if self._pending_events:
            cur.executemany(
                """
                INSERT INTO video_event
                    (run_id, video_id, event_id, kind, t_start, t_end, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, event_id) DO NOTHING
                """,
                self._pending_events,
            )
            self._pending_events.clear()
        self.conn.commit()
