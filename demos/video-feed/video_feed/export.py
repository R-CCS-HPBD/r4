"""Results exporter — S3 JSONL rows mirroring the Postgres plane 1:1.

Worker-side counterpart of :mod:`video_feed.results`. Writes JSONL files into
the output bucket (via ctx.upload_bytes) so the platform keeps results in the
artifact plane even before the DB schema is applied. Column names match
``video_*`` tables exactly, so a later consumer replays them verbatim into
Postgres (schema/001_video_feed.sql).

Every writer takes explicit bucket-relative ``prefix`` (usually the run root).
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence


def _rows_to_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8")


def run_row_json(meta: dict[str, Any]) -> bytes:
    """Single video_run row (catalogue entry)."""
    return _rows_to_jsonl([meta])


def keypoint_rows_json(run_id: str, video_id: str, rows: Iterable[dict[str, Any]]) -> bytes:
    """video_keypoint-shaped rows from pose CSV records."""
    out = []
    for r in rows:
        out.append(
            {
                "run_id": run_id,
                "video_id": video_id,
                "frame": int(r["frame"]),
                "t_rel": float(r.get("time_sec", 0.0)),
                "bodypart": str(r["bodypart"]),
                "x": float(r.get("x", 0.0)),
                "y": float(r.get("y", 0.0)),
                "likelihood": float(r.get("likelihood", 0.0)),
            }
        )
    return _rows_to_jsonl(out)


def event_rows_json(run_id: str, video_id: str, rows: Iterable[dict[str, Any]]) -> bytes:
    """video_event-shaped rows (motion / wheel / tag events)."""
    out = []
    for r in rows:
        out.append(
            {
                "run_id": run_id,
                "video_id": video_id,
                "event_id": str(r["event_id"]),
                "kind": str(r.get("kind", "motion_bout")),
                "t_start": float(r["t_start"]),
                "t_end": float(r["t_end"]),
                "payload": r.get("payload", {}),
            }
        )
    return _rows_to_jsonl(out)


def embedding_rows_json(
    run_id: str,
    video_id: str,
    kind: str,
    vecs: Iterable[tuple[float, float, Sequence[float], dict[str, Any]]],
) -> bytes:
    """video_embedding-shaped rows.

    Each tuple: (t_start, t_end, vec, meta).
    """
    out = []
    for t0, t1, v, meta in vecs:
        out.append(
            {
                "run_id": run_id,
                "video_id": video_id,
                "kind": kind,
                "t_start": float(t0),
                "t_end": float(t1),
                "embedding": [float(x) for x in v],
                "meta": meta or {},
            }
        )
    return _rows_to_jsonl(out)
